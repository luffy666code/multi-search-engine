#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Runtime/environment helpers shared by the search skill.

The runtime contract is deliberately conservative:
- Python 3.7+ only (no 3.8/3.9-only syntax or APIs in core code)
- Windows and Linux are both supported
- TLS verification is strict by default
- custom CA bundles are supported
- insecure TLS is opt-in and always recorded
"""
from __future__ import annotations

import os
import platform
import ssl
import sys
from pathlib import Path
from typing import Dict, Optional, Tuple

MIN_PYTHON = (3, 7)
SKILL_ROOT = Path(__file__).resolve().parent.parent


def python_version_string() -> str:
    return "%d.%d.%d" % (sys.version_info[0], sys.version_info[1], sys.version_info[2])


def execution_os() -> str:
    system = (platform.system() or "unknown").lower()
    machine = platform.machine() or "unknown"
    return "%s_%s" % (system, machine)


def ensure_supported_python() -> Tuple[bool, str]:
    ok = sys.version_info >= MIN_PYTHON
    return ok, python_version_string()


def _existing_file(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    p = Path(value).expanduser()
    if not p.is_absolute():
        p = (SKILL_ROOT / p).resolve()
    if p.is_file():
        return str(p)
    return None


def optional_certifi_bundle() -> Optional[str]:
    try:
        import certifi  # type: ignore
        path = certifi.where()
        return path if path and Path(path).is_file() else None
    except Exception:
        return None


def choose_ca_bundle(explicit: Optional[str] = None) -> Tuple[Optional[str], str]:
    """Return (bundle_path, source).

    Priority: explicit CLI -> MSE_CA_BUNDLE -> SSL_CERT_FILE -> certifi (only when
    importable).  If none is present, urllib/OpenSSL uses the system default store.
    """
    candidates = [
        (explicit, "cli"),
        (os.environ.get("MSE_CA_BUNDLE"), "env:MSE_CA_BUNDLE"),
        (os.environ.get("SSL_CERT_FILE"), "env:SSL_CERT_FILE"),
    ]
    for value, source in candidates:
        path = _existing_file(value)
        if path:
            return path, source
    certifi_path = optional_certifi_bundle()
    if certifi_path:
        return certifi_path, "certifi"
    return None, "system-default"


def build_ssl_context(insecure: bool = False, ca_bundle: Optional[str] = None):
    """Build an SSL context and return (context, metadata)."""
    if insecure:
        ctx = ssl._create_unverified_context()
        return ctx, {
            "tls_mode": "insecure",
            "tls_verified": False,
            "ca_bundle": None,
            "ca_source": "verification-disabled",
        }

    bundle, source = choose_ca_bundle(ca_bundle)
    if bundle:
        ctx = ssl.create_default_context(cafile=bundle)
    else:
        ctx = ssl.create_default_context()
    return ctx, {
        "tls_mode": "strict",
        "tls_verified": True,
        "ca_bundle": bundle,
        "ca_source": source,
    }


def is_cert_verification_error(message: str) -> bool:
    text = (message or "").lower()
    markers = (
        "certificate_verify_failed",
        "certificate verify failed",
        "unable to get local issuer certificate",
        "self signed certificate",
        "unknown ca",
    )
    return any(m in text for m in markers)


def environment_report(ca_bundle: Optional[str] = None) -> Dict[str, object]:
    ok, version = ensure_supported_python()
    paths = ssl.get_default_verify_paths()
    selected, source = choose_ca_bundle(ca_bundle)
    ca_count = None
    context_error = None
    try:
        ctx, _ = build_ssl_context(False, ca_bundle)
        ca_count = len(ctx.get_ca_certs())
    except Exception as exc:
        context_error = "%s: %s" % (type(exc).__name__, exc)

    warnings = []
    if not ok:
        warnings.append("python-version-too-old")
    if context_error:
        warnings.append("ssl-context-create-failed")
    elif not selected and (ca_count == 0):
        warnings.append("no-ca-certificates-detected")

    return {
        "execution_os": execution_os(),
        "python_version": version,
        "python_supported": ok,
        "python_minimum": "%d.%d" % MIN_PYTHON,
        "ca_bundle_selected": selected,
        "ca_source": source,
        "default_cafile": paths.cafile,
        "default_capath": paths.capath,
        "loaded_ca_count": ca_count,
        "ssl_context_error": context_error,
        "warnings": warnings,
    }
