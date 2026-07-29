"""Validation helpers for user-supplied service base URLs.

The integration forwards the user's IAM bearer token to the hijri, hadith
and quran service URLs, so those URLs must be checked before any request is
made:

- ``https://`` is required for any public host — plain HTTP would expose
  the bearer token to anyone on the network path;
- plain ``http://`` is accepted only for localhost / RFC-1918 (private)
  hosts, for self-hosters running the stack on their own machine or LAN.

This module is deliberately free of Home Assistant imports so it can be
unit-tested standalone.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

# Config flow error keys returned by validate_service_url()
ERROR_INVALID_URL = "invalid_url"
ERROR_HTTPS_REQUIRED = "https_required"

# Hosts plain http is acceptable for: RFC-1918 LAN ranges, loopback and
# link-local only.  (ipaddress.is_private would also match documentation
# ranges like 192.0.2.0/24 or the CGNAT range 100.64.0.0/10, which must
# NOT be treated as trusted private hosts.)
_PRIVATE_NETWORKS = tuple(
    ipaddress.ip_network(cidr)
    for cidr in (
        "10.0.0.0/8",  # RFC 1918
        "172.16.0.0/12",  # RFC 1918
        "192.168.0.0/16",  # RFC 1918
        "127.0.0.0/8",  # IPv4 loopback
        "::1/128",  # IPv6 loopback
        "169.254.0.0/16",  # IPv4 link-local
        "fe80::/10",  # IPv6 link-local
    )
)


def is_private_host(hostname: str | None) -> bool:
    """Return True for localhost / loopback / RFC-1918 / link-local hosts."""
    if not hostname:
        return False
    host = hostname.strip().lower()
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # A regular DNS name — not a private host as far as we can tell.
        return False
    return any(ip in network for network in _PRIVATE_NETWORKS)


def validate_service_url(url: str) -> str | None:
    """Validate a user-supplied service base URL.

    Returns a config-flow error key, or None when the URL is acceptable:

    - ERROR_INVALID_URL: unparseable, missing host, embedded credentials,
      or a scheme other than http/https;
    - ERROR_HTTPS_REQUIRED: plain http against a non-private host — the
      bearer token would be sent in cleartext over the internet.
    """
    try:
        parsed = urlparse(url.strip())
    except (ValueError, AttributeError):
        return ERROR_INVALID_URL
    if parsed.scheme not in ("http", "https"):
        return ERROR_INVALID_URL
    try:
        hostname = parsed.hostname
    except ValueError:
        return ERROR_INVALID_URL
    if not hostname:
        return ERROR_INVALID_URL
    if parsed.username or parsed.password:
        # Credentials embedded in the URL would leak into logs and headers.
        return ERROR_INVALID_URL
    if parsed.scheme == "http" and not is_private_host(hostname):
        return ERROR_HTTPS_REQUIRED
    return None


def non_default_hosts(urls: dict[str, str], defaults: dict[str, str]) -> dict[str, str]:
    """Return {key: hostname} for URLs whose host differs from the defaults.

    Used to warn the user before their bearer token is forwarded to hosts
    other than the built-in ilmy.org services.
    """
    result: dict[str, str] = {}
    for key, url in urls.items():
        default = defaults.get(key)
        if default is None:
            continue
        host = (urlparse(url.strip()).hostname or "").lower()
        default_host = (urlparse(default).hostname or "").lower()
        if host != default_host:
            result[key] = host
    return result
