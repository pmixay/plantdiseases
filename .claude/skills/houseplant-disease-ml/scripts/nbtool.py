#!/usr/bin/env python3
"""
nbtool — inspect and navigate the PlantVision training notebook (or any .ipynb).

The training notebook (`server/train_notebook.ipynb`) is large: a handful of
code cells run into the thousands of lines. Reading it whole burns context and
buries the part you actually need. This tool lets you map it, pull one section,
or grep a config knob across every cell — cheaply, with stdlib only.

Sections are detected from the notebook's own `§N` headers (markdown `## §2 …`
and code `# §2 …`), so this stays correct as the notebook evolves and is not
hard-coded to a particular cell count.

Usage
-----
    # Map the notebook: one line per cell (type, size, header).
    python nbtool.py list [NOTEBOOK]

    # Dump one section by number (markdown intro + code), e.g. §4 training loop.
    python nbtool.py section 4 [NOTEBOOK]

    # Grep a knob/identifier across all code cells (case-insensitive regex).
    python nbtool.py grep "LORA_|LABEL_SMOOTH|FAIL_DRIFT" [NOTEBOOK]

    # Scaffold a fresh, repo-consistent analysis notebook next to the training one.
    python nbtool.py scaffold OUT.ipynb

NOTEBOOK defaults to ./train_notebook.ipynb, then ./server/train_notebook.ipynb.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

SECTION_RE = re.compile(r"^\s*#+\s*§\s*(\d+)\b", re.MULTILINE)


def _find_notebook(arg: str | None) -> Path:
    if arg:
        p = Path(arg)
        if not p.exists():
            sys.exit(f"notebook not found: {p}")
        return p
    for cand in ("train_notebook.ipynb", "server/train_notebook.ipynb"):
        if Path(cand).exists():
            return Path(cand)
    sys.exit("no notebook given and none found at ./ or ./server/")


def _load(path: Path) -> dict:
    return json.loads(path.read_text())


def _src(cell: dict) -> str:
    s = cell.get("source", "")
    return "".join(s) if isinstance(s, list) else s


def _section_of(cell: dict) -> int | None:
    """Return the §N this cell belongs to, if its source opens with a §N header."""
    head = "\n".join(_src(cell).strip().splitlines()[:3])
    m = SECTION_RE.search(head)
    return int(m.group(1)) if m else None


def _first_header(cell: dict) -> str:
    for ln in _src(cell).splitlines():
        ln = ln.strip()
        if ln:
            return ln[:88]
    return "(empty)"


def cmd_list(nb: dict) -> None:
    print(f"{len(nb['cells'])} cells\n")
    cur = None
    for i, c in enumerate(nb["cells"]):
        sec = _section_of(c)
        if sec is not None and sec != cur:
            cur = sec
        src = _src(c)
        nlines = src.count("\n") + 1 if src else 0
        tag = f"§{cur}" if cur is not None else "  "
        print(f"[{i:2}] {tag:>3} {c['cell_type']:8} {nlines:5}L  {_first_header(c)}")


def cmd_section(nb: dict, n: int) -> None:
    cells = nb["cells"]
    start = None
    for i, c in enumerate(cells):
        if _section_of(c) == n:
            start = i
            break
    if start is None:
        sys.exit(f"§{n} not found. Run `list` to see available sections.")
    # Run to the next cell that opens a DIFFERENT section header.
    end = len(cells)
    for j in range(start + 1, len(cells)):
        s = _section_of(cells[j])
        if s is not None and s != n:
            end = j
            break
    for j in range(start, end):
        c = cells[j]
        print(f"\n{'='*72}\n[cell {j}] {c['cell_type']}\n{'='*72}")
        print(_src(c))


def cmd_grep(nb: dict, pattern: str) -> None:
    rx = re.compile(pattern, re.IGNORECASE)
    hits = 0
    for i, c in enumerate(nb["cells"]):
        if c["cell_type"] != "code":
            continue
        sec = None
        for k in range(i, -1, -1):
            sec = _section_of(nb["cells"][k])
            if sec is not None:
                break
        for lineno, ln in enumerate(_src(c).splitlines(), 1):
            if rx.search(ln):
                tag = f"§{sec}" if sec is not None else "  "
                print(f"{tag} cell{i:>2}:{lineno:<4} {ln.strip()[:108]}")
                hits += 1
    if not hits:
        print(f"no matches for /{pattern}/")


SCAFFOLD_CELLS = [
    ("markdown", "# Analysis — PlantVision\n\n"
                 "Repo-consistent scratch notebook. Mirrors `train_notebook.ipynb` §1 "
                 "conventions (SEED, taxonomy, disease-macro-F1) so any number you "
                 "produce here is comparable to the ship gate.\n"),
    ("code",
     "# Canonical boilerplate — keep in sync with train_notebook.ipynb §1\n"
     "import json, random\n"
     "import numpy as np\n"
     "from sklearn.metrics import f1_score, recall_score, confusion_matrix\n\n"
     "SEED = 0\n"
     "random.seed(SEED); np.random.seed(SEED)\n\n"
     "# 9 trainable classes (alphabetical == sorted(ImageFolder.classes)).\n"
     "CLASS_NAMES = [\n"
     "    'gray_mold_botrytis', 'healthy', 'leaf_scorch_underwatering',\n"
     "    'leaf_spot', 'not_a_plant', 'nutrient_deficiency',\n"
     "    'pests', 'powdery_mildew', 'viral_disease',\n"
     "]\n"
     "NON_DISEASE = {'healthy', 'not_a_plant'}\n"
     "DISEASE_IDX = [i for i, c in enumerate(CLASS_NAMES) if c not in NON_DISEASE]\n\n"
     "def disease_macro_f1(y_true, y_pred):\n"
     "    \"\"\"Macro-F1 over DISEASE classes only — the ship-gate metric.\n"
     "    Matches tools/eval_bootstrap.py:disease_macro_f1.\"\"\"\n"
     "    return float(f1_score(y_true, y_pred, labels=DISEASE_IDX,\n"
     "                          average='macro', zero_division=0))\n"),
    ("code",
     "# Load predictions however your eval produced them, then:\n"
     "# print('disease macro-F1:', disease_macro_f1(y_true, y_pred))\n"
     "# For CIs / a paired A/B, prefer the repo tool:\n"
     "#   from tools.eval_bootstrap import paired_bootstrap, bootstrap_macro_f1_ci\n"),
]


def cmd_scaffold(out: str) -> None:
    p = Path(out)
    if p.exists():
        sys.exit(f"refusing to overwrite existing file: {p}")
    cells = []
    for ctype, text in SCAFFOLD_CELLS:
        cell = {"cell_type": ctype, "metadata": {}, "source": text.splitlines(keepends=True)}
        if ctype == "code":
            cell["outputs"] = []
            cell["execution_count"] = None
        cells.append(cell)
    nb = {
        "cells": cells,
        "metadata": {"kernelspec": {"name": "python3", "display_name": "Python 3"}},
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    p.write_text(json.dumps(nb, indent=1))
    print(f"wrote {p}  ({len(cells)} cells)")


def main(argv: list[str]) -> None:
    if not argv:
        print(__doc__)
        return
    cmd = argv[0]
    if cmd == "scaffold":
        if len(argv) < 2:
            sys.exit("usage: nbtool.py scaffold OUT.ipynb")
        cmd_scaffold(argv[1])
        return
    if cmd == "list":
        cmd_list(_load(_find_notebook(argv[1] if len(argv) > 1 else None)))
    elif cmd == "section":
        if len(argv) < 2 or not argv[1].lstrip("§").isdigit():
            sys.exit("usage: nbtool.py section N [NOTEBOOK]")
        n = int(argv[1].lstrip("§"))
        cmd_section(_load(_find_notebook(argv[2] if len(argv) > 2 else None)), n)
    elif cmd == "grep":
        if len(argv) < 2:
            sys.exit("usage: nbtool.py grep PATTERN [NOTEBOOK]")
        cmd_grep(_load(_find_notebook(argv[2] if len(argv) > 2 else None)), argv[1])
    else:
        sys.exit(f"unknown command: {cmd}\n{__doc__}")


if __name__ == "__main__":
    main(sys.argv[1:])
