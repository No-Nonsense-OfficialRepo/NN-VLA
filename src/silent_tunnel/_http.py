"""Minimal JSON-over-HTTP helper (stdlib only)."""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from urllib.error import HTTPError

from ._version import __version__


class HttpError(Exception):
    def __init__(self, status: int, body: str = ""):
        super().__init__(f"HTTP {status}")
        self.status = status
        self.body = body


def _is_loopback(url: str) -> bool:
    return (urllib.parse.urlparse(url).hostname or "") in ("127.0.0.1", "localhost", "::1")


def request_json(method, url, headers=None, body=None, timeout=10.0):
    """Send a request, return parsed JSON (or None for an empty body).

    Raises HttpError for non-2xx. Network-level failures (URLError, timeouts)
    propagate as OSError subclasses for the caller to translate.
    """
    hdrs = {"Accept": "application/json", "User-Agent": f"SilentTunnel/{__version__}"}
    hdrs.update(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    # Never route loopback calls (the local ZeroTier service) through a system proxy.
    if _is_loopback(url) and urllib.parse.urlparse(url).scheme == 'http':
        # build_opener also initializes HTTPS and its environment-dependent
        # certificate/key-log files, even for plain local HTTP. Keep service
        # calls independent of TLS and disallow redirects of the access key.
        opener = urllib.request.OpenerDirector()
        for handler in (urllib.request.HTTPHandler(), urllib.request.HTTPDefaultErrorHandler(),
                        urllib.request.HTTPErrorProcessor()):
            opener.add_handler(handler)
    else:
        opener = urllib.request.build_opener()
    try:
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read()
    except HTTPError as e:
        raise HttpError(e.code, e.read().decode("utf-8", "replace")[:500]) from None
    return json.loads(raw) if raw.strip() else None
