#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Central search-engine catalog and routing helpers.

Language preference, geographic market and engine scope are separate concepts:
- language changes UI/result-language hints and engine order;
- market is optional and only applied when the caller explicitly requests it;
- scope (cn/global) is a discovery-routing label, not a network fault domain.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional
from urllib.parse import urlencode, urlsplit

ENGINE_DEFS: Dict[str, dict] = {
    "baidu": {"family": "baidu", "host_suffix": "baidu.com", "scope": "cn", "network_group": "baidu.com", "site_capable": True, "kind": "web"},
    "weixin": {"family": "sogou", "host_suffix": "sogou.com", "scope": "cn", "network_group": "sogou.com", "site_capable": False, "kind": "vertical"},
    "toutiao": {"family": "toutiao", "host_suffix": "toutiao.com", "scope": "cn", "network_group": "toutiao.com", "site_capable": False, "kind": "vertical"},
    "so": {"family": "360", "host_suffix": "so.com", "scope": "cn", "network_group": "so.com", "site_capable": True, "kind": "web"},
    "sogou": {"family": "sogou", "host_suffix": "sogou.com", "scope": "cn", "network_group": "sogou.com", "site_capable": True, "kind": "web"},
    "bing": {"family": "bing", "host_suffix": "bing.com", "scope": "global", "network_group": "bing.com", "site_capable": True, "kind": "web"},
    "duckduckgo": {"family": "duckduckgo", "host_suffix": "duckduckgo.com", "scope": "global", "network_group": "duckduckgo.com", "site_capable": True, "kind": "web"},
    "google": {"family": "google", "host_suffix": "google.com", "scope": "global", "network_group": "google.com", "site_capable": True, "kind": "web"},
    "brave": {"family": "brave", "host_suffix": "brave.com", "scope": "global", "network_group": "brave.com", "site_capable": True, "kind": "web"},
}

CN_ENGINES = ["baidu", "weixin", "toutiao", "so", "sogou"]
GLOBAL_ENGINES = ["bing", "duckduckgo", "google", "brave"]
ALL_INTERLEAVED = ["baidu", "bing", "weixin", "duckduckgo", "toutiao", "google", "so", "brave", "sogou"]
CN_FIRST = ["baidu", "bing", "weixin", "duckduckgo", "toutiao", "google", "so", "brave", "sogou"]
GLOBAL_FIRST = ["bing", "duckduckgo", "google", "brave", "baidu", "sogou", "so", "weixin", "toutiao"]


def engine_names() -> List[str]:
    return list(ENGINE_DEFS.keys())


def search_host_suffixes() -> tuple:
    suffixes = {x["host_suffix"] for x in ENGINE_DEFS.values()}
    suffixes.update({"google.com.hk", "google.co.jp", "google.co.uk", "google.co.in", "google.com.au", "google.ca", "google.de", "google.fr"})
    return tuple(sorted(suffixes))


def site_capable_engines() -> set:
    return {name for name, meta in ENGINE_DEFS.items() if meta.get("site_capable")}


def engine_family(engine: str) -> str:
    return str(ENGINE_DEFS[engine]["family"])


def engine_scope(engine: str) -> str:
    return str(ENGINE_DEFS[engine]["scope"])


def engine_network_group(engine: str) -> str:
    return str(ENGINE_DEFS[engine].get("network_group") or ENGINE_DEFS[engine]["host_suffix"])


def engine_host_suffix(engine: str) -> str:
    return str(ENGINE_DEFS[engine]["host_suffix"])


def engine_from_url(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    if host == "weixin.sogou.com":
        return "weixin"
    if host.startswith("google.") or ".google." in host:
        return "google"
    for name, meta in ENGINE_DEFS.items():
        suffix = str(meta["host_suffix"])
        if host == suffix or host.endswith("." + suffix):
            return name
    return "direct"


def _language_probe_text(query: str) -> str:
    text = query or ""
    # Domains/operators are control syntax, not natural-language evidence.
    text = re.sub(r"\b(?:site|filetype|inurl|intitle|intext)\s*:\s*\S+", " ", text, flags=re.I)
    text = re.sub(r"https?://\S+", " ", text, flags=re.I)
    return text


def query_language_hint(query: str) -> str:
    text = _language_probe_text(query)
    has_cjk = bool(re.search(r"[\u3400-\u9fff]", text))
    has_latin = bool(re.search(r"[A-Za-z]", text))
    if has_cjk and has_latin:
        return "mixed"
    if has_cjk:
        return "cjk"
    if has_latin:
        return "latin"
    return "mixed"


def _bing_market(market: str, lang: str) -> Optional[str]:
    market = (market or "").lower()
    mapping = {
        "cn": "zh-CN", "us": "en-US", "gb": "en-GB", "uk": "en-GB",
        "jp": "ja-JP", "de": "de-DE", "fr": "fr-FR", "in": "en-IN",
        "au": "en-AU", "ca": "en-CA",
    }
    return mapping.get(market)


def _ddg_market(market: str, lang: str) -> Optional[str]:
    market = (market or "").lower()
    mapping = {
        "cn": "cn-zh", "us": "us-en", "gb": "uk-en", "uk": "uk-en",
        "jp": "jp-jp", "de": "de-de", "fr": "fr-fr", "in": "in-en",
        "au": "au-en", "ca": "ca-en",
    }
    return mapping.get(market)


def make_search_url(engine: str, query: str, market: Optional[str] = None) -> str:
    hint = query_language_hint(query)
    market = (market or "none").lower()
    if market in ("none", "auto", "global", "worldwide"):
        market = ""
    if engine == "baidu":
        return "https://www.baidu.com/s?" + urlencode({"wd": query})
    if engine == "weixin":
        return "https://weixin.sogou.com/weixin?" + urlencode({"type": "2", "query": query})
    if engine == "sogou":
        return "https://www.sogou.com/web?" + urlencode({"query": query})
    if engine == "so":
        return "https://www.so.com/s?" + urlencode({"q": query})
    if engine == "toutiao":
        return "https://so.toutiao.com/search?" + urlencode({"keyword": query})
    if engine == "bing":
        params = {"q": query}
        if hint == "latin":
            params["setlang"] = "en"
        elif hint == "cjk":
            params["setlang"] = "zh-hans"
        mkt = _bing_market(market, hint) if market else None
        if mkt:
            params["mkt"] = mkt
        return "https://www.bing.com/search?" + urlencode(params)
    if engine == "duckduckgo":
        params = {"q": query}
        kl = _ddg_market(market, hint) if market else None
        if kl:
            params["kl"] = kl
        return "https://html.duckduckgo.com/html/?" + urlencode(params)
    if engine == "google":
        params = {"q": query, "num": "10"}
        if hint == "latin":
            params["hl"] = "en"
        elif hint == "cjk":
            params["hl"] = "zh-CN"
        if market:
            params["gl"] = market
        return "https://www.google.com/search?" + urlencode(params)
    if engine == "brave":
        return "https://search.brave.com/search?" + urlencode({"q": query, "source": "web"})
    raise ValueError("unsupported engine: %s" % engine)


def default_engine_order(query: str, profile: str = "auto") -> List[str]:
    profile = (profile or "auto").lower()
    if profile == "cn":
        return list(CN_ENGINES)
    if profile == "global":
        return list(GLOBAL_ENGINES)
    if profile == "all":
        return list(ALL_INTERLEAVED)
    if profile != "auto":
        raise ValueError("unsupported profile: %s" % profile)
    hint = query_language_hint(query)
    if hint == "cjk":
        return list(CN_FIRST)
    if hint == "latin":
        return list(GLOBAL_FIRST)
    return list(ALL_INTERLEAVED)
