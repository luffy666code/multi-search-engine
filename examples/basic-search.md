# Basic search

The shortest useful query. The skill picks engines automatically based on query language.

```bash
python scripts/search.py "AI agent frameworks"
```

On Linux, use `python3`:

```bash
python3 scripts/search.py "AI agent frameworks"
```

Results are written to an isolated `temp_search/runs/<run-id>/`. Use `scripts/show.py <run-id> --top 5` to inspect them, or open `results-preview.md`.
