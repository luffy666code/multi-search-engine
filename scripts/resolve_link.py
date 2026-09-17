#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Best-effort resolver for one opaque search-engine redirect link.

It is intentionally not run against every result by default.  Resolve only
shortlisted candidates to avoid extra anti-bot pressure.  Failure is explicit
and never upgrades evidence by itself.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from urllib.parse import urljoin, urlparse

from runtime import build_ssl_context, is_cert_verification_error
from engine_catalog import search_host_suffixes
from url_safety import URLSafetyError, URLResolutionError, assert_url_safe

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Connection": "close",
}
SEARCH_SUFFIXES = search_host_suffixes()


def emit(obj):
    print(json.dumps(obj, ensure_ascii=True, separators=(",", ":")))


def is_search_host(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == s or host.endswith("." + s) for s in SEARCH_SUFFIXES)


class Trace(urllib.request.HTTPRedirectHandler):
    def __init__(self, allow_private_network=False):
        super().__init__()
        self.history = []
        self.allow_private_network = bool(allow_private_network)
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        assert_url_safe(newurl, self.allow_private_network)
        self.history.append({"status": int(code), "from_url": req.full_url, "to_url": newurl})
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--referer", default=None)
    ap.add_argument("--timeout", type=float, default=12.0)
    ap.add_argument("--max-bytes", type=int, default=128000)
    ap.add_argument("--ca-bundle", default=None)
    ap.add_argument("--insecure", action="store_true")
    ap.add_argument("--allow-insecure-fallback", action="store_true")
    ap.add_argument("--allow-private-network", action="store_true",
                    help="Allow loopback/private/link-local targets and redirects; disabled by default")
    args = ap.parse_args()

    headers = dict(HEADERS)
    if args.referer:
        headers["Referer"] = args.referer
    try:
        assert_url_safe(args.url, args.allow_private_network)
        tracer = Trace(args.allow_private_network)
        context, tls_meta = build_ssl_context(args.insecure, args.ca_bundle)
        opener = urllib.request.build_opener(tracer, urllib.request.HTTPSHandler(context=context))
        req = urllib.request.Request(args.url, headers=headers)
        try:
            resp = opener.open(req, timeout=args.timeout)
        except urllib.error.HTTPError as exc:
            resp = exc
        except urllib.error.URLError as exc:
            msg = "URLError: %s" % exc.reason
            if (not args.insecure and args.allow_insecure_fallback and is_cert_verification_error(msg)):
                context, tls_meta = build_ssl_context(True, None)
                tls_meta["tls_fallback_used"] = True
                tracer = Trace(args.allow_private_network)
                opener = urllib.request.build_opener(tracer, urllib.request.HTTPSHandler(context=context))
                try:
                    resp = opener.open(req, timeout=args.timeout)
                except urllib.error.HTTPError as http_exc:
                    resp = http_exc
            else:
                raise
        body = resp.read(args.max_bytes)
        final_url = resp.geturl()
        ctype = resp.headers.get("Content-Type", "")
        text = body.decode("utf-8", errors="ignore") if "text" in ctype or "html" in ctype else ""

        # Some redirectors finish with an HTML/meta/js jump instead of HTTP Location.
        if is_search_host(final_url) and text:
            candidates = []
            for pat in (
                r'<meta[^>]+http-equiv=["\']?refresh["\']?[^>]+content=["\'][^"\']*url=([^"\'>]+)',
                r'(?:location\.href|location\.replace|window\.location)\s*(?:=|\()\s*["\']([^"\']+)',
            ):
                m = re.search(pat, text, re.I)
                if m:
                    candidates.append(urljoin(final_url, m.group(1).strip()))
            for cand in candidates:
                if cand.startswith(("http://", "https://")) and not is_search_host(cand):
                    assert_url_safe(cand, args.allow_private_network)
                    final_url = cand
                    break

        direct = bool(final_url and not is_search_host(final_url))
        emit({
            "ok": direct,
            "input_url": args.url,
            "final_url": final_url,
            "direct_external": direct,
            "status": int(getattr(resp, "status", getattr(resp, "code", 0)) or 0),
            "history": tracer.history,
            "tls_mode": tls_meta.get("tls_mode"),
            "tls_verified": tls_meta.get("tls_verified"),
            "tls_fallback_used": bool(tls_meta.get("tls_fallback_used")),
            "ca_source": tls_meta.get("ca_source"),
        })
        return 0 if direct else 4
    except Exception as exc:
        emit({"ok": False, "input_url": args.url, "error": f"{type(exc).__name__}: {exc}"})
        return 2


if __name__ == "__main__":
    sys.exit(main())
