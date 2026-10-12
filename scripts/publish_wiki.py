#!/usr/bin/env python3
"""Publish the in-repo wiki/ folder to the GitHub Wiki.

Why this exists (verified 2026-10-12):
  GitHub creates the `Prism.wiki.git` namespace lazily - it does not exist
  until the FIRST page is created through the *web UI*. Until then every
  git push and API call to the wiki returns "Repository not found" (404),
  regardless of token permissions. There is no REST endpoint for creating
  wiki pages, so one manual seed click is unavoidable.

Usage:
  1. (once) Open https://github.com/Esrevorter/Prism/wiki
     -> "Create the first page" -> title "Home", body anything -> save.
  2. GITHUB_TOKEN=<fine-grained PAT with Contents: R&W on Prism> \
       python3 scripts/publish_wiki.py
     The script clones the (now existing) wiki repo, copies every wiki/*.md
     from this repo over it, and pushes. Idempotent; safe to re-run after
     each docs change. Page names = file names.
"""
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.environ.get("PRISM_WIKI_REPO", "https://github.com/Esrevorter/Prism.wiki.git")
SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "wiki")

import re as _re


def transform(text):
    """Rewrite repo-style links so they work on GitHub's hosted wiki.

    GitHub renders wiki pages itself, but relative links that were written for
    the in-repo `wiki/` folder do not resolve there:
      * `[text](./Page.md)`  -> `[text](Page)`   (wiki-internal link)
      * `[text](../LICENSE)` -> absolute repo URL (wiki sits in its own repo)
    The source of truth stays plain Markdown that renders perfectly in the repo.
    """
    # internal page links: ./Name.md (optionally with #anchor) -> Name(#anchor)
    text = _re.sub(r"\]\(\./([A-Za-z0-9&;_\-\.]+?)\.md(#[^)]*)?\)",
                   lambda m: f"]({m.group(1)}{m.group(2) or ''})", text)
    # parent-relative repo links -> absolute URLs to the code repo
    text = _re.sub(r"\]\(\.\./([^)#]+)(#[^)]*)?\)",
                   lambda m: f"](https://github.com/Esrevorter/Prism/blob/main/{m.group(1)}{m.group(2) or ''})",
                   text)
    return text


def _auth(url):
    tok = os.environ.get("GITHUB_TOKEN", "")
    if tok and url.startswith("https://github.com"):
        return url.replace("https://github.com", f"https://x-access-token:{tok}@github.com")
    return url


def run(*args, cwd=None):
    cmd = [_auth(a) if isinstance(a, str) else a for a in args]
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=env)
    if r.returncode != 0:
        raise RuntimeError(f"FAILED: {' '.join(args)}\n{r.stderr}")
    return r.stdout


def main():
    if not os.environ.get("GITHUB_TOKEN"):
        sys.exit("Set GITHUB_TOKEN to a fine-grained PAT (Contents: Read & write on Prism).")
    try:
        run("git", "ls-remote", REPO, "HEAD")
    except RuntimeError:
        sys.exit(
            "Wiki namespace does not exist yet.\n"
            "Seed it once via the web UI: open the repo's /wiki page,\n"
            "click 'Create the first page', save any page, then re-run this script.")
    tmp = tempfile.mkdtemp(prefix="prism-wiki-")
    try:
        run("git", "clone", REPO, tmp)
        n = 0
        for name in sorted(os.listdir(SRC)):
            if name.endswith(".md"):
                with open(os.path.join(SRC, name), encoding="utf-8") as fh:
                    body = fh.read()
                with open(os.path.join(tmp, name), "w", encoding="utf-8") as fh:
                    fh.write(transform(body))
                n += 1
        run("git", "-C", tmp, "add", "-A")
        if run("git", "-C", tmp, "status", "--porcelain").strip():
            run("git", "-C", tmp, "-c", "user.email=agent@prism.local",
                "-c", "user.name=Prism Agent", "commit", "-m",
                f"Sync {n} pages from repo wiki/ folder")
            run("git", "-C", tmp, "push", "origin", "HEAD")
        print(f"Published {n} wiki pages.")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
