# Site-restricted search

Use `site:` to constrain results to a specific domain.

```bash
python scripts/search.py "site:arxiv.org embodied intelligence"
```

`site:` is not just a text hint. The skill validates that at least one structured result actually belongs to the target domain. If the search engine ignores or rewrites the operator, the run returns `semantic_ok=false` / `site-intent-unsatisfied` instead of silently accepting results from the wrong domain.

So even with `site:`, semantic validation still runs — a matching title is not enough on its own.
