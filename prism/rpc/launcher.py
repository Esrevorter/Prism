"""Zero-argument desktop launcher for the Prism GUI wallet.

End-user promise (spec §"GUI/MPC product requirement", user-stressed
usability mandate): double-click / run one bare command -> everything else
happens automatically, exactly like Electrum or Bitcoin Core manage their own
backend:

  1. pick a free loopback port (no "address already in use" errors ever);
  2. create a private per-user data dir under ~/.prism/ (0700) and mark it
     with a .prism_wallet marker file so a stray terminal opened in that
     folder reopens THIS wallet, not a fresh one;
  3. single-instance guard: if another launcher holds the lock AND is healthy,
     just open its UI in the browser instead of starting a second daemon;
  4. start the JSON-RPC + GUI daemon in-process (same trusted code path as
     `prism-rpc`; no subprocesses to orphan on quit);
  5. open the wallet UI in the system default browser once the socket is
     confirmed listening (no race, no blank page).

Everything here is stdlib-only, matching the project hard constraint.
"""
from __future__ import annotations

import errno
import http.client
import json
import os
import socket
import sys
import time
import webbrowser

if os.name == "nt":                       # Windows: msvcrt locking (see below)
    import msvcrt
else:                                     # POSIX: flock
    import fcntl

DEFAULT_ROOT = os.path.join(os.path.expanduser("~"), ".prism")
MARKER_NAME = ".prism_wallet"
LOCK_NAME = "gui.lock"


def _die(msg: str, code: int = 1) -> None:
    print(f"\n  [Prism] {msg}\n", file=sys.stderr)
    raise SystemExit(code)


def make_data_dir(path: str | None = None) -> str:
    """Create/adopt the per-user wallet data dir (private mode 0700)."""
    d = os.path.abspath(path or os.path.join(DEFAULT_ROOT, "wallet"))
    try:
        os.makedirs(d, mode=0o700, exist_ok=True)
    except OSError as e:                      # exists with wrong perms etc.
        if e.errno != errno.EEXIST:
            raise
    if os.name != "nt":
        try:                                  # tighten even if dir pre-existed
            os.chmod(d, 0o700)
        except OSError:                       # pragma: no cover
            pass
    # Windows has no POSIX permission bits; NTFS privacy comes from the
    # default creator-owner ACL. Storing our own copy of the lock file in
    # memory also lets us avoid re-reading it while another process holds
    # an exclusive byte-range lock on it (msvcrt raises OSError on read).
    with open(os.path.join(d, MARKER_NAME), "w") as fh:
        fh.write("prism-gui\n")               # marker: this dir owns a wallet
    return d


def find_free_port(host: str = "127.0.0.1") -> int:
    s = socket.socket()
    try:
        s.bind((host, 0))
        return s.getsockname()[1]
    finally:
        s.close()


class InstanceLock:
    """Advisory single-instance lock inside the data dir.

    POSIX: flock on the lockfile (advisory, released automatically if the
    holder dies). Windows: msvcrt.locking() mandatory byte-range lock —
    fcntl does not exist there, and importing it unconditionally made the
    whole launcher module unimportable on Windows (CI Release job,
    ModuleNotFoundError: No module named 'fcntl'). The Windows lock is also
    released by the OS when the holding process exits.
    """

    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, LOCK_NAME)
        self.fh = None
        self.info: dict = {}     # in-memory mirror of the lockfile payload

    @staticmethod
    def _read_info(path: str) -> dict:
        """Best-effort read of a (possibly locked) lockfile.

        On Windows, msvcrt byte-range locks make reads by other processes
        raise OSError — so we try/except and fall back to {}. On POSIX,
        flock is advisory and the read succeeds."""
        try:
            with open(path) as fh:
                return json.loads(fh.read() or "{}")
        except (OSError, ValueError):
            return {}

    def _try_lock(self, fh):
        """Non-blocking exclusive lock; True if acquired.

        Windows detail: msvcrt.locking(LK_NBLCK, n) locks *n bytes* only.
        A one-byte range is defeated when a second process opens the same
        file for writing — buffered writes outside byte 0 succeed silently
        (observed in CI: two launchers both "acquired"). We therefore lock
        a generous fixed span covering any realistic JSON payload.
        """
        try:
            if os.name == "nt":
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, self.LOCK_SPAN)
            else:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    LOCK_SPAN = 4096             # bytes covered by the Windows byte-range lock

    def _unlock(self, fh):
        if os.name == "nt":
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, self.LOCK_SPAN)
        else:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    def acquire_or_live(self):
        """Return (acquired, live_url|None). If not acquired, probe the
        holder's health endpoint so we can reopen its UI instead of failing.

        Cross-platform ordering matters:
        1. Try to take the lock FIRST. On Windows msvcrt byte-range locks
           are mandatory — reading while a holder has the range locked
           raises PermissionError — so reads happen only on handles we
           hold ourselves, or best-effort after a failed acquisition.
        2. If we acquired but the payload still describes a HEALTHY daemon,
           the previous holder died without releasing cleanly yet is
           somehow reachable: hand back its URL and release our fresh
           lock (caller reopens that UI instead of starting a duplicate).
        3. If acquisition fails outright, best-effort-read + probe; then
           poll briefly for either outcome (holder releases -> stale/clean
           steal; holder's daemon comes up -> live URL)."""
        fh = open(self.path, "a+")
        if self._try_lock(fh):
            # We hold it now: safe to inspect any stale payload.
            try:
                fh.seek(0)
                stale = json.loads(fh.read() or "{}")
            except ValueError:
                stale = {}
            host, port = stale.get("host", "127.0.0.1"), stale.get("port")
            if port and _health_ok(host, int(port)):
                self.info = stale
                self._unlock(fh)
                fh.close()
                return False, f"http://{host}:{int(port)}/"
            fh.seek(0)
            fh.truncate()
            self.fh = fh
            return True, None
        # Lock is held by someone else right now.
        info = self.info or self._read_info(self.path)
        host, port = info.get("host", "127.0.0.1"), info.get("port")
        if port and _health_ok(host, int(port)):
            self.info = info
            fh.close()
            return False, f"http://{host}:{int(port)}/"
        # Holder present but not answering (booting) or payload unreadable
        # (Windows + mandatory lock during contention). Poll briefly.
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            fh.close()
            time.sleep(0.1)
            try:
                fh = open(self.path, "a+")
            except OSError:                   # pragma: no cover
                continue
            if self._try_lock(fh):            # holder released -> steal/clean
                fh.seek(0)
                fh.truncate()
                self.fh = fh
                return True, None
            info = self._read_info(self.path)
            host, port = info.get("host", "127.0.0.1"), info.get("port")
            if port and _health_ok(host, int(port)):
                self.info = info
                fh.close()
                return False, f"http://{host}:{int(port)}/"
        fh.close()
        return False, None                    # still contended; caller retries

    def write_info(self, host: str, port: int, pid: int) -> None:
        assert self.fh is not None
        self.info = {"host": host, "port": port, "pid": pid}
        self.fh.seek(0)
        self.fh.truncate()
        json.dump(self.info, self.fh)
        self.fh.flush()

    def release(self) -> None:
        if self.fh is not None:
            try:
                self._unlock(self.fh)
                self.fh.close()
                os.unlink(self.fh.name)
            except OSError:
                pass
            self.fh = None


def _health_ok(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
        conn.request("GET", "/health")
        resp = conn.getresponse()
        ok = resp.status == 200 and b"ok" in resp.read()
        conn.close()
        return ok
    except OSError:
        return False


def wait_until_healthy(host: str, port: int, deadline_s: float = 10.0) -> bool:
    end = time.monotonic() + deadline_s
    while time.monotonic() < end:
        if _health_ok(host, port):
            return True
        time.sleep(0.05)
    return False


def main(argv=None) -> int:
    """Bare-command entry point: `prism-gui` with zero required arguments.

    Optional hidden conveniences (end users never need them):
      prism-gui [path-to-wallet-folder]   reopen a specific wallet dir
      prism-gui --no-browser              never open a browser window
                                          (used by the Tauri desktop shell,
                                          which renders the UI in its own
                                          webview instead of a browser tab)
      prism-gui --json-status             print one JSON line describing the
                                          live daemon {url, data_dir} right
                                          before serving, so a supervising
                                          parent process can discover the
                                          randomly-chosen loopback port
    A folder containing the .prism_wallet marker is adopted as-is; anything
    else falls back to the default per-user location.  Tests may pass
    data_dir=/tmp-dir explicitly to avoid touching ~/.prism.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    kwargs = {}
    if isinstance(args, list) and args and isinstance(args[-1], dict):
        kwargs = args.pop()
    # Flag scan: only leading dashes are flags; first positional is the folder.
    no_browser = "--no-browser" in args
    json_status = "--json-status" in args
    args = [a for a in args if not str(a).startswith("--")]
    req = args[0] if args else None
    data_dir = kwargs.get("data_dir")
    if data_dir is not None:
        # explicit dir (tests / future folder-picker): ensure it exists + marked
        data_dir = make_data_dir(data_dir)
    else:
        if req:
            cand = os.path.abspath(req)
            if os.path.isfile(os.path.join(cand, MARKER_NAME)):
                data_dir = cand
    if data_dir is None and os.path.isfile(os.path.join(os.getcwd(), MARKER_NAME)):
        data_dir = os.getcwd()                # terminal opened inside a wallet dir
    if data_dir is None:
        data_dir = make_data_dir()

    lock = InstanceLock(data_dir)
    acquired, live_url = None, None
    deadline = time.monotonic() + 15.0
    while True:
        acquired, live_url = lock.acquire_or_live()
        if acquired or live_url:
            break
        # Contended but nothing healthy yet (peer still booting): retry.
        if time.monotonic() >= deadline:
            _die("Another Prism launcher holds the wallet lock but no "
                 "healthy daemon was found. Close it and try again.")
        time.sleep(0.2)
    if not acquired:
        print(f"Prism wallet is already running — opening it at {live_url}")
        if json_status:
            print(json.dumps({"url": live_url, "data_dir": data_dir,
                              "already_running": True}), flush=True)
        if not no_browser:
            webbrowser.open(live_url)
        return 0

    from .server import serve_with_callback       # late import: avoids cycles

    host, port = "127.0.0.1", find_free_port(host="127.0.0.1")
    lock.write_info(host, port, os.getpid())
    url = f"http://{host}:{port}/"
    print("Starting your Prism wallet...")

    from .service import PrismService           # fresh per-user instance

    def _open_when_ready():
        if wait_until_healthy(host, port):
            print(f"Wallet ready: {url}  (data: {data_dir})")
            if json_status:
                # Machine-readable line for the supervising desktop shell
                # (packaging/tauri). Emitted only after /health confirms the
                # daemon is really serving — never a race.
                print(json.dumps({"url": url, "data_dir": data_dir,
                                  "already_running": False}), flush=True)
            if not no_browser:
                print("Keep this window open while you use the wallet; "
                      "close it (or press Ctrl-C) to stop.")
                webbrowser.open(url)
        else:                                   # pragma: no cover
            print(f"Daemon started but health check timed out; try {url}")

    try:
        serve_with_callback(host=host, port=port, on_ready=_open_when_ready,
                            service=PrismService())
    except KeyboardInterrupt:
        pass
    finally:
        lock.release()
    print("Prism wallet stopped. Your data stays in:", data_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
