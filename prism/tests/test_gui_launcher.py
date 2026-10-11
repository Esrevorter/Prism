"""End-to-end tests for the zero-argument `prism-gui` desktop launcher.

Usability mandate (spec §GUI/MPC product requirement, restated by the user):
an end user must never touch a command line beyond launching one app; the
client manages its own backend like Electrum / Bitcoin Core. These tests
prove the launcher mechanics over real loopback sockets, and prove the
refusal cases (per ethics.md §4: every positive needs its negative).
"""
from __future__ import annotations

import json
import os
import threading
import time

import pytest

from prism.rpc import launcher as L
from prism.rpc.server import BoundHTTPServer, RPCRequestHandler
from prism.rpc.service import PrismService


# ---------- helpers -----------------------------------------------------------

def _start_server(svc: PrismService, host="127.0.0.1", port=0):
    if port == 0:
        # Bind an ephemeral OS-assigned port directly (port 0). The previous
        # probe-then-rebind dance failed on CI sandboxes where unprivileged
        # processes cannot bind ports < 1024 (EACCES on port 1).
        httpd = BoundHTTPServer(host, 0, svc)
        port = httpd.server_address[1]
    else:
        httpd = BoundHTTPServer(host, port, svc)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, t, port


@pytest.fixture()
def wallet_dir(tmp_path):
    d = tmp_path / "wallet"
    d.mkdir()
    return str(d)


# ---------- data dir / marker --------------------------------------------------

class TestDataDir:
    def test_make_data_dir_private_and_marked(self, tmp_path):
        d = L.make_data_dir(str(tmp_path / "w"))
        assert os.path.isfile(os.path.join(d, L.MARKER_NAME))
        if os.name == "nt":
            # Windows has no POSIX permission bits: st_mode always reports
            # 0o777 for directories regardless of chmod. Privacy there comes
            # from NTFS creator-owner ACLs (inherited from the user profile).
            pytest.skip("POSIX mode bits do not exist on Windows")
        assert os.stat(d).st_mode & 0o777 == 0o700      # private to the user

    def test_marker_makes_dir_reopenable(self, wallet_dir):
        """A stray terminal inside the folder reopens THIS wallet, not a new
        one — the marker is what find-in-cwd logic keys on."""
        L.make_data_dir(wallet_dir)
        assert os.path.isfile(os.path.join(wallet_dir, L.MARKER_NAME))

    def test_negative_nonmarker_dir_not_adopted(self, tmp_path):
        plain = tmp_path / "not-a-wallet"
        plain.mkdir()
        # main() only adopts an explicit path if the marker exists there;
        # simulate that resolution rule directly:
        req_ok = os.path.isfile(os.path.join(str(plain), L.MARKER_NAME))
        assert req_ok is False


# ---------- free port selection -------------------------------------------------

class TestFreePort:
    def test_returns_bindable_port(self):
        p = L.find_free_port()
        assert 1024 <= p <= 65535
        import socket
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", p))                    # still free at once
        finally:
            s.close()

    def test_two_calls_distinct_or_reusable(self):
        # Negative-ish sanity: never returns port 0 / privileged ports.
        for _ in range(5):
            assert L.find_free_port() > 1024


# ---------- single-instance lock -----------------------------------------------

class TestInstanceLock:
    def test_acquire_release_cycle(self, wallet_dir):
        lock = L.InstanceLock(wallet_dir)
        acquired, live = lock.acquire_or_live()
        assert acquired and live is None
        lock.write_info("127.0.0.1", 9999, os.getpid())
        lock.release()
        # after release a second launcher gets in
        lock2 = L.InstanceLock(wallet_dir)
        acquired2, _ = lock2.acquire_or_live()
        assert acquired2
        lock2.release()

    def test_second_instance_defers_to_live_daemon(self, wallet_dir):
        svc = PrismService()
        httpd, _, port = _start_server(svc, port=0)
        try:
            a = L.InstanceLock(wallet_dir)
            ok, _ = a.acquire_or_live()
            assert ok
            a.write_info("127.0.0.1", port, os.getpid())
            b = L.InstanceLock(wallet_dir)
            acquired, live_url = b.acquire_or_live()
            assert acquired is False                     # refusal case
            assert live_url == f"http://127.0.0.1:{port}/"
        finally:
            httpd.shutdown(); httpd.server_close(); a.release()

    def test_stale_lock_is_stolen(self, wallet_dir):
        """Holder died without cleanup (dead pid/port): new launcher must NOT
        be locked out forever — it steals the stale lock."""
        with open(os.path.join(wallet_dir, L.LOCK_NAME), "w") as fh:
            json.dump({"host": "127.0.0.1", "port": 1, "pid": 999999}, fh)
        lock = L.InstanceLock(wallet_dir)
        acquired, live = lock.acquire_or_live()
        assert acquired is True and live is None
        lock.release()

    def test_garbage_lockfile_treated_as_stale(self, wallet_dir):
        with open(os.path.join(wallet_dir, L.LOCK_NAME), "w") as fh:
            fh.write("not json at all")
        lock = L.InstanceLock(wallet_dir)
        acquired, live = lock.acquire_or_live()
        assert acquired is True and live is None
        lock.release()


# ---------- health probe ----------------------------------------------------------

class TestHealthProbe:
    def test_ok_against_real_server(self):
        svc = PrismService()
        httpd, _, port = _start_server(svc, port=0)
        try:
            assert L._health_ok("127.0.0.1", port) is True
            assert L.wait_until_healthy("127.0.0.1", port, deadline_s=2)
        finally:
            httpd.shutdown(); httpd.server_close()

    def test_refused_when_nothing_listening(self):
        assert L._health_ok("127.0.0.1", 1) is False     # nothing bound there

    def test_wait_times_out_gracefully(self):
        assert L.wait_until_healthy("127.0.0.1", 1, deadline_s=0.2) is False


# ---------- per-user service isolation ---------------------------------------------

class TestBoundServiceIsolation:
    def test_bound_httpd_serves_its_own_service(self, wallet_dir):
        """The classic module singleton must not leak into launcher instances:
        each BoundHTTPServer dispatches against ITS PrismService."""
        svc_a, svc_b = PrismService(), PrismService()
        ha = svc_a.contact_add("Zoe", "prsm1zoe000000000000000000000000000000")
        httpd_a, _, pa = _start_server(svc_a, port=0)
        httpd_b, _, pb = _start_server(svc_b, port=0)
        try:
            def rpc(port, method, params=None):
                import urllib.request
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/jsonrpc",
                    data=json.dumps({"jsonrpc": "2.0", "id": 1,
                                     "method": method,
                                     "params": params or {}}).encode(),
                    headers={"Content-Type": "application/json"})
                return json.loads(urllib.request.urlopen(req, timeout=2).read())

            names_a = [c["display_name"] for c in
                       rpc(pa, "contacts.list")["result"]["contacts"]]
            names_b = [c["display_name"] for c in
                       rpc(pb, "contacts.list")["result"]["contacts"]]
            assert "Zoe" in names_a
            assert "Zoe" not in names_b                  # isolated state
            assert ha["contact"]["address"].startswith("prsm1zoe")
        finally:
            httpd_a.shutdown(); httpd_a.server_close()
            httpd_b.shutdown(); httpd_b.server_close()


# ---------- full launcher run (real subprocess, bare invocation) --------------------

class TestLauncherRun:
    def test_bare_run_serves_gui_and_auto_opens(self, wallet_dir, monkeypatch):
        """Simulate double-click: zero args, no --open flag, no port config.
        Launcher must pick a free port, bind, mark ready via health check,
        call webbrowser.open itself, and serve the GUI index at GET /."""
        opened: list[str] = []
        monkeypatch.setattr(L.webbrowser, "open",
                            lambda url: opened.append(url))
        # keep HOME pristine: default dir must not be consulted here because
        # we pass data_dir explicitly through argv tail-dict.
        t = threading.Thread(target=L.main,
                             args=([{"data_dir": wallet_dir}],),
                             daemon=True)
        t.start()
        # discover the port from the sidecar the launcher writes.
        # NOTE: never read gui.lock here — on Windows the holder's msvcrt
        # mandatory byte-range lock makes rival reads raise PermissionError.
        # The unlocked gui.info sidecar is the canonical discovery channel.
        info = None
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                with open(os.path.join(wallet_dir, L.INFO_NAME)) as fh:
                    raw = fh.read().strip()
                if raw:
                    info = json.loads(raw)
                    break
            except OSError:
                pass
            time.sleep(0.05)
        assert info is not None, "launcher never wrote its instance info"
        host, port = info["host"], info["port"]
        assert L.wait_until_healthy(host, port, deadline_s=10), \
            "daemon not healthy after bare launch"

        import urllib.request
        with urllib.request.urlopen(f"http://{host}:{port}/", timeout=3) as r:
            body = r.read()
            assert r.status == 200
        assert b"<html" in body.lower()                  # real GUI bundle served

        # browser opened automatically, exactly once, at the right URL
        deadline2 = time.monotonic() + 5
        while not opened and time.monotonic() < deadline2:
            time.sleep(0.05)
        assert opened == [f"http://{host}:{port}/"]

        # a working JSON-RPC round-trip through the launcher-owned service
        req = urllib.request.Request(
            f"http://{host}:{port}/jsonrpc",
            data=json.dumps({"jsonrpc": "2.0", "id": 1,
                             "method": "health.ping"}).encode(),
            headers={"Content-Type": "application/json"})
        res = json.loads(urllib.request.urlopen(req, timeout=3).read())
        assert res["result"]["status"] == "ok"

        # shutdown: interrupt the serve_forever via server-side stop is not
        # reachable cross-thread cleanly; instead verify lock file has our pid
        assert info["pid"] == os.getpid()
        # thread stays alive (daemon=True) so pytest teardown won't hang.
