#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Main entry point for global multi-source web search.

Domestic and international search engines are discovery channels. Reachability
and usefulness are decided at runtime; HTTP success alone never counts as
semantic success. The default ``auto`` profile only changes engine order based
on query language and still allows both domestic and global sources.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple
from difflib import SequenceMatcher

from runtime import ensure_supported_python, environment_report, execution_os, python_version_string

from engine_catalog import (
    default_engine_order,
    engine_family,
    engine_host_suffix,
    engine_names,
    engine_scope,
    engine_network_group,
    make_search_url,
    query_language_hint,
    site_capable_engines,
)

from quality import (
    evaluate_engine,
    normalize_domain,
    parse_query_intent,
    publisher_key,
    canonical_url_key,
    result_quality_score,
)

SKILL_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = SKILL_ROOT / "scripts"
RUNS_ROOT = SKILL_ROOT / "temp_search" / "runs"

# Engine metadata is centralized in engine_catalog.py.  Search order is dynamic:
# CJK queries prefer domestic discovery first, latin queries prefer global
# discovery first, while both remain available under the default auto profile.
ENGINE_ORDER = engine_names()
ENGINE_DOMAINS = {name: engine_host_suffix(name) for name in ENGINE_ORDER}
ENGINE_FAMILIES = {name: engine_family(name) for name in ENGINE_ORDER}
ENGINE_SCOPES = {name: engine_scope(name) for name in ENGINE_ORDER}
ENGINE_NETWORK_GROUPS = {name: engine_network_group(name) for name in ENGINE_ORDER}
SITE_CAPABLE = site_capable_engines()

# A network-level failure (timeout / DNS / reset / TLS EOF), unlike 403/captcha,
# says nothing about anti-bot. Scope is only a routing label, not a hard network
# fault domain. After one scope failure later engines use a short probe timeout;
# hard scope skipping is opt-in only (--network-fail-skip > 0).
NETWORK_ERROR_MARKERS = (
    "timed out", "timeout", "urlopen error", "getaddrinfo failed", "dns resolution failed",
    "name or service not known", "connection reset", "connection refused",
    "connection aborted", "unexpected eof", "eof occurred",
)
REAL_URL_STATES = ("direct", "extracted-direct", "resolved-direct")


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def emit_json(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=True, separators=(",", ":")))


def parse_stdout_json(stdout: str) -> dict:
    for line in reversed([x.strip() for x in stdout.splitlines() if x.strip()]):
        try: return json.loads(line)
        except json.JSONDecodeError: pass
    return {"raw_stdout": stdout[-1200:]}


def run_script(args: List[str]) -> Tuple[int, dict, str]:
    cp = subprocess.run(
        [sys.executable] + args, cwd=str(SKILL_ROOT), stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="utf-8", errors="replace", shell=False,
    )
    return cp.returncode, parse_stdout_json(cp.stdout), cp.stdout


def result_is_merge_candidate(item: dict, site_query: bool) -> bool:
    if item.get("excluded"): return False
    status = item.get("intent_status")
    # Every merged candidate must point at a real external publisher. In-engine
    # cards (dictionary cards, /search navigation, /aclk ads) never qualify even
    # when their text happens to match query terms.
    has_external = item.get("url_state") in REAL_URL_STATES or bool(item.get("publisher_domain"))
    if site_query:
        return item.get("site_target_match") is True and status in ("matched", "site-only")
    # Keep matched/partial.  "unknown" is retained only for direct/extracted URLs;
    # opaque, snippet-less unknown rows are too weak for the merged candidate set.
    if status in ("matched", "partial"): return has_external
    if status == "unknown" and item.get("url_state") in REAL_URL_STATES:
        return True
    return False


def _normalized_title_for_similarity(title: str) -> str:
    text = re.sub(r"\s+", " ", (title or "").lower()).strip()
    text = re.sub(r"\s*[-|_|–|—]\s*[^-|_|–|—]{1,40}$", "", text)
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", text)


def _find_merge_index(merged: List[dict], item: dict) -> int:
    url_key = canonical_url_key(item)
    domain = normalize_domain(item.get("publisher_domain") or item.get("display_domain"))
    title_norm = _normalized_title_for_similarity(item.get("title") or "")
    for idx, existing in enumerate(merged):
        existing_url = canonical_url_key(existing)
        if url_key and existing_url and url_key == existing_url:
            return idx
        existing_domain = normalize_domain(existing.get("publisher_domain") or existing.get("display_domain"))
        if domain and existing_domain and domain == existing_domain:
            a = title_norm
            b = _normalized_title_for_similarity(existing.get("title") or "")
            if a and b and (a == b or SequenceMatcher(None, a, b).ratio() >= 0.92):
                return idx
    return -1


def merge_item(merged: List[dict], item: dict) -> None:
    idx = _find_merge_index(merged, item)
    if idx >= 0:
        existing = merged[idx]
        vias = set(existing.get("discovered_via_all") or [existing.get("discovered_via")])
        vias.add(item.get("discovered_via"))
        existing["discovered_via_all"] = sorted(x for x in vias if x)
        quality_rank = {"resolved-direct": 4, "extracted-direct": 3, "direct": 2, "opaque-redirect": 1, "search-host-link": 0}
        if quality_rank.get(item.get("url_state"), 0) > quality_rank.get(existing.get("url_state"), 0):
            for key in ("url", "redirect_url", "url_state", "publisher_domain", "display_domain"):
                existing[key] = item.get(key)
        if len(item.get("summary") or "") > len(existing.get("summary") or ""):
            existing["summary"] = item.get("summary")
            existing["summary_state"] = item.get("summary_state")
        existing["quality_score"] = result_quality_score(existing)
        return
    row = dict(item)
    row["publisher_key"] = publisher_key(row)
    row["discovered_via_all"] = [row.get("discovered_via")] if row.get("discovered_via") else []
    row["quality_score"] = result_quality_score(row)
    merged.append(row)


def write_preview(path: Path, query: str, items: List[dict], limit: int = 10) -> None:
    lines = [f"# 检索预览", "", f"查询：{query}", "", "| # | 标题 | 来源域名 | 摘要 | 链接状态 | 质量分 |", "|---:|---|---|---|---|---:|"]
    for i, it in enumerate(items[:limit], 1):
        title = (it.get("title") or "").replace("|", "\\|")
        domain = it.get("publisher_domain") or it.get("display_domain") or "未知"
        summary = (it.get("summary") or "").replace("|", "\\|").replace("\n", " ")[:180]
        state = it.get("url_state") or ""
        lines.append(f"| {i} | {title} | {domain} | {summary} | {state} | {it.get('quality_score', 0)} |")
    if not items:
        lines.append("| - | 无通过语义质量门槛的结果 | - | - | - | - |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="全网多源检索主入口")
    ap.add_argument("query", help="Search query; pass it as one quoted argument")
    ap.add_argument("--profile", choices=["auto", "all", "cn", "global"], default="auto",
                    help="auto=both regions with language-aware order; all=fixed mixed order; cn/global=restrict discovery engines")
    ap.add_argument("--engines", default="",
                    help="Explicit comma-separated engine order. Overrides --profile when provided")
    ap.add_argument("--min-results", type=int, default=2, help="Minimum semantically relevant results for a general-query engine")
    ap.add_argument("--interval", type=float, default=4.0)
    ap.add_argument("--jitter", type=float, default=1.0)
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--probe-timeout", type=float, default=6.0,
                    help="Short timeout for later engines in the same routing scope after one network failure")
    ap.add_argument("--network-fail-skip", type=int, default=0,
                    help="Aggressive opt-in: after N network failures in a routing scope, skip its remaining engines. Default 0 never hard-skips")
    ap.add_argument("--market", default="none",
                    help="Optional geographic market hint (e.g. cn/us/jp/gb). Default none: language does not imply geography")
    ap.add_argument("--max-bytes", type=int, default=8_000_000)
    ap.add_argument("--ca-bundle", default=None, help="Custom PEM CA bundle for Linux/container environments")
    ap.add_argument("--insecure", action="store_true", help="Disable TLS verification explicitly")
    ap.add_argument("--allow-insecure-fallback", action="store_true",
                    help="Retry with TLS verification disabled only after a certificate-verification failure")
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--stop-after-families", type=int, default=0,
                    help="0 = no early stop (default). Otherwise require this many semantically useful engine families plus --stop-min-results and --stop-min-publishers")
    ap.add_argument("--stop-min-results", type=int, default=8)
    ap.add_argument("--stop-min-publishers", type=int, default=4)
    args = ap.parse_args()
    args.market = (args.market or "none").lower()
    if args.market not in ("none", "auto", "global", "worldwide") and not re.fullmatch(r"[a-z]{2}", args.market):
        emit_json({"ok": False, "error": "invalid-market", "market": args.market, "expected": "none or two-letter country code"})
        return 2

    py_ok, py_version = ensure_supported_python()
    if not py_ok:
        emit_json({
            "ok": False, "error": "unsupported-python", "python_version": py_version,
            "minimum": "3.7", "execution_os": execution_os(),
        })
        return 2

    runtime = environment_report(args.ca_bundle)
    intent = parse_query_intent(args.query)
    lang_hint = query_language_hint(args.query)
    if lang_hint == "cjk":
        accept_language = "zh-CN,zh;q=0.9,en;q=0.8"
    elif lang_hint == "latin":
        accept_language = "en-US,en;q=0.9,zh-CN;q=0.7"
    else:
        accept_language = "zh-CN,zh;q=0.9,en;q=0.9"
    if args.engines.strip():
        engines = [x.strip() for x in args.engines.split(",") if x.strip()]
        engine_plan_source = "explicit"
    else:
        engines = default_engine_order(args.query, args.profile)
        engine_plan_source = "profile:%s" % args.profile
    unknown = [x for x in engines if x not in ENGINE_ORDER]
    if unknown:
        emit_json({"ok": False, "error": "unsupported-engines", "engines": unknown}); return 2

    run_id = args.run_id or (datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6])
    if not re.fullmatch(r"[0-9A-Za-z._-]{1,96}", run_id):
        emit_json({"ok": False, "error": "invalid-run-id"}); return 2
    run_dir = (RUNS_ROOT / run_id).resolve(); raw_dir = run_dir / "raw"; raw_dir.mkdir(parents=True, exist_ok=False)

    manifest = {
        "run_id": run_id, "query": args.query, "query_intent": intent.to_dict(),
        "started_at": now_iso(), "finished_at": None, "run_dir": str(run_dir),
        "engine_profile": args.profile, "market": args.market, "engine_plan_source": engine_plan_source,
        "engine_plan_requested": engines, "engine_plan_effective": [], "attempts": [],
        "useful_engines": [], "useful_engine_families": [], "skipped_engines": [],
        "intent_satisfied": False, "failure_reason": None,
        "runtime": runtime,
        "tls_policy": {
            "insecure": bool(args.insecure),
            "allow_insecure_fallback": bool(args.allow_insecure_fallback),
            "ca_bundle": args.ca_bundle,
        },
    }

    # Capability-aware plan: site: must never be "satisfied" by vertical engines
    # that cannot enforce arbitrary domains. Domestic and global web engines are
    # both eligible when they declare site-capability.
    effective = []
    for e in engines:
        if intent.is_site_query and e not in SITE_CAPABLE:
            manifest["skipped_engines"].append({"engine": e, "reason": "site-constraint-unsupported"})
        else:
            effective.append(e)
    manifest["engine_plan_effective"] = effective

    merged: List[dict] = []
    blocked_domains = set(); useful_families = set()
    network_failures_by_scope: Dict[str, int] = {}

    for idx, engine in enumerate(effective, 1):
        domain_group = ENGINE_DOMAINS[engine]
        scope = ENGINE_SCOPES[engine]
        network_group = ENGINE_NETWORK_GROUPS[engine]
        scope_failures = network_failures_by_scope.get(scope, 0)
        if args.network_fail_skip > 0 and scope_failures >= args.network_fail_skip:
            manifest["skipped_engines"].append(
                {"engine": engine, "reason": "regional-network-unreachable:%s:%d" % (scope, scope_failures)})
            continue
        if domain_group in blocked_domains:
            manifest["skipped_engines"].append({"engine": engine, "reason": f"same-domain-blocked:{domain_group}"}); continue
        if manifest["attempts"]:
            time.sleep(max(0.0, args.interval) + random.random() * max(0.0, args.jitter))

        fast_probe = scope_failures > 0
        engine_timeout = min(args.timeout, args.probe_timeout) if fast_probe else args.timeout
        name = f"{idx:02d}-{engine}"; url = make_search_url(engine, args.query, args.market)
        fetch_args = [str(SCRIPTS / "fetch.py"), url, "--engine", engine, "--name", name,
                      "--out-dir", str(run_dir), "--timeout", str(engine_timeout),
                      "--max-bytes", str(args.max_bytes), "--retries", "0",
                      "--accept-language", accept_language]
        if args.ca_bundle:
            fetch_args.extend(["--ca-bundle", args.ca_bundle])
        if args.insecure:
            fetch_args.append("--insecure")
        if args.allow_insecure_fallback:
            fetch_args.append("--allow-insecure-fallback")
        fetch_rc, fetch_info, _ = run_script(fetch_args)
        meta_path = run_dir / f"{name}.meta.json"; meta = {}
        if meta_path.exists():
            try: meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception: pass

        attempt = {
            "engine": engine, "engine_family": ENGINE_FAMILIES[engine], "scope": scope,
            "network_group": network_group, "fast_probe": fast_probe, "network_unreachable": False,
            "request_url": url,
            "fetch_returncode": fetch_rc, "fetch_meta": str(meta_path), "status": meta.get("status"),
            "final_url": meta.get("final_url"), "blocked": bool(meta.get("blocked")),
            "blocked_reason": meta.get("blocked_reason"), "captcha_detected": bool(meta.get("captcha_detected")),
            "low_signal": bool(meta.get("low_signal")),
            "host_redirected": bool(meta.get("host_redirected")),
            "redirect_from_host": meta.get("redirect_from_host"), "redirect_to_host": meta.get("redirect_to_host"),
            "geo_localized": bool(meta.get("geo_localized")), "geo_redirect": bool(meta.get("geo_redirect")),
            "url_safety": meta.get("url_safety"),
            "error": meta.get("error"), "body_sha256": meta.get("body_sha256"),
            "tls_mode": meta.get("tls_mode"), "tls_verified": meta.get("tls_verified"),
            "tls_fallback_used": bool(meta.get("tls_fallback_used")), "ca_source": meta.get("ca_source"),
            "parse_returncode": None, "parse_strategy": None, "results_file": None,
            "quality": {"semantic_status": "fetch-failed", "semantic_usable": False},
            "transport": {
                "ok": meta.get("status") is not None, "http_status": meta.get("status"),
                "fetch_returncode": fetch_rc, "blocked": bool(meta.get("blocked")),
                "network_unreachable": False,
            },
            "parse": {"ok": False, "returncode": None, "item_count": 0},
            "semantic": {"ok": False, "status": "fetch-failed", "relevant_item_count": 0},
        }
        if meta.get("blocked"): blocked_domains.add(domain_group)

        # Scope-level failure count is a timeout heuristic only. Scope is not a hard
        # network fault domain; by default later engines are fast-probed, not skipped.
        err_text = str(meta.get("error") or "").lower()
        network_failed = (fetch_rc != 0 or meta.get("status") is None) and any(
            marker in err_text for marker in NETWORK_ERROR_MARKERS)
        if network_failed:
            network_failures_by_scope[scope] = network_failures_by_scope.get(scope, 0) + 1
            attempt["network_unreachable"] = True
            attempt["transport"]["network_unreachable"] = True

        html_path = raw_dir / f"{name}.html"
        if fetch_rc == 0 and html_path.exists() and not meta.get("blocked"):
            parse_args = [str(SCRIPTS / "parse.py"), engine, str(html_path), "--name", name,
                          "--out-dir", str(run_dir), "--meta", str(meta_path), "--query", args.query]
            parse_rc, parse_info, _ = run_script(parse_args)
            attempt["parse_returncode"] = parse_rc; attempt["parse_strategy"] = parse_info.get("strategy")
            attempt["parse"]["returncode"] = parse_rc
            results_file = run_dir / f"{name}.results.json"; attempt["results_file"] = str(results_file)
            if results_file.exists():
                try:
                    obj = json.loads(results_file.read_text(encoding="utf-8"))
                    rows = list(obj.get("items", [])) + list(obj.get("rejected_items", []))
                    attempt["parse"]["ok"] = parse_rc == 0
                    attempt["parse"]["item_count"] = len(rows)
                    quality = evaluate_engine(rows, intent, args.min_results)
                    attempt["quality"] = quality
                    attempt["semantic"] = {
                        "ok": bool(quality.get("semantic_usable")),
                        "status": quality.get("semantic_status"),
                        "relevant_item_count": quality.get("relevant_item_count", 0),
                        "site_match_count": quality.get("site_match_count", 0),
                    }
                    if quality["semantic_usable"]:
                        manifest["useful_engines"].append(engine)
                        useful_families.add(ENGINE_FAMILIES[engine])
                    for item in obj.get("items", []):
                        if result_is_merge_candidate(item, intent.is_site_query):
                            merge_item(merged, item)
                except Exception as exc:
                    attempt["quality"] = {"semantic_status": "results-json-read-failed", "semantic_usable": False, "error": str(exc)}

        manifest["attempts"].append(attempt)

        # Optional quality-based early stop. Default is disabled so multi-source
        # discovery cannot silently collapse to the first two HTTP-successful engines.
        if args.stop_after_families > 0:
            publisher_domains = {normalize_domain(x.get("publisher_domain") or x.get("display_domain")) for x in merged}
            publisher_domains.discard("")
            if (len(useful_families) >= args.stop_after_families and len(merged) >= args.stop_min_results
                    and len(publisher_domains) >= args.stop_min_publishers):
                manifest["early_stop"] = {
                    "reason": "quality-threshold-met", "useful_families": len(useful_families),
                    "merged_results": len(merged), "publisher_domains": len(publisher_domains),
                }
                break

    for item in merged:
        item["quality_score"] = result_quality_score(item)
    merged.sort(key=lambda x: (x.get("quality_score", 0), len(x.get("summary") or "")), reverse=True)

    manifest["finished_at"] = now_iso(); manifest["useful_engine_families"] = sorted(useful_families)
    manifest["merged_item_count"] = len(merged)
    manifest["network_failures_by_scope"] = dict(network_failures_by_scope)
    manifest["language_hint"] = lang_hint
    site_matches = [x for x in merged if x.get("site_target_match") is True]
    if intent.is_site_query:
        manifest["intent_satisfied"] = bool(site_matches)
        if not site_matches: manifest["failure_reason"] = "site-intent-unsatisfied"
    else:
        manifest["intent_satisfied"] = bool(merged)
        if not merged: manifest["failure_reason"] = "no-semantically-qualified-results"

    transport_ok_count = sum(1 for a in manifest["attempts"] if (a.get("transport") or {}).get("ok"))
    parse_ok_count = sum(1 for a in manifest["attempts"] if (a.get("parse") or {}).get("ok"))
    semantic_ok_count = sum(1 for a in manifest["attempts"] if (a.get("semantic") or {}).get("ok"))
    run_status = {
        "transport": {"ok": transport_ok_count > 0, "successful_attempts": transport_ok_count},
        "parse": {"ok": parse_ok_count > 0, "successful_attempts": parse_ok_count},
        "semantic": {"ok": bool(manifest["intent_satisfied"]), "useful_attempts": semantic_ok_count,
                     "merged_item_count": len(merged), "failure_reason": manifest["failure_reason"]},
    }
    manifest["status"] = run_status

    merged_obj = {
        "run_id": run_id, "query": args.query, "query_intent": intent.to_dict(),
        "generated_at": manifest["finished_at"], "semantic_ok": manifest["intent_satisfied"],
        "status": run_status, "items": merged,
    }
    evidence_stub = {
        "run_id": run_id, "query": args.query, "generated_at": manifest["finished_at"],
        "semantic_ok": manifest["intent_satisfied"], "sources": merged, "claims": [],
        "note": "Search candidates are C-grade discovery evidence only. Opaque redirect links and search snippets must not be promoted until the original page is opened and verified.",
    }
    preview_path = run_dir / "results-preview.md"
    write_preview(preview_path, args.query, merged)
    (run_dir / "search-run.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "results.json").write_text(json.dumps(merged_obj, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "evidence.json").write_text(json.dumps(evidence_stub, ensure_ascii=False, indent=2), encoding="utf-8")

    ok = bool(manifest["intent_satisfied"] and merged and manifest["useful_engines"])
    emit_json({
        "ok": ok, "semantic_ok": manifest["intent_satisfied"], "run_id": run_id, "run_dir": str(run_dir),
        "useful_engines": manifest["useful_engines"], "useful_engine_families": manifest["useful_engine_families"],
        "merged_item_count": len(merged), "site_target": intent.site_domain,
        "failure_reason": manifest["failure_reason"], "execution_os": execution_os(),
        "engine_profile": args.profile, "market": args.market, "language_hint": lang_hint, "network_failures_by_scope": dict(network_failures_by_scope),
        "python_version": python_version_string(), "manifest": str(run_dir / "search-run.json"),
        "results": str(run_dir / "results.json"), "preview": str(preview_path), "evidence": str(run_dir / "evidence.json"),
    })
    if ok: return 0
    return 5 if intent.is_site_query and not manifest["intent_satisfied"] else 4


if __name__ == "__main__":
    sys.exit(main())
