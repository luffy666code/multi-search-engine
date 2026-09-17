# Changelog

All notable changes to this project are documented in this file.

## 1.5.0

- Expanded from region-specific search to global web search.
- Added global search-engine adapters (Bing, Google, DuckDuckGo, Brave).
- Added automatic engine routing based on query language and runtime availability.
- Added semantic quality validation, evidence tracking, and failure fallback.
- Removed region-only restrictions.

### Added

- Central engine catalog (`scripts/engine_catalog.py`) as the single source of truth for engine family, host, site capability, and URL templates.
- `--profile auto|all|cn|global` routing; `auto` orders engines by query language while keeping both Chinese-language and global engines available.
- `site:` support across both Chinese-language and global web engines; vertical engines are skipped automatically.
- Result parsers for Bing, DuckDuckGo, Google, and Brave with conservative external-anchor fallback.
- Anti-bot and challenge-page detection for global engines; `--engine direct` can fetch any reachable public HTTP/HTTPS URL.
- `scripts/url_safety.py` refuses loopback, private, link-local, and cloud-metadata endpoints by default.
- Three-layer `transport / parse / semantic` status in run manifests and results.
- Explicit `--market`; query language and geographic market are independent.

### Changed

- Default `--network-fail-skip 0`: after a pure network failure in a routing scope, remaining engines in that scope use a short probe timeout but are still attempted once by default.
- Cross-engine dedup prefers real canonical URLs, then near-duplicate titles within the same publisher domain.
- Redirect tracking split into `host_redirected / redirect_from_host / redirect_to_host / geo_localized`.
- `self_check.py` is an optional diagnostic tool, decoupled from the run environment.

## 1.4.0

- Added cross-platform Linux support; docs use `/` paths and distinguish `python3` (Linux) from `python` (Windows).
- Minimum Python version is 3.7; removed Python 3.9+ runtime syntax.
- Added `scripts/runtime.py` for runtime detection, Python version, OS/architecture, and CA/TLS policy.
- TLS verification supports system CA, `--ca-bundle`, `MSE_CA_BUNDLE`, and optional certifi.
- Added `--allow-insecure-fallback` and `--insecure`; TLS downgrade is never silent and is recorded in the run manifest.
- Run manifest records runtime / tls_policy; engine attempts record `tls_mode / tls_verified / tls_fallback_used / ca_source`.
- Added `references/linux-execution.md`; Windows docs moved to cross-platform path style.

## 1.3.0

- Fixed silent `site:` degradation: added query-intent parsing and target-domain validation; an unmatched target domain returns `semantic_ok=false`.
- `useful_engines` now driven by semantic quality: `semantic_status / semantic_usable / relevant_item_count / site_match_count / noise_rate / summary_coverage / direct_url_coverage`.
- Removed "stop after the first two engines have results"; all compatible engines are tried by default, with explicit early-stop thresholds.
- `site:` queries skip vertical engines such as Sogou WeChat and Toutiao.
- Added filtering for navigation pages, legal/feedback pages, pagination numbers, explicit ads, and site-domain mismatches; filtered items remain in per-engine `rejected_items` for audit.
- Real target URLs are extracted from Baidu `mu / data-tools / data-url`; unified `redirect_url / url_state / publisher_domain / display_domain`.
- Added `scripts/resolve_link.py` for on-demand best-effort resolution of opaque redirects from a shortlist.
- When summaries are missing, a limited fallback is generated from visible text and recorded as `summary_state`.
- Added `results-preview.md` and `scripts/show.py`.
- Cross-engine dedup uses normalized title plus publisher domain.
- `self_check.py` adds offline site-intent regression tests.

## 1.2.0

- Added `scripts/search.py` as the single recommended entry point.
- Each search uses an isolated `temp_search/runs/<run-id>/`.
- stdout uses short ASCII JSON; structured files stay UTF-8.
- Added fetch/parse/cleanup/self_check, anti-bot detection, dynamic availability, and structured evidence logging.

## 1.1.0

- Added low-level fetch/parse scripts and the structured evidence specification.

## 1.0.0

- Initial web-research structure with a structured source strategy.
