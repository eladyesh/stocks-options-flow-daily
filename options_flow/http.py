"""Bounded, rate-limited public requests; no credentials, paywall or challenge bypass."""
from __future__ import annotations

from dataclasses import dataclass
import json
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

from .core import iso

HOSTS = {"www.nasdaqtrader.com", "marketdata.theocc.com", "cdn.cboe.com",
         "cdn-api.cboe.com", "api.nasdaq.com"}
USER_AGENT = "StockOptionsFlowDaily/1.0 (+https://github.com/eladyesh/stocks-options-flow-daily)"


def validate_url(url):
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in HOSTS or parts.username or parts.password or parts.port not in (None, 443):
        raise ValueError("Only configured public HTTPS data sources are permitted")


class PublicRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


@dataclass
class Download:
    body: bytes
    url: str
    retrieved_at: str
    modified: str | None

    def json(self):
        return json.loads(self.body)

    def provenance(self):
        return {"url":self.url, "retrieved_at":self.retrieved_at,
                "http_last_modified":self.modified, "access":"original public dataset read"}


class Client:
    def __init__(self):
        self.locks = {host:threading.Lock() for host in HOSTS}
        self.last = {}

    def get(self, url, max_bytes=35_000_000):
        validate_url(url)
        host = urlsplit(url).hostname
        for attempt in range(2):
            with self.locks[host]:
                delay = max(0, self.last.get(host, 0) + .35 - time.monotonic())
                time.sleep(delay)
                self.last[host] = time.monotonic()
            try:
                request = Request(url, headers={"User-Agent":USER_AGENT, "Accept":"application/json,text/csv,application/xml,text/plain"})
                with build_opener(PublicRedirects()).open(request, timeout=35) as response:
                    body = response.read(max_bytes + 1)
                    if len(body) > max_bytes:
                        raise ValueError("Public response exceeded size limit")
                    return Download(body, response.url, iso(), response.headers.get("Last-Modified"))
            except HTTPError as exc:
                # Stop on access restrictions/rate limits; do not change identities/routes to evade them.
                if exc.code not in (500, 502, 503, 504) or attempt:
                    raise RuntimeError(f"Public source HTTP {exc.code}") from None
            except (TimeoutError, OSError):
                if attempt:
                    raise RuntimeError("Public source unavailable or timed out") from None
            time.sleep(1)
        raise RuntimeError("Public source unavailable")
