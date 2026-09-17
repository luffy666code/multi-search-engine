#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Parse domestic and international search pages into one common contract.

Key properties:
- source-specific containers first, generic external-link fallback second
- best-effort extraction of original target URL/domain from result blocks
- visible-text fallback for missing summaries
- explicit filtering of navigation/legal/advertisement/pagination noise
- query/site intent annotations; no HTTP-200 page is semantically useful by itself
"""
from __future__ import annotations

import argparse
import base64
import html as html_mod
import json
import re
import sys
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from engine_catalog import search_host_suffixes

from quality import (
    classify_item,
    domain_matches,
    is_opaque_redirect,
    normalize_domain,
    parse_query_intent,
    url_host,
)

SKILL_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = SKILL_ROOT / "temp_search"
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_WS_RE = re.compile(r"\s+")
DOMAIN_RE = re.compile(r"(?<![\w.-])([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)(?::\d+)?(?:/[^\s<]*)?")
SEARCH_DOMAINS = search_host_suffixes()


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def emit_json(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=True, separators=(",", ":")))


def safe_name(value: str) -> str:
    value = re.sub(r"[^0-9A-Za-z._-]+", "_", value or "").strip("._")
    if not value:
        raise ValueError("name is empty after sanitization")
    return value[:96]


def resolve_skill_path(value: str) -> Path:
    p = Path(value)
    if not p.is_absolute():
        p = SKILL_ROOT / p
    return p.resolve()


def resolve_out_dir(value: Optional[str], html_path: Path) -> Path:
    if value:
        p = Path(value)
        if not p.is_absolute():
            p = SKILL_ROOT / p
        return p.resolve()
    if html_path.parent.name == "raw":
        return html_path.parent.parent.resolve()
    return DEFAULT_OUT.resolve()


def clean_text(value: object, max_len: int = 300) -> str:
    s = "" if value is None else str(value)
    s = html_mod.unescape(s)
    s = re.sub(r"<script\b[^>]*>.*?</script>", " ", s, flags=re.I | re.S)
    s = re.sub(r"<style\b[^>]*>.*?</style>", " ", s, flags=re.I | re.S)
    s = re.sub(r"<!--.*?-->", " ", s, flags=re.S)
    s = re.sub(r"<[^>]*>?", " ", s)
    s = _CTRL_RE.sub("", s)
    s = _WS_RE.sub(" ", s).strip()
    return s[:max_len]


def norm_url(raw: str, base: str) -> str:
    if not raw:
        return ""
    raw = html_mod.unescape(raw.strip())
    if raw.startswith(("javascript:", "#")):
        return ""
    absolute = urljoin(base, raw)
    parsed = urlparse(absolute)
    if parsed.scheme not in ("http", "https"):
        return ""
    qsl = parse_qsl(parsed.query, keep_blank_values=True)
    host = normalize_domain(parsed.hostname or "")
    # Bing international wraps natural results in /ck/a?u=a1<urlsafe-base64(target)>.
    # cn.bing.com usually serves direct hrefs, but www.bing.com often does not.
    if domain_matches(host, "bing.com") and parsed.path.startswith("/ck/a"):
        for k, v in qsl:
            if k == "u" and v[:2] in ("a1", "a0"):
                padded = v[2:] + "=" * (-len(v[2:]) % 4)
                try:
                    decoded = base64.urlsafe_b64decode(padded).decode("utf-8", "ignore")
                except Exception:
                    decoded = ""
                if decoded.startswith(("http://", "https://")):
                    return norm_url(decoded, base)
    qdict: Dict[str, List[str]] = {}
    for k, v in qsl:
        qdict.setdefault(k, []).append(v)
    unwrap_keys = ["url", "target", "u", "dest", "destination"]
    if is_search_domain(host):
        unwrap_keys.extend(["uddg", "q"])
    for key in unwrap_keys:
        for v in qdict.get(key) or []:
            if v.startswith(("http://", "https://")):
                return norm_url(v, base)
    drop = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "spm", "from"}
    kept = [(k, v) for k, v in qsl if k not in drop]
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", urlencode(kept, doseq=True), ""))


def is_search_domain(host: str) -> bool:
    h = normalize_domain(host)
    return any(h == d or h.endswith("." + d) for d in SEARCH_DOMAINS)


class AnchorCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: List[Tuple[str, str]] = []
        self._href: Optional[str] = None
        self._parts: List[str] = []
        self._skip = 0
    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in ("script", "style"):
            self._skip += 1; return
        if self._skip: return
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self._href = href; self._parts = []
    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in ("script", "style") and self._skip:
            self._skip -= 1; return
        if self._skip: return
        if tag == "a" and self._href is not None:
            self.links.append((self._href, clean_text(" ".join(self._parts), 220)))
            self._href = None; self._parts = []
    def handle_data(self, data):
        if not self._skip and self._href is not None:
            self._parts.append(data)


def split_blocks(html_text: str, start_re: str) -> List[str]:
    marks = [m.start() for m in re.finditer(start_re, html_text, re.I | re.S)]
    return [html_text[pos:(marks[i+1] if i+1 < len(marks) else len(html_text))] for i, pos in enumerate(marks)]


def extract_link(block: str, base: str) -> Tuple[str, str]:
    for pattern in (
        r"<h[1-4][^>]*>.*?<a[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>",
        r"<a[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>",
    ):
        m = re.search(pattern, block, re.I | re.S)
        if m:
            return norm_url(m.group(1), base), clean_text(m.group(2), 220)
    return "", ""


def find_summary(block: str, patterns: Iterable[str]) -> str:
    for pattern in patterns:
        m = re.search(pattern, block, re.I | re.S)
        if m:
            text = clean_text(m.group(1), 360)
            if text:
                return text
    return ""


def fallback_summary(block: str, title: str, domain_hint: str = "") -> str:
    text = clean_text(block, 1000)
    if title:
        text = text.replace(title, " ", 1)
    if domain_hint:
        text = text.replace(domain_hint, " ")
    text = re.sub(r"(?:百度快照|举报|收藏|广告|官网|进入网站|查看详情)", " ", text)
    text = _WS_RE.sub(" ", text).strip(" -|·—：:")
    if len(text) < 12:
        return ""
    return text[:360]


def extract_json_attr_target(block: str) -> str:
    # Baidu commonly carries the true destination in mu= or data-tools JSON.
    for attr in ("mu", "data-landurl", "data-url", "data-href"):
        m = re.search(rf"\b{re.escape(attr)}=[\"']([^\"']+)[\"']", block, re.I)
        if m and m.group(1).startswith(("http://", "https://")):
            return html_mod.unescape(m.group(1))
    m = re.search(r"\bdata-tools=[\"']([^\"']+)[\"']", block, re.I)
    if m:
        raw = html_mod.unescape(m.group(1))
        try:
            obj = json.loads(raw)
            for key in ("url", "mu", "landurl"):
                v = obj.get(key)
                if isinstance(v, str) and v.startswith(("http://", "https://")):
                    return v
        except Exception:
            pass
    return ""


def extract_display_domain(block: str, preferred: str = "") -> str:
    if preferred:
        return normalize_domain(preferred)
    visible = clean_text(block, 1200)
    for m in DOMAIN_RE.finditer(visible):
        host = normalize_domain(m.group(1))
        if host and not is_search_domain(host):
            return host
    # Also inspect raw attributes, which can carry an unrendered source URL.
    for m in re.finditer(r"https?://([A-Za-z0-9.-]+)", html_mod.unescape(block), re.I):
        host = normalize_domain(m.group(1))
        if host and not is_search_domain(host):
            return host
    return ""


def build_item(block: str, base: str, title: str, clicked_url: str, summary: str, engine: str) -> dict:
    extracted_target = extract_json_attr_target(block)
    clicked_url = norm_url(clicked_url, base)
    target_url = norm_url(extracted_target, base) if extracted_target else ""
    redirect_url = ""
    url_state = "direct"
    best_url = clicked_url

    if target_url and not is_search_domain(url_host(target_url)):
        best_url = target_url
        url_state = "extracted-direct"
        if clicked_url and clicked_url != target_url:
            redirect_url = clicked_url
    elif is_opaque_redirect(clicked_url):
        redirect_url = clicked_url
        best_url = clicked_url
        url_state = "opaque-redirect"
    elif is_search_domain(url_host(clicked_url)):
        url_state = "search-host-link"

    publisher_domain = ""
    if best_url and not is_search_domain(url_host(best_url)):
        publisher_domain = url_host(best_url)
    display_domain = extract_display_domain(block, publisher_domain)

    summary_state = "extracted" if summary else "missing"
    if not summary:
        summary = fallback_summary(block, title, display_domain)
        if summary:
            summary_state = "fallback-visible-text"

    # Conservative ad hint.  /baidu.php links frequently represent commercial/C-slot
    # results; the explicit 广告 marker is stronger and always treated as ad.
    lower = block.lower()
    ad_suspected = bool(re.search(r">\s*广告\s*<", block, re.I)) or (
        "baidu.php" in (redirect_url or clicked_url).lower() and any(x in lower for x in ("ec-tuiguang", "commercial", "广告"))
    )

    return {
        "title": title,
        "url": best_url,
        "redirect_url": redirect_url or None,
        "url_state": url_state,
        "publisher_domain": publisher_domain or None,
        "display_domain": display_domain or None,
        "summary": summary,
        "summary_state": summary_state,
        "ad_suspected": ad_suspected,
        "engine": engine,
    }


def generic_anchor_fallback(html_text: str, base: str, engine: str, search_hosts: Iterable[str], limit: int = 30) -> List[dict]:
    parser = AnchorCollector()
    try:
        parser.feed(html_text)
    except Exception:
        return []
    seen = set(); items = []
    for href, title in parser.links:
        if len(title) < 2:
            continue
        url = norm_url(href, base)
        if not url:
            continue
        parsed = urlparse(url); host = normalize_domain(parsed.hostname or "")
        if any(host == h or host.endswith("." + h) for h in search_hosts):
            if parsed.path in ("", "/") or parsed.path.startswith(("/s", "/web")):
                continue
        key = (url, title)
        if key in seen: continue
        seen.add(key)
        items.append({
            "title": title, "url": url,
            "redirect_url": url if is_opaque_redirect(url) else None,
            "url_state": "opaque-redirect" if is_opaque_redirect(url) else ("search-host-link" if is_search_domain(host) else "direct"),
            "publisher_domain": None if is_search_domain(host) else host,
            "display_domain": None if is_search_domain(host) else host,
            "summary": "", "summary_state": "missing", "ad_suspected": False, "engine": engine,
        })
        if len(items) >= limit: break
    return items


def dedupe(items: Iterable[dict], limit: int = 30) -> List[dict]:
    out = []; seen_urls = set(); seen_titles = set()
    for it in items:
        url = it.get("url") or ""; title = it.get("title") or ""
        title_key = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", title.lower())
        if not url or not title_key: continue
        # Direct URLs are reliable duplicate keys; opaque redirects are not.
        if it.get("url_state") != "opaque-redirect" and url in seen_urls: continue
        if title_key in seen_titles: continue
        seen_urls.add(url); seen_titles.add(title_key); out.append(it)
        if len(out) >= limit: break
    return out


def parse_blocks(html_text: str, base: str, engine: str, start_re: str, summary_patterns: Iterable[str]) -> List[dict]:
    items = []
    for block in split_blocks(html_text, start_re):
        clicked, title = extract_link(block, base)
        if not clicked or not title: continue
        summary = find_summary(block, summary_patterns)
        items.append(build_item(block, base, title, clicked, summary, engine))
    return dedupe(items)


def parse_baidu(html_text: str, base: str) -> Tuple[List[dict], str]:
    items = parse_blocks(html_text, base, "baidu", r'<div[^>]*class=["\'][^"\']*(?:result|c-container)[^"\']*["\']', (
        r'class=["\'][^"\']*c-abstract[^"\']*["\'][^>]*>(.*?)</(?:div|span|p)>',
        r'class=["\'][^"\']*content-right[^"\']*["\'][^>]*>(.*?)</(?:div|span|p)>',
        r'class=["\'][^"\']*c-span-last[^"\']*["\'][^>]*>(.*?)</(?:div|span|p)>',
    ))
    return (items, "baidu-result-blocks") if items else (dedupe(generic_anchor_fallback(html_text, base, "baidu", ("baidu.com",))), "generic-anchor-fallback")


def parse_sogou(html_text: str, base: str) -> Tuple[List[dict], str]:
    items = parse_blocks(html_text, base, "sogou", r'<div[^>]*class=["\'][^"\']*(?:vrwrap|rb)[^"\']*["\']', (
        r'class=["\'][^"\']*(?:text-layout|fz-mid|str_info|space-txt|str-text-info)[^"\']*["\'][^>]*>(.*?)</(?:div|span|p)>',
        r'class=["\'][^"\']*text-[^"\']*["\'][^>]*>(.*?)</(?:div|span|p)>',
    ))
    return (items, "sogou-result-blocks") if items else (dedupe(generic_anchor_fallback(html_text, base, "sogou", ("sogou.com",))), "generic-anchor-fallback")


def parse_weixin(html_text: str, base: str) -> Tuple[List[dict], str]:
    items = parse_blocks(html_text, base, "weixin", r'<li[^>]*(?:id=["\'][^"\']+["\']|class=["\'][^"\']*news[^"\']*["\'])', (
        r'class=["\'][^"\']*(?:txt-info|s-p)[^"\']*["\'][^>]*>(.*?)</p>',
        r'<p[^>]*>(.*?)</p>',
    ))
    return (items, "weixin-news-list") if items else (dedupe(generic_anchor_fallback(html_text, base, "weixin", ("sogou.com",))), "generic-anchor-fallback")


def parse_so(html_text: str, base: str) -> Tuple[List[dict], str]:
    items = parse_blocks(html_text, base, "so", r'<li[^>]*class=["\'][^"\']*res-list[^"\']*["\']', (
        r'class=["\'][^"\']*res-desc[^"\']*["\'][^>]*>(.*?)</p>',
        r'class=["\'][^"\']*res-desc[^"\']*["\'][^>]*>(.*?)</div>',
    ))
    return (items, "so-res-list") if items else (dedupe(generic_anchor_fallback(html_text, base, "so", ("so.com",))), "generic-anchor-fallback")


def parse_toutiao(html_text: str, base: str) -> Tuple[List[dict], str]:
    m = re.search(r"window\._SSR_HYDRATED_DATA\s*=\s*(\{.*?\});?\s*</script>", html_text, re.S)
    if not m: return [], "no-ssr-data"
    try: data = json.loads(m.group(1))
    except Exception: return [], "ssr-json-parse-failed"
    stack = [data]; items = []
    while stack and len(items) < 60:
        node = stack.pop()
        if isinstance(node, dict):
            title = node.get("title") or node.get("name") or node.get("display_name")
            url = node.get("url") or node.get("share_url") or node.get("display_url")
            summary = node.get("abstract") or node.get("description") or ""
            if isinstance(title, str) and isinstance(url, str) and url.startswith(("http://", "https://")):
                host = url_host(url)
                items.append({
                    "title": clean_text(title, 220), "url": norm_url(url, base), "redirect_url": None,
                    "url_state": "direct" if not is_search_domain(host) else "search-host-link",
                    "publisher_domain": host if host and not is_search_domain(host) else None,
                    "display_domain": host if host and not is_search_domain(host) else None,
                    "summary": clean_text(summary, 360), "summary_state": "extracted" if summary else "missing",
                    "ad_suspected": False, "engine": "toutiao",
                })
            stack.extend(node.values())
        elif isinstance(node, list): stack.extend(node)
    return dedupe(items), "toutiao-ssr-json"



def parse_bing(html_text: str, base: str) -> Tuple[List[dict], str]:
    items = parse_blocks(html_text, base, "bing", r'<li[^>]*class=["\'][^"\']*b_algo[^"\']*["\']', (
        r'class=["\'][^"\']*b_caption[^"\']*["\'][^>]*>.*?<p[^>]*>(.*?)</p>',
        r'<p[^>]*>(.*?)</p>',
    ))
    return (items, "bing-b_algo") if items else (
        dedupe(generic_anchor_fallback(html_text, base, "bing", ("bing.com",))),
        "generic-anchor-fallback",
    )


def parse_duckduckgo(html_text: str, base: str) -> Tuple[List[dict], str]:
    items = parse_blocks(html_text, base, "duckduckgo", r'<div[^>]*class=["\'][^"\']*result[^"\']*["\']', (
        r'class=["\'][^"\']*result__snippet[^"\']*["\'][^>]*>(.*?)</(?:a|div|span|p)>',
        r'class=["\'][^"\']*result__body[^"\']*["\'][^>]*>(.*?)</div>',
    ))
    return (items, "duckduckgo-result-blocks") if items else (
        dedupe(generic_anchor_fallback(html_text, base, "duckduckgo", ("duckduckgo.com",))),
        "generic-anchor-fallback",
    )


def parse_google(html_text: str, base: str) -> Tuple[List[dict], str]:
    # Google markup changes frequently. Prefer known result containers, then use
    # a conservative external-anchor fallback. /url?q=<target> is unwrapped by norm_url.
    items = parse_blocks(html_text, base, "google", r'<div[^>]*class=["\'][^"\']*(?:MjjYud|g)[^"\']*["\']', (
        r'class=["\'][^"\']*(?:VwiC3b|yXK7lf)[^"\']*["\'][^>]*>(.*?)</div>',
        r'<span[^>]*class=["\'][^"\']*(?:aCOpRe|st)[^"\']*["\'][^>]*>(.*?)</span>',
    ))
    return (items, "google-result-blocks") if items else (
        dedupe(generic_anchor_fallback(html_text, base, "google", ("google.com",))),
        "generic-anchor-fallback",
    )


def parse_brave(html_text: str, base: str) -> Tuple[List[dict], str]:
    items = parse_blocks(html_text, base, "brave", r'<div[^>]*class=["\'](?:[^"\']*\s)?(?:snippet|result)(?:\s[^"\']*)?["\']', (
        r'class=["\'][^"\']*(?:snippet-description|description)[^"\']*["\'][^>]*>(.*?)</(?:div|p|span)>',
        r'<p[^>]*>(.*?)</p>',
    ))
    return (items, "brave-result-blocks") if items else (
        dedupe(generic_anchor_fallback(html_text, base, "brave", ("brave.com",))),
        "generic-anchor-fallback",
    )


PARSERS = {
    "baidu": parse_baidu,
    "sogou": parse_sogou,
    "weixin": parse_weixin,
    "so": parse_so,
    "toutiao": parse_toutiao,
    "bing": parse_bing,
    "duckduckgo": parse_duckduckgo,
    "google": parse_google,
    "brave": parse_brave,
}



def load_meta(html_path: Path, name: str, explicit: Optional[str]) -> Tuple[dict, Optional[Path]]:
    candidates = []
    if explicit: candidates.append(resolve_skill_path(explicit))
    if html_path.parent.name == "raw": candidates.append(html_path.parent.parent / f"{name}.meta.json")
    for path in candidates:
        if path.exists():
            try: return json.loads(path.read_text(encoding="utf-8")), path
            except Exception: return {}, path
    return {}, None


def main() -> int:
    ap = argparse.ArgumentParser(description="全网搜索结果统一解析器")
    ap.add_argument("engine", choices=sorted(PARSERS))
    ap.add_argument("html_file")
    ap.add_argument("--name", default=None)
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--meta", default=None)
    ap.add_argument("--url", default=None)
    ap.add_argument("--query", default="", help="Original query for semantic/site-intent annotation")
    args = ap.parse_args()

    html_path = resolve_skill_path(args.html_file)
    if not html_path.exists(): emit_json({"ok": False, "error": f"file-not-found:{html_path}"}); return 2
    name = safe_name(args.name or html_path.stem)
    out_dir = resolve_out_dir(args.out_dir, html_path); out_dir.mkdir(parents=True, exist_ok=True)
    meta, meta_path = load_meta(html_path, name, args.meta)
    source_url = args.url or meta.get("final_url") or meta.get("request_url") or ""
    fetched_at = meta.get("fetched_at") or now_iso()
    html_text = html_path.read_text(encoding="utf-8", errors="replace")
    intent = parse_query_intent(args.query or "")

    warnings: List[str] = []
    blocked = bool(meta.get("blocked") or meta.get("captcha_detected")); low_signal = bool(meta.get("low_signal"))
    if blocked:
        raw_items, strategy = [], "blocked-by-fetch-metadata"; warnings.append("fetch metadata marks response blocked/captcha")
    else:
        if low_signal: warnings.append("known result container missing; parser/fallback attempted")
        raw_items, strategy = PARSERS[args.engine](html_text, source_url)
        if not raw_items: warnings.append("parser produced no structured result items")

    classified = [classify_item(x, intent) for x in raw_items]
    accepted = [x for x in classified if not x.get("excluded")]
    rejected = [x for x in classified if x.get("excluded")]

    items = []
    for rank, item in enumerate(accepted, 1):
        row = dict(item); row.update({
            "rank": rank, "discovered_via": args.engine, "source_url": source_url,
            "search_source_url": source_url, "fetched_at": fetched_at,
            "source_grade": "C", "verification_state": "search-snippet",
            "raw_html": str(html_path), "fetch_meta": str(meta_path) if meta_path else None,
        })
        items.append(row)

    result = {
        "meta": {
            "engine": args.engine, "query": args.query, "query_intent": intent.to_dict(),
            "input_file": str(html_path), "fetch_meta": str(meta_path) if meta_path else None,
            "source_url": source_url, "fetched_at": fetched_at, "strategy": strategy,
            "warnings": warnings, "raw_item_count": len(classified), "item_count": len(items),
            "rejected_item_count": len(rejected), "blocked": blocked, "low_signal": low_signal,
        },
        "items": items,
        "rejected_items": rejected,
    }
    out_file = out_dir / f"{name}.results.json"
    out_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    emit_json({
        "ok": bool(items), "engine": args.engine, "raw_item_count": len(classified), "item_count": len(items),
        "rejected_item_count": len(rejected), "strategy": strategy, "results_file": str(out_file), "warnings": warnings,
    })
    return 0 if items else 3


if __name__ == "__main__":
    sys.exit(main())
