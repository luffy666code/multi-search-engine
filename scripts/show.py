#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Emit a small slice of results instead of dumping the whole UTF-8 JSON file.

stdout remains ASCII-only JSON for Windows safety, but only requested rows/fields
are emitted, preventing multi-layer escaped full-file output from wasting tokens.
"""
from __future__ import annotations
import argparse, json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "temp_search" / "runs"
FINAL = ROOT / "temp_search" / "final"


def emit(obj): print(json.dumps(obj, ensure_ascii=True, separators=(",", ":")))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("--top", type=int, default=8)
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--summary-chars", type=int, default=180)
    args = ap.parse_args()
    run = args.run
    if not re.fullmatch(r"[0-9A-Za-z._-]{1,96}", run):
        emit({"ok": False, "error": "invalid-run-id"}); return 2
    path = RUNS / run / "results.json"
    if not path.exists(): path = FINAL / run / "results.json"
    if not path.exists(): emit({"ok": False, "error": "results-not-found"}); return 2
    obj = json.loads(path.read_text(encoding="utf-8"))
    items = obj.get("items", [])
    start = max(0, args.start - 1)
    rows = []
    for i, it in enumerate(items[start:start + max(1, args.top)], start + 1):
        rows.append({
            "index": i,
            "title": it.get("title"),
            "publisher_domain": it.get("publisher_domain"),
            "url": it.get("url"),
            "url_state": it.get("url_state"),
            "summary": (it.get("summary") or "")[:max(0, args.summary_chars)],
            "intent_status": it.get("intent_status"),
            "engine": it.get("engine"),
        })
    emit({"ok": True, "run_id": run, "total": len(items), "rows": rows})
    return 0

if __name__ == "__main__": sys.exit(main())
