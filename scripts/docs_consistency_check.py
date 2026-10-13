#!/usr/bin/env python3
"""Drift guard: AGENTS.md precedence list must match docs/internal/README.md chain."""
import re, sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
agents = (root / "AGENTS.md").read_text()
readme = (root / "docs" / "internal" / "README.md").read_text()

# canonical ordered items both files must list identically
canonical = ["spec.md", "rules.md", "ethics.md", "qwen.md"]
def order(text):
    # find first-appearance index of each governance file within the precedence block
    m = re.search(r"(?:precedence|chain)[\s\S]{0,1200}", text, re.I)
    block = m.group(0) if m else text
    pos = []
    for f in canonical:
        i = block.find(f)
        if i < 0:
            print(f"FAIL: {f} missing from precedence block"); return None
        pos.append((i, f))
    return [f for _, f in sorted(pos)]

a, r = order(agents), order(readme)
if a is None or r is None:
    sys.exit(1)
# README lists spec first then integrity (rules+ethics) then rules-limits then qwen;
# compare set-order tolerant of ethics appearing twice (integrity contract + own item)
def norm(seq):
    out=[]
    for x in seq:
        if x not in out: out.append(x)
    return out
if norm(a) != norm(r):
    print(f"DRIFT: AGENTS.md order {norm(a)} != README order {norm(r)}"); sys.exit(1)
print("OK: load-order/precedence consistent between AGENTS.md and docs/internal/README.md")
