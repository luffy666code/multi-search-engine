#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Safely clean one search run while preserving final structured outputs."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
RUNS_ROOT = (SKILL_ROOT / "temp_search" / "runs").resolve()
FINAL_ROOT = (SKILL_ROOT / "temp_search" / "final").resolve()
FINAL_FILES = ("search-run.json", "results.json", "results-preview.md", "evidence.json")


def emit(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=True, separators=(",", ":")))


def main() -> int:
    ap = argparse.ArgumentParser(description="清理单次检索运行目录")
    ap.add_argument("run", help="run-id or absolute run directory")
    ap.add_argument("--discard-final", action="store_true", help="Do not preserve search-run/results/evidence JSON")
    args = ap.parse_args()

    p = Path(args.run)
    run_dir = p.resolve() if p.is_absolute() else (RUNS_ROOT / p).resolve()

    try:
        run_dir.relative_to(RUNS_ROOT)
    except ValueError:
        emit({"ok": False, "error": "refuse-to-delete-outside-runs-root", "path": str(run_dir)})
        return 2

    if not run_dir.exists() or not run_dir.is_dir():
        emit({"ok": False, "error": "run-not-found", "path": str(run_dir)})
        return 2

    preserved = []
    if not args.discard_final:
        dest = FINAL_ROOT / run_dir.name
        dest.mkdir(parents=True, exist_ok=True)
        for name in FINAL_FILES:
            src = run_dir / name
            if src.exists():
                shutil.copy2(src, dest / name)
                preserved.append(str(dest / name))

    shutil.rmtree(run_dir)
    emit({"ok": True, "deleted": str(run_dir), "preserved": preserved})
    return 0


if __name__ == "__main__":
    sys.exit(main())
