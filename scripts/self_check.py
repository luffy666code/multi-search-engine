#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline package/runtime self-check; no network access required.

This is a diagnostic tool, not a packaging gate. It validates package structure,
Python 3.7 compatibility, runtime basics, engine/parser consistency and semantic
regressions without assuming that any search engine is reachable from the host.
"""
from __future__ import annotations

import argparse
import ast
import json
import py_compile
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from runtime import environment_report, ensure_supported_python
from engine_catalog import default_engine_order, engine_names, site_capable_engines, query_language_hint, make_search_url
from quality import classify_item, evaluate_engine, parse_query_intent
from parse import PARSERS
from search import result_is_merge_candidate
from url_safety import URLSafetyError, inspect_url

REQUIRED = [
    "SKILL.md", "Skillicon.png", "LICENSE", "CHANGELOG.md",
    "scripts/runtime.py", "scripts/engine_catalog.py", "scripts/search.py", "scripts/fetch.py",
    "scripts/parse.py", "scripts/quality.py", "scripts/resolve_link.py", "scripts/show.py",
    "scripts/cleanup.py", "scripts/self_check.py", "scripts/url_safety.py",
    "references/search-sources.md", "references/query-strategy.md",
    "references/reliability-and-fallback.md", "references/evidence-schema.md",
    "references/windows-execution.md", "references/linux-execution.md",
]


def parse_args():
    ap = argparse.ArgumentParser(description="multi-search-engine offline/runtime self-check")
    ap.add_argument("--ca-bundle", default=None)
    ap.add_argument("--require-ca", action="store_true", help="Fail if no CA certificates are detectable")
    return ap.parse_args()


def syntax_check_python37(path: Path, errors):
    source = path.read_text(encoding="utf-8")
    try:
        try:
            ast.parse(source, filename=str(path), feature_version=(3, 7))
        except (TypeError, ValueError):
            try:
                ast.parse(source, filename=str(path), feature_version=7)
            except TypeError:
                ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        errors.append("python37-syntax:%s:%s" % (path.name, exc))


def main() -> int:
    args = parse_args()
    errors = []
    warnings = []

    py_ok, py_version = ensure_supported_python()
    if not py_ok:
        errors.append("python-too-old:%s:min=3.7" % py_version)

    for rel in REQUIRED:
        if not (ROOT / rel).exists():
            errors.append("missing:%s" % rel)

    for path in (ROOT / "scripts").glob("*.py"):
        try:
            py_compile.compile(str(path), doraise=True)
        except Exception as exc:
            errors.append("compile:%s:%s" % (path.name, exc))
        syntax_check_python37(path, errors)

    # Catalog/parser contract: every configured search engine must have a parser.
    configured = set(engine_names())
    parsed = set(PARSERS.keys())
    if configured != parsed:
        errors.append("engine-parser-mismatch:configured=%s:parsers=%s" % (
            sorted(configured), sorted(parsed)
        ))

    # Auto routing must include both domestic and global engines, merely changing order.
    zh_plan = default_engine_order("具身智能 产业报告", "auto")
    en_plan = default_engine_order("embodied intelligence industry report", "auto")
    if not ({"baidu", "bing"}.issubset(set(zh_plan)) and {"baidu", "bing"}.issubset(set(en_plan))):
        errors.append("routing-regression:auto-must-include-cn-and-global")
    if not zh_plan or zh_plan[0] != "baidu":
        errors.append("routing-regression:cjk-order")
    if not en_plan or en_plan[0] != "bing":
        errors.append("routing-regression:latin-order")
    if query_language_hint("ByteDance AI 战略") != "mixed":
        errors.append("language-regression:mixed-query")
    if query_language_hint("site:leadleo.com 具身智能 报告") != "cjk":
        errors.append("language-regression:operator-domain-must-not-force-latin")
    if query_language_hint("site:arxiv.org embodied intelligence") != "latin":
        errors.append("language-regression:latin-site-query")
    google_default = make_search_url("google", "具身智能 报告")
    google_us = make_search_url("google", "具身智能 报告", "us")
    if "gl=" in google_default:
        errors.append("market-regression:language-must-not-imply-google-market")
    if "gl=us" not in google_us:
        errors.append("market-regression:explicit-google-market")


    # Offline parser fixtures for the newly added international engines.
    parser_fixtures = {
        "bing": (
            '<li class="b_algo"><h2><a href="https://www.bing.com/ck/a?u=a1aHR0cHM6Ly9leGFtcGxlLmNvbS9jaw&ntb=1">Wrapped Report</a></h2>'
            '<div class="b_caption"><p>Industry report and market overview</p></div></li>'
            '<li class="b_algo"><h2><a href="https://example.com/a">Embodied Intelligence Report</a></h2>'
            '<div class="b_caption"><p>Industry report and market overview</p></div></li>',
            "https://www.bing.com/search?q=x",
        ),
        "duckduckgo": (
            '<div class="result results_links"><h2 class="result__title"><a class="result__a" '
            'href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fb">Embodied Intelligence Research</a></h2>'
            '<a class="result__snippet">Research summary from example</a></div>',
            "https://html.duckduckgo.com/html/?q=x",
        ),
        "google": (
            '<div class="MjjYud"><a href="/url?q=https://example.com/c"><h3>Embodied Intelligence Paper</h3></a>'
            '<div class="VwiC3b">Paper summary and results</div></div>',
            "https://www.google.com/search?q=x",
        ),
        "brave": (
            '<div class="snippet"><a href="https://example.com/d">Embodied Intelligence News</a>'
            '<div class="snippet-description">Latest news and analysis</div></div>',
            "https://search.brave.com/search?q=x",
        ),
    }
    for engine, fixture in parser_fixtures.items():
        try:
            rows, strategy = PARSERS[engine](fixture[0], fixture[1])
            if not rows or rows[0].get("publisher_domain") != "example.com":
                errors.append("parser-regression:%s:%s" % (engine, strategy))
        except Exception as exc:
            errors.append("parser-regression:%s:%s" % (engine, exc))

    # Runtime compatibility regression: QueryIntent.to_dict must work on 3.7.
    try:
        probe = parse_query_intent("site:example.com embodied intelligence report").to_dict()
        if not isinstance(probe, dict) or not probe.get("is_site_query"):
            errors.append("runtime-regression:query-intent-to-dict")
    except Exception as exc:
        errors.append("runtime-regression:query-intent-to-dict:%s" % exc)

    skill = ROOT / "SKILL.md"
    if skill.exists():
        text = skill.read_text(encoding="utf-8")
        if not text.startswith("---\n") or text.count("---") < 2:
            errors.append("skill-frontmatter-invalid")
        for ref in re.findall(r'`(references/[^`]+\.md)`', text):
            if not (ROOT / ref).exists():
                errors.append("broken-reference:%s" % ref)

    # Global site: drift regression: wrong-domain results must never be useful.
    intent = parse_query_intent("site:example.com embodied intelligence report")
    bad_rows = [
        classify_item({
            "title": "Search Home", "url": "https://www.bing.com/", "url_state": "search-host-link",
            "summary": "", "publisher_domain": None, "display_domain": None, "ad_suspected": False,
        }, intent),
        classify_item({
            "title": "Embodied intelligence report tool", "url": "https://example.net/tool", "url_state": "direct",
            "summary": "report generator", "publisher_domain": "example.net", "display_domain": "example.net",
            "ad_suspected": False,
        }, intent),
    ]
    q_bad = evaluate_engine(bad_rows, intent, 1)
    if q_bad.get("semantic_usable") or q_bad.get("semantic_status") not in {
        "intent-drift", "site-constraint-unverified", "parse-empty"
    }:
        errors.append("semantic-regression:site-drift:%s" % q_bad)

    good_rows = [
        classify_item({
            "title": "Embodied Intelligence Industry Report", "url": "https://example.com/report/1",
            "url_state": "direct", "summary": "embodied intelligence industry report",
            "publisher_domain": "example.com", "display_domain": "example.com", "ad_suspected": False,
        }, intent),
    ]
    q_good = evaluate_engine(good_rows, intent, 1)
    if not q_good.get("semantic_usable") or q_good.get("site_match_count") != 1:
        errors.append("semantic-regression:site-match:%s" % q_good)

    # Site-capability must exclude vertical engines and include web engines in both regions.
    site_capable = site_capable_engines()
    if "weixin" in site_capable or "toutiao" in site_capable:
        errors.append("site-capability-regression:vertical-engine")
    if not {"baidu", "bing", "google", "duckduckgo"}.issubset(site_capable):
        errors.append("site-capability-regression:global-web-engine")

    # In-engine cards/navigation (search-host-link with no external publisher) must
    # never enter the merged candidate set even when their text matches query terms.
    general_intent = parse_query_intent("embodied intelligence")
    card = classify_item({
        "title": "Embodied dictionary", "url": "https://cn.bing.com/dict/search?q=embodied",
        "url_state": "search-host-link", "summary": "",
        "publisher_domain": None, "display_domain": None, "ad_suspected": False,
    }, general_intent)
    if result_is_merge_candidate(card, False):
        errors.append("merge-regression:search-host-link-accepted:%s" % card.get("intent_status"))
    external = classify_item({
        "title": "embodied intelligence report", "url": "https://example.com/x",
        "url_state": "direct", "summary": "embodied intelligence",
        "publisher_domain": "example.com", "display_domain": "example.com", "ad_suspected": False,
    }, general_intent)
    if not result_is_merge_candidate(external, False):
        errors.append("merge-regression:direct-external-rejected")

    # URL-safety regression: public build blocks obvious SSRF/local targets by default.
    for unsafe in ("http://127.0.0.1/", "http://localhost/", "http://169.254.169.254/"):
        try:
            inspect_url(unsafe, False)
            errors.append("url-safety-regression:accepted:%s" % unsafe)
        except URLSafetyError:
            pass
    try:
        inspect_url("http://127.0.0.1/", True)
    except Exception as exc:
        errors.append("url-safety-regression:explicit-private-optin:%s" % exc)

    runtime = environment_report(args.ca_bundle)
    warnings.extend(runtime.get("warnings") or [])
    if args.require_ca and "no-ca-certificates-detected" in warnings:
        errors.append("runtime:no-ca-certificates-detected")

    result = {
        "ok": not errors,
        "root": str(ROOT),
        "runtime": runtime,
        "engine_count": len(configured),
        "engines": sorted(configured),
        "errors": errors,
        "warnings": sorted(set(warnings)),
    }
    print(json.dumps(result, ensure_ascii=True, separators=(",", ":")))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
