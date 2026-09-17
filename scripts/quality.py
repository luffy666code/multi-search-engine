#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared semantic-quality helpers for the global multi-search skill.

This module has no third-party dependencies. It separates transport success
from semantic success so a search page that returned HTTP 200 but ignored the
query (especially site:) cannot be marked useful silently.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, asdict
from typing import Dict, Iterable, List, Optional
from urllib.parse import urlparse

from engine_catalog import search_host_suffixes

SEARCH_HOST_SUFFIXES = search_host_suffixes()

# Exact/near-exact navigation, legal and pagination labels observed on search pages.
NOISE_TITLE_EXACT = {
    "首页", "百度首页", "搜狗首页", "360搜索", "bing", "google", "duckduckgo", "brave",
    "帮助", "问题帮助", "help", "feedback", "反馈", "意见反馈", "意见反馈及投诉",
    "免责声明", "隐私政策", "privacy", "terms", "用户协议", "提交网址", "网站提交", "更多",
    "上一页", "下一页", "下一页>", "previous", "next", "登录", "注册", "sign in", "搜索", "站内搜索",
    "images", "videos", "maps", "shopping", "settings", "preferences", "log in", "login",
    "sign up", "advanced search",
}
NOISE_TITLE_PATTERNS = (
    re.compile(r"^\d{1,3}$"),
    re.compile(r"^(?:第\s*)?\d+\s*页$"),
    re.compile(r"^(?:百度|搜狗|360|bing|google|duckduckgo|brave).*(?:帮助|反馈|提交网址|隐私|协议|help|privacy|terms)$", re.I),
)
NOISE_HOSTS = {
    "help.baidu.com", "fankui.sogou.com", "corp.sogou.com",
    "support.google.com", "policies.google.com", "privacy.microsoft.com",
}
NOISE_PATH_FRAGMENTS = (
    "/sitesubmit", "/docs/terms", "/private", "/help", "/feedback", "/antispider/",
    "/aclk", "/setprefs",
)

# Common weak/generic titles.  These are not always deleted on title alone; they
# become noise when they also fail query intent matching.
GENERIC_TITLE_MARKERS = (
    "智能报告生成系统", "研究报告 ai 工具", "aiwork365",
    "行业报告_市场报告_投资报告", "行业报告 市场报告 投资报告",
)

SITE_RE = re.compile(r"(?:^|\s)site\s*:\s*([A-Za-z0-9.-]+\.[A-Za-z]{2,})(?=\s|$)", re.I)
OPERATOR_RE = re.compile(r"\b(?:site|filetype|inurl|intitle|intext)\s*:\s*\S+", re.I)
QUOTED_RE = re.compile(r'["“”]([^"“”]{2,80})["“”]')


@dataclass
class QueryIntent:
    raw_query: str
    site_domain: Optional[str]
    terms: List[str]
    quoted_terms: List[str]

    @property
    def is_site_query(self) -> bool:
        return bool(self.site_domain)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["is_site_query"] = self.is_site_query
        return data


def normalize_domain(value: Optional[str]) -> str:
    if not value:
        return ""
    value = value.strip().lower().rstrip(".")
    if "://" in value:
        value = (urlparse(value).hostname or "").lower()
    if value.startswith("www."):
        value = value[4:]
    return value


def domain_matches(host: Optional[str], target: Optional[str]) -> bool:
    h = normalize_domain(host)
    t = normalize_domain(target)
    return bool(h and t and (h == t or h.endswith("." + t)))


def parse_query_intent(query: str) -> QueryIntent:
    m = SITE_RE.search(query or "")
    site_domain = normalize_domain(m.group(1)) if m else None
    quoted = [x.strip() for x in QUOTED_RE.findall(query or "") if x.strip()]
    cleaned = SITE_RE.sub(" ", query or "")
    cleaned = OPERATOR_RE.sub(" ", cleaned)
    cleaned = re.sub(r'["“”()（）\[\]{}，,。；;：:!?！？+|]', " ", cleaned)
    parts = [x.strip() for x in re.split(r"\s+", cleaned) if x.strip()]

    stop = {
        "的", "和", "或", "与", "及", "最新", "相关", "一下", "看看", "搜索", "查询",
        "the", "and", "or", "latest", "search", "find", "about",
    }
    terms: List[str] = []
    for term in quoted + parts:
        term = term.strip("-_")
        if not term or term.lower() in stop:
            continue
        # Single Chinese characters are too noisy.  One-character latin/digits are kept
        # only when they came from quoted text, which is rare and intentional.
        if len(term) == 1 and re.search(r"[\u4e00-\u9fff]", term):
            continue
        if term not in terms:
            terms.append(term)
    return QueryIntent(raw_query=query, site_domain=site_domain, terms=terms, quoted_terms=quoted)


def url_host(url: Optional[str]) -> str:
    if not url:
        return ""
    try:
        return normalize_domain(urlparse(url).hostname or "")
    except Exception:
        return ""


def is_search_host(host: str) -> bool:
    h = normalize_domain(host)
    return any(h == suffix or h.endswith("." + suffix) for suffix in SEARCH_HOST_SUFFIXES)


def is_opaque_redirect(url: Optional[str]) -> bool:
    if not url:
        return False
    p = urlparse(url)
    host = normalize_domain(p.hostname or "")
    path = p.path.lower()
    if domain_matches(host, "baidu.com") and path in ("/link", "/baidu.php"):
        return True
    if domain_matches(host, "sogou.com") and path.startswith("/link"):
        return True
    if domain_matches(host, "so.com") and ("link" in path or "jump" in path):
        return True
    if domain_matches(host, "bing.com") and path.startswith("/ck/a"):
        return True
    if domain_matches(host, "duckduckgo.com") and path.startswith("/l/"):
        return True
    if domain_matches(host, "google.com") and path == "/url":
        return True
    return False


def normalize_title(title: str) -> str:
    s = (title or "").lower()
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", s)
    return s


def stable_title_hash(title: str) -> str:
    return hashlib.sha1(normalize_title(title).encode("utf-8")).hexdigest()[:16]


def obvious_noise_reasons(item: dict) -> List[str]:
    title = (item.get("title") or "").strip()
    url = item.get("url") or ""
    redirect_url = item.get("redirect_url") or ""
    host = url_host(url) or url_host(redirect_url)
    path = (urlparse(url or redirect_url).path or "").lower() if (url or redirect_url) else ""
    reasons: List[str] = []

    if not title:
        reasons.append("empty-title")
    if title in NOISE_TITLE_EXACT:
        reasons.append("navigation-title")
    for pat in NOISE_TITLE_PATTERNS:
        if pat.search(title):
            reasons.append("pagination-or-navigation-title")
            break
    if host in NOISE_HOSTS:
        reasons.append("legal-or-feedback-host")
    if any(fragment in path for fragment in NOISE_PATH_FRAGMENTS):
        reasons.append("legal-navigation-path")
    if item.get("ad_suspected"):
        reasons.append("advertisement")
    return reasons


def match_intent(item: dict, intent: QueryIntent) -> Dict[str, object]:
    title = (item.get("title") or "").lower()
    summary = (item.get("summary") or "").lower()
    publisher_domain = normalize_domain(item.get("publisher_domain"))
    display_domain = normalize_domain(item.get("display_domain"))
    host = publisher_domain or display_domain or url_host(item.get("url"))
    text = f"{title}\n{summary}\n{publisher_domain}\n{display_domain}".lower()

    matched_terms = [t for t in intent.terms if t.lower() in text]
    total = max(1, len(intent.terms))
    term_ratio = len(matched_terms) / total if intent.terms else 1.0
    site_match = domain_matches(host, intent.site_domain) if intent.site_domain else None

    # Query intent labels are deliberately lexical and conservative.  They do not
    # pretend to be semantic embeddings; "unknown" means the snippet is too weak.
    if intent.site_domain:
        if site_match and (term_ratio > 0 or not intent.terms):
            status = "matched"
        elif site_match:
            status = "site-only"
        elif not host and item.get("url_state") == "opaque-redirect":
            status = "site-unverified"
        else:
            status = "mismatch"
    else:
        if term_ratio >= 0.5 or (len(matched_terms) >= 1 and len(intent.terms) <= 2):
            status = "matched"
        elif matched_terms:
            status = "partial"
        elif not summary and len(title) < 6:
            status = "unknown"
        else:
            status = "mismatch"

    return {
        "intent_status": status,
        "matched_terms": matched_terms,
        "term_match_ratio": round(term_ratio, 3),
        "site_target": intent.site_domain,
        "site_target_match": site_match,
    }


def classify_item(item: dict, intent: QueryIntent) -> dict:
    out = dict(item)
    out.update(match_intent(out, intent))
    reasons = obvious_noise_reasons(out)
    title_l = (out.get("title") or "").lower()
    if any(marker in title_l for marker in GENERIC_TITLE_MARKERS) and out["intent_status"] in ("mismatch", "unknown"):
        reasons.append("generic-tool-or-directory-noise")

    if intent.site_domain and out["intent_status"] == "mismatch":
        reasons.append("site-constraint-mismatch")

    out["excluded"] = bool(reasons)
    out["exclude_reasons"] = sorted(set(reasons))
    return out


def canonical_url_key(item: dict) -> str:
    """Stable key for a confirmed direct publisher URL; empty for opaque/search links."""
    if item.get("url_state") not in ("direct", "extracted-direct", "resolved-direct"):
        return ""
    url = item.get("url") or ""
    try:
        p = urlparse(url)
    except Exception:
        return ""
    host = normalize_domain(p.hostname or "")
    if not host or is_search_host(host):
        return ""
    path = re.sub(r"/{2,}", "/", p.path or "/").rstrip("/") or "/"
    query = p.query or ""
    return "%s%s%s" % (host, path, ("?" + query) if query else "")


def result_quality_score(item: dict) -> int:
    """Transparent ranking score for discovery candidates, not a truth/credibility score."""
    if item.get("excluded"):
        return -100
    score = 0
    status = item.get("intent_status")
    if status == "matched": score += 3
    elif status == "site-only": score += 2
    elif status == "partial": score += 1
    if item.get("site_target_match") is True: score += 3
    state = item.get("url_state")
    if state in ("resolved-direct", "extracted-direct"): score += 3
    elif state == "direct": score += 2
    elif state == "opaque-redirect": score -= 2
    elif state == "search-host-link": score -= 3
    summary = (item.get("summary") or "").strip()
    if summary:
        score += 1
        if item.get("summary_state") == "extracted": score += 1
    vias = [x for x in (item.get("discovered_via_all") or []) if x]
    if len(set(vias)) >= 2: score += 1
    return score


def publisher_key(item: dict) -> str:
    domain = normalize_domain(item.get("publisher_domain") or item.get("display_domain"))
    title_hash = stable_title_hash(item.get("title") or "")
    if domain and not is_search_host(domain):
        return f"{domain}|{title_hash}"
    return f"title|{title_hash}"


def evaluate_engine(items: Iterable[dict], intent: QueryIntent, min_results: int = 2) -> dict:
    rows = list(items)
    valid = [x for x in rows if not x.get("excluded")]
    matched = [x for x in valid if x.get("intent_status") in ("matched", "partial", "site-only")]
    site_matched = [x for x in valid if x.get("site_target_match") is True]
    summaries = [x for x in valid if (x.get("summary") or "").strip()]
    direct_urls = [x for x in valid if x.get("url_state") in ("direct", "extracted-direct", "resolved-direct")]
    opaque = [x for x in valid if x.get("url_state") == "opaque-redirect"]

    valid_count = len(valid)
    raw_count = len(rows)
    summary_coverage = len(summaries) / valid_count if valid_count else 0.0
    direct_url_coverage = len(direct_urls) / valid_count if valid_count else 0.0
    noise_rate = (raw_count - valid_count) / raw_count if raw_count else 0.0

    if not raw_count:
        semantic_status = "parse-empty"
        semantic_usable = False
    elif intent.site_domain and not site_matched:
        # Critical: HTTP/parse success is not semantic success when site: was ignored.
        unverified = any(x.get("intent_status") == "site-unverified" for x in valid)
        semantic_status = "site-constraint-unverified" if unverified else "intent-drift"
        semantic_usable = False
    elif len(matched) < max(1, min_results if not intent.site_domain else 1):
        semantic_status = "low-relevance"
        semantic_usable = False
    elif noise_rate >= 0.70:
        semantic_status = "too-noisy"
        semantic_usable = False
    elif summary_coverage < 0.25:
        semantic_status = "degraded-summary"
        semantic_usable = True
    elif direct_url_coverage < 0.25 and opaque:
        semantic_status = "degraded-links"
        semantic_usable = True
    else:
        semantic_status = "useful"
        semantic_usable = True

    domains = sorted({normalize_domain(x.get("publisher_domain") or x.get("display_domain")) for x in valid if normalize_domain(x.get("publisher_domain") or x.get("display_domain"))})
    return {
        "raw_item_count": raw_count,
        "valid_item_count": valid_count,
        "relevant_item_count": len(matched),
        "site_match_count": len(site_matched),
        "excluded_item_count": raw_count - valid_count,
        "summary_coverage": round(summary_coverage, 3),
        "direct_url_coverage": round(direct_url_coverage, 3),
        "opaque_redirect_count": len(opaque),
        "noise_rate": round(noise_rate, 3),
        "publisher_domains": domains,
        "semantic_status": semantic_status,
        "semantic_usable": semantic_usable,
    }
