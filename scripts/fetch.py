#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Low-level HTTP fetcher for the global multi-search skill.

Design goals:
- zero mandatory third-party dependencies
- arbitrary HTTP/HTTPS websites are allowed
- paths are anchored to the skill root, never to the caller CWD
- stdout is ASCII-only JSON so console code pages cannot corrupt control output
- body bytes, decoded text and metadata are always persisted, including failures
- anti-bot detection is structural and conservative
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote, urlsplit, urlunsplit

from runtime import build_ssl_context, environment_report, is_cert_verification_error
from engine_catalog import engine_from_url, engine_names
from url_safety import URLSafetyError, URLResolutionError, assert_url_safe

SKILL_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = SKILL_ROOT / "temp_search"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    # Ask for identity so the saved bytes normally match the response body directly.
    # Some servers may ignore this; gzip is still handled below.
    "Accept-Encoding": "identity",
    "Connection": "close",
}

# Search-engine host mapping lives in engine_catalog.py. Direct arbitrary URLs
# remain supported via engine=direct.
CAPTCHA_RULES = {
    "baidu": {
        "url_patterns": ("wappass.baidu.com", "/antispider/"),
        "text_patterns": ("百度安全验证", "请输入验证码", "网络不给力，请稍后重试"),
        "result_marker": r'class=["\'][^"\']*result',
    },
    "sogou": {
        "url_patterns": ("/antispider/",),
        "text_patterns": ("搜狗验证", "请输入验证码"),
        "result_marker": r'class=["\'][^"\']*(?:vrwrap|rb|vrTitle)',
    },
    "weixin": {
        "url_patterns": ("/antispider/",),
        "text_patterns": ("搜狗验证", "请输入验证码"),
        "result_marker": r'class=["\'][^"\']*(?:news-list|txt-box)',
    },
    "so": {
        "url_patterns": ("/antispider/", "/security/"),
        "text_patterns": ("访问验证", "安全验证", "请输入验证码"),
        "result_marker": r'class=["\'][^"\']*res-list',
    },
    "toutiao": {
        "url_patterns": ("/verify", "/captcha"),
        "text_patterns": ("验证码", "安全验证"),
        "result_marker": r'(?:_SSR_HYDRATED_DATA|class=["\'][^"\']*result)',
    },
    "bing": {
        "url_patterns": ("/challenge", "/captcha"),
        "text_patterns": ("One last step", "verify you are human", "unusual traffic"),
        "result_marker": r'class=["\'][^"\']*b_algo',
    },
    "duckduckgo": {
        "url_patterns": ("/challenge", "/captcha"),
        "text_patterns": ("Unfortunately, bots use DuckDuckGo too", "verify you are human"),
        "result_marker": r'class=["\'][^"\']*result(?:__a|s_links| )',
    },
    "google": {
        "url_patterns": ("/sorry/",),
        "text_patterns": ("unusual traffic from your computer network", "Before you continue to Google"),
        "result_marker": r'(?:class=["\'][^"\']*MjjYud|<h3\b)',
    },
    "brave": {
        "url_patterns": ("/challenge", "/captcha"),
        "text_patterns": ("verify you are human", "unusual traffic"),
        "result_marker": r'class=["\'][^"\']*(?:snippet|result)',
    },
}


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def emit_json(obj: dict) -> None:
    """ASCII-only stdout avoids Windows console-codepage corruption."""
    print(json.dumps(obj, ensure_ascii=True, separators=(",", ":")))


def safe_name(value: str) -> str:
    value = re.sub(r"[^0-9A-Za-z._-]+", "_", value or "").strip("._")
    if not value:
        raise ValueError("name is empty after sanitization")
    return value[:96]


def resolve_under_skill(value: Optional[str], default: Path) -> Path:
    if not value:
        return default.resolve()
    p = Path(value)
    if not p.is_absolute():
        p = SKILL_ROOT / p
    return p.resolve()


def infer_engine(url: str) -> str:
    return engine_from_url(url)


def quote_url(url: str) -> str:
    parts = urlsplit(url)
    path = quote(parts.path, safe="/%:@&=+$,;~*'()!-._")
    query = quote(parts.query, safe="=&?/%:@+$,;~*'()!-._")
    return urlunsplit((parts.scheme, parts.netloc, path, query, parts.fragment))


class TraceRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, allow_private_network: bool = False) -> None:
        super().__init__()
        self.history: List[dict] = []
        self.allow_private_network = bool(allow_private_network)

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        assert_url_safe(newurl, self.allow_private_network)
        self.history.append({"status": int(code), "from_url": req.full_url, "to_url": newurl})
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def decode_bytes(body: bytes, content_type: str) -> Tuple[str, str, bool]:
    """Return (text, encoding, used_replacement)."""
    candidates: List[str] = []
    m = re.search(r"charset\s*=\s*[\"']?([\w.-]+)", content_type or "", re.I)
    if m:
        candidates.append(m.group(1))

    head = body[:8192].decode("ascii", errors="ignore")
    for pattern in (
        r'<meta[^>]+charset\s*=\s*["\']?([\w.-]+)',
        r'<meta[^>]+content=["\'][^"\']*charset=([\w.-]+)',
    ):
        m = re.search(pattern, head, re.I)
        if m:
            candidates.append(m.group(1))

    candidates.extend(["utf-8", "gb18030", "gbk"])
    seen = set()
    for enc in candidates:
        if not enc:
            continue
        enc_l = enc.lower()
        if enc_l in seen:
            continue
        seen.add(enc_l)
        try:
            return body.decode(enc), enc, False
        except (LookupError, UnicodeDecodeError):
            pass
    return body.decode("utf-8", errors="replace"), "utf-8", True


def detect_captcha(final_url: str, text: str, engine: str) -> dict:
    rules = CAPTCHA_RULES.get(engine)
    if not rules:
        return {
            "captcha_detected": False,
            "captcha_hits": [],
            "result_marker_present": None,
            "low_signal": False,
        }

    url_hits = [p for p in rules["url_patterns"] if p in (final_url or "")]
    head = text[:300_000]
    text_hits = [p for p in rules["text_patterns"] if p in head]
    marker_present = bool(re.search(rules["result_marker"], text, re.I | re.S))

    # Strong URL evidence is sufficient. Text alone is only treated as captcha if
    # the normal result container is also missing; this avoids JS-string false positives.
    captcha = bool(url_hits) or (bool(text_hits) and not marker_present)
    low_signal = (not captcha) and (not marker_present)
    return {
        "captcha_detected": captcha,
        "captcha_hits": [f"url:{x}" for x in url_hits] + [f"text:{x}" for x in text_hits],
        "result_marker_present": marker_present,
        "low_signal": low_signal,
    }


def read_response(resp, max_bytes: int) -> Tuple[bytes, bool]:
    wire = resp.read(max_bytes + 1)
    truncated = len(wire) > max_bytes
    if truncated:
        wire = wire[:max_bytes]
    return wire, truncated


def fetch_once(url: str, timeout: float, max_bytes: int, ssl_context, headers: Optional[dict] = None, allow_private_network: bool = False) -> Tuple[object, bytes, bool, TraceRedirect]:
    assert_url_safe(url, allow_private_network)
    tracer = TraceRedirect(allow_private_network)
    handlers = [tracer]
    if ssl_context is not None:
        handlers.append(urllib.request.HTTPSHandler(context=ssl_context))
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(quote_url(url), headers=headers or DEFAULT_HEADERS)
    try:
        resp = opener.open(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        # HTTP 4xx/5xx still has a response body and must be persisted.
        resp = exc
    except (URLSafetyError, URLResolutionError):
        raise
    except urllib.error.URLError as exc:
        raise ConnectionError(f"URLError: {exc.reason}") from exc
    except OSError as exc:
        raise ConnectionError(f"OSError: {exc}") from exc

    wire, truncated = read_response(resp, max_bytes)
    return resp, wire, truncated, tracer


def persist_failure_files(raw_dir: Path, name: str) -> Tuple[Path, Path]:
    html_path = raw_dir / f"{name}.html"
    raw_path = raw_dir / f"{name}.body.raw"
    if not html_path.exists():
        html_path.write_text("", encoding="utf-8")
    if not raw_path.exists():
        raw_path.write_bytes(b"")
    return html_path, raw_path


def main() -> int:
    ap = argparse.ArgumentParser(description="全网检索低层抓取器（零强制第三方依赖）")
    ap.add_argument("url")
    ap.add_argument("--engine", default=None, choices=sorted(engine_names() + ["direct"]))
    ap.add_argument("--name", default=None)
    ap.add_argument("--out-dir", default=None, help="Relative paths are anchored to the skill root")
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--max-bytes", type=int, default=8_000_000)
    ap.add_argument("--retries", type=int, default=1, help="Network-level retries only")
    ap.add_argument("--backoff", type=float, default=5.0, help="Base seconds between network retries")
    ap.add_argument("--ca-bundle", default=None, help="PEM CA bundle. Relative paths are anchored to the skill root")
    ap.add_argument("--insecure", action="store_true", help="Disable TLS certificate verification (explicit opt-in)")
    ap.add_argument("--allow-insecure-fallback", action="store_true",
                    help="If strict TLS fails only because certificate verification is unavailable, retry once without verification")
    ap.add_argument("--accept-language", default=None,
                    help="Override Accept-Language header, e.g. 'en-US,en;q=0.9' for latin queries")
    ap.add_argument("--allow-private-network", action="store_true",
                    help="Allow loopback/private/link-local targets and redirects. Disabled by default to reduce SSRF risk")
    args = ap.parse_args()

    request_headers = dict(DEFAULT_HEADERS)
    if args.accept_language:
        request_headers["Accept-Language"] = args.accept_language

    engine = args.engine or infer_engine(args.url)
    out_dir = resolve_under_skill(args.out_dir, DEFAULT_OUT)
    raw_dir = out_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    if args.name:
        name = safe_name(args.name)
    else:
        idx = 1
        while (out_dir / f"query-{idx:02d}.meta.json").exists():
            idx += 1
        name = f"query-{idx:02d}"

    meta: Dict[str, object] = {
        "request_url": args.url,
        "engine": engine,
        "name": name,
        "fetched_at": now_iso(),
        "status": None,
        "final_url": None,
        "history": [],
        "content_type": None,
        "content_encoding": None,
        "captcha_detected": False,
        "captcha_hits": [],
        "result_marker_present": None,
        "low_signal": False,
        "blocked": False,
        "blocked_reason": None,
        "bytes_read_wire": 0,
        "bytes_decoded_body": 0,
        "truncated": False,
        "encoding": None,
        "decode_replacement_used": False,
        "body_sha256": None,
        "text_sha256": None,
        "saved_html": None,
        "saved_raw": None,
        "error": None,
        "runtime": environment_report(args.ca_bundle),
        "tls_mode": None,
        "tls_verified": None,
        "tls_fallback_used": False,
        "ca_bundle": None,
        "ca_source": None,
        "security_warning": None,
        "url_safety": None,
        "host_redirected": False,
        "redirect_from_host": None,
        "redirect_to_host": None,
        "geo_localized": False,
        "geo_redirect": False,
    }

    resp = None
    wire = b""
    truncated = False
    tracer: Optional[TraceRedirect] = None
    last_error: Optional[str] = None

    try:
        meta["url_safety"] = assert_url_safe(args.url, args.allow_private_network)
    except URLResolutionError as exc:
        meta["error"] = "dns-resolution: %s" % exc
        html_path, raw_path = persist_failure_files(raw_dir, name)
        meta["saved_html"] = str(html_path)
        meta["saved_raw"] = str(raw_path)
        meta_path = out_dir / (name + ".meta.json")
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        emit_json(meta)
        return 2
    except URLSafetyError as exc:
        meta["error"] = "url-safety: %s" % exc
        html_path, raw_path = persist_failure_files(raw_dir, name)
        meta["saved_html"] = str(html_path)
        meta["saved_raw"] = str(raw_path)
        meta_path = out_dir / (name + ".meta.json")
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        emit_json(meta)
        return 2

    try:
        ssl_context, tls_meta = build_ssl_context(args.insecure, args.ca_bundle)
    except Exception as exc:
        meta["error"] = "ssl-context: %s: %s" % (type(exc).__name__, exc)
        html_path, raw_path = persist_failure_files(raw_dir, name)
        meta["saved_html"] = str(html_path)
        meta["saved_raw"] = str(raw_path)
        meta_path = out_dir / (name + ".meta.json")
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        emit_json(meta)
        return 2

    meta.update(tls_meta)

    for attempt in range(max(0, args.retries) + 1):
        try:
            resp, wire, truncated, tracer = fetch_once(args.url, args.timeout, args.max_bytes, ssl_context, request_headers, args.allow_private_network)
            break
        except URLResolutionError as exc:
            last_error = "DNS resolution failed: %s" % exc
            break
        except URLSafetyError as exc:
            last_error = "URL safety blocked redirect: %s" % exc
            break
        except ConnectionError as exc:
            last_error = str(exc)
            if attempt < args.retries:
                time.sleep(args.backoff * (3 ** attempt))

    # Explicitly requested security fallback. Never silently downgrade TLS.
    if (resp is None and not args.insecure and args.allow_insecure_fallback
            and is_cert_verification_error(last_error or "")):
        try:
            ssl_context, tls_meta = build_ssl_context(True, None)
            meta.update(tls_meta)
            meta["tls_fallback_used"] = True
            meta["security_warning"] = "TLS certificate verification disabled after certificate verification failure"
            resp, wire, truncated, tracer = fetch_once(args.url, args.timeout, args.max_bytes, ssl_context, request_headers, args.allow_private_network)
            last_error = None
        except URLResolutionError as exc:
            last_error = "DNS resolution failed: %s" % exc
        except URLSafetyError as exc:
            last_error = "URL safety blocked redirect: %s" % exc
        except ConnectionError as exc:
            last_error = str(exc)

    if resp is None:
        meta["error"] = last_error or "unknown network failure"
        html_path, raw_path = persist_failure_files(raw_dir, name)
        meta["saved_html"] = str(html_path)
        meta["saved_raw"] = str(raw_path)
        meta_path = out_dir / f"{name}.meta.json"
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        emit_json(meta)
        return 2

    meta["status"] = int(getattr(resp, "status", getattr(resp, "code", 0)) or 0)
    meta["final_url"] = resp.geturl() if hasattr(resp, "geturl") else args.url
    meta["history"] = tracer.history if tracer else []
    req_host = (urlsplit(args.url).hostname or "").lower()
    final_host = (urlsplit(meta["final_url"] or "").hostname or "").lower()
    host_redirected = bool(req_host and final_host and req_host != final_host)
    meta["host_redirected"] = host_redirected
    meta["redirect_from_host"] = req_host or None
    meta["redirect_to_host"] = final_host or None
    geo_localized = False
    if host_redirected:
        # Only mark known geographic host localization, not every cross-host redirect.
        if req_host.endswith("bing.com") and final_host.endswith("bing.com"):
            prefix = final_host[:-len("bing.com")].rstrip(".")
            geo_localized = bool(prefix and prefix not in ("www", "search"))
        elif "google." in req_host and "google." in final_host:
            geo_localized = final_host not in ("google.com", "www.google.com")
    meta["geo_localized"] = geo_localized
    # Backward-compatible alias; new consumers should use geo_localized.
    meta["geo_redirect"] = geo_localized
    meta["content_type"] = resp.headers.get("Content-Type")
    meta["content_encoding"] = (resp.headers.get("Content-Encoding") or "").lower() or None
    meta["bytes_read_wire"] = len(wire)
    meta["truncated"] = truncated
    meta["body_sha256"] = hashlib.sha256(wire).hexdigest()

    # Persist exact response-body bytes before any transformation.
    raw_path = raw_dir / f"{name}.body.raw"
    raw_path.write_bytes(wire)

    body = wire
    if meta["content_encoding"] in ("gzip", "x-gzip"):
        if truncated:
            meta["error"] = "response was truncated before gzip decompression"
            body = b""
        else:
            try:
                body = gzip.decompress(wire)
            except OSError as exc:
                meta["error"] = f"gzip decompression failed: {exc}"
                body = b""

    text, encoding, replacement = decode_bytes(body, str(meta["content_type"] or "")) if body else ("", "utf-8", False)
    meta["bytes_decoded_body"] = len(body)
    meta["encoding"] = encoding
    meta["decode_replacement_used"] = replacement
    meta["text_sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()

    det = detect_captcha(str(meta["final_url"] or ""), text, engine)
    meta.update(det)

    status = int(meta["status"] or 0)
    if status in (403, 429):
        meta["blocked"] = True
        meta["blocked_reason"] = f"http-{status}"
    elif meta["captcha_detected"]:
        meta["blocked"] = True
        meta["blocked_reason"] = "captcha-or-antispider"

    html_path = raw_dir / f"{name}.html"
    html_path.write_text(text, encoding="utf-8")
    meta["saved_html"] = str(html_path)
    meta["saved_raw"] = str(raw_path)

    meta_path = out_dir / f"{name}.meta.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    emit_json(meta)
    return 0


if __name__ == "__main__":
    sys.exit(main())
