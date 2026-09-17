#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""URL safety checks for direct fetches and redirect resolution.

Public builds deny loopback, private, link-local, multicast, reserved and
unspecified addresses by default. This reduces SSRF risk when an AI agent is
allowed to fetch arbitrary URLs. Use --allow-private-network only when the user
intentionally needs an intranet/local service.
"""
from __future__ import annotations

import ipaddress
import socket
from typing import Dict, List
from urllib.parse import urlsplit


class URLSafetyError(ValueError):
    pass


class URLResolutionError(OSError):
    pass


def _blocked_ip(ip_text: str) -> bool:
    ip = ipaddress.ip_address(ip_text)
    return bool(
        ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
        or ip.is_reserved or ip.is_unspecified
    )


def inspect_url(url: str, allow_private_network: bool = False) -> Dict[str, object]:
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in ("http", "https"):
        raise URLSafetyError("unsupported URL scheme: %s" % (parsed.scheme or "<empty>"))
    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host:
        raise URLSafetyError("URL has no hostname")
    if host in ("localhost", "localhost.localdomain") or host.endswith(".localhost"):
        if not allow_private_network:
            raise URLSafetyError("private/local hostname is blocked: %s" % host)
    addresses: List[str] = []
    try:
        # Literal IPs do not need DNS.
        ipaddress.ip_address(host)
        addresses = [host]
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), 0, socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise URLResolutionError("DNS resolution failed for %s: %s" % (host, exc))
        for info in infos:
            addr = info[4][0]
            if addr not in addresses:
                addresses.append(addr)
    if not allow_private_network:
        blocked = [addr for addr in addresses if _blocked_ip(addr)]
        if blocked:
            raise URLSafetyError("private/non-public address blocked for %s: %s" % (host, ",".join(blocked)))
    return {"url": url, "host": host, "resolved_addresses": addresses, "allow_private_network": bool(allow_private_network)}


def assert_url_safe(url: str, allow_private_network: bool = False) -> Dict[str, object]:
    return inspect_url(url, allow_private_network)
