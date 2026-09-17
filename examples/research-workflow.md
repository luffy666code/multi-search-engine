# End-to-end research workflow

A reliable research loop is not one search call. It is: search → shortlist → resolve original URL → verify source → produce claims.

## 1. Search

```bash
python scripts/search.py "embodied intelligence benchmark" --profile auto
```

Pick a shortlist from `results-preview.md`. Prefer results with a real `direct` URL and a non-empty `publisher_domain`.

## 2. Resolve the original URL

If a shortlisted result is an opaque redirect (for example a Baidu `mu` wrapper or a Bing `/ck/a` link), resolve it on demand:

```bash
python scripts/resolve_link.py "<redirect-url>" --referer "<search-url>"
```

Only resolve the links you actually plan to cite.

## 3. Verify the source

Open the original page when a claim matters. A search snippet is grade C; the claim only moves toward grade A/B after the original source is opened and content-verified.

```bash
python scripts/fetch.py "https://example.com/paper" --engine direct --name verify-01
```

## 4. Produce claims

Write each claim with its evidence: title, URL, publisher domain, and verification state. Two engines surfacing the same article still count as one independent source.

Do not convert transport success into semantic success. If the run reports `semantic_ok=false` or `site-intent-unsatisfied`, treat that engine's results as untrusted and route to another engine.
