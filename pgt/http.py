"""Polite HTTP client: identifies itself, obeys robots.txt, rate-limits per host,
retries transient failures, and never treats a failure as "listing gone"."""
from __future__ import annotations

import random
import time
import urllib.robotparser
from dataclasses import dataclass
from urllib.parse import urlparse

import requests

USER_AGENT = ("porsche-gt-scout/1.0 (personal single-buyer vehicle research; "
              "low-volume daily fetch; honours robots.txt)")


@dataclass
class Response:
    url: str
    status: int            # 0 = network failure, -1 = blocked by robots.txt
    text: str = ""
    error: str | None = None
    headers: dict | None = None

    @property
    def ok(self) -> bool:
        return self.status == 200


class Fetcher:
    def __init__(self, min_interval: float = 1.0, timeout: float = 30.0, max_retries: int = 3,
                 session: requests.Session | None = None, respect_robots: bool = True):
        self.min_interval = min_interval
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"})
        self.respect_robots = respect_robots
        self._last: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self.requests = 0
        self.errors = 0

    # -- robots -----------------------------------------------------------------
    def allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        host = urlparse(url).netloc
        if host not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                r = self.session.get(f"https://{host}/robots.txt", timeout=self.timeout)
                if r.status_code == 200:
                    rp.parse(r.text.splitlines())
                    self._robots[host] = rp
                elif r.status_code in (401, 403):
                    self._robots[host] = None      # robots says nothing is allowed
                else:
                    rp.parse([])
                    self._robots[host] = rp
            except requests.RequestException:
                rp.parse([])
                self._robots[host] = rp
        rp = self._robots[host]
        return bool(rp and rp.can_fetch(USER_AGENT, url))

    # -- fetch ------------------------------------------------------------------
    def _wait(self, host: str) -> None:
        last = self._last.get(host, 0.0)
        delay = self.min_interval + random.uniform(0, self.min_interval * 0.3) - (time.monotonic() - last)
        if delay > 0:
            time.sleep(delay)
        self._last[host] = time.monotonic()

    def get(self, url: str, params: dict | None = None, check_robots: bool = True) -> Response:
        if check_robots and not self.allowed(url):
            return Response(url, -1, error="disallowed by robots.txt")
        host = urlparse(url).netloc
        last_err, last_headers, last_body = None, None, ""
        for attempt in range(self.max_retries + 1):
            self._wait(host)
            self.requests += 1
            try:
                r = self.session.get(url, params=params, timeout=self.timeout)
            except requests.RequestException as exc:   # network-level failure
                last_err = f"{type(exc).__name__}: {exc}"
                self.errors += 1
                time.sleep(min(30, 2 ** attempt))
                continue
            if r.status_code in (429, 500, 502, 503, 504):
                self.errors += 1
                last_err = f"HTTP {r.status_code}"
                last_headers = {k.lower(): v for k, v in r.headers.items()
                                if k.lower() in ("server", "retry-after", "via", "x-cache", "cf-ray", "x-amz-cf-id",
                                                 "x-amzn-waf-action", "content-type", "x-datadome", "akamai-grn")}
                last_body = r.text[:300]
                retry_after = r.headers.get("Retry-After", "")
                wait = float(retry_after) if retry_after.isdigit() else min(60, 3 * 2 ** attempt)
                time.sleep(wait)
                continue
            return Response(url, r.status_code, r.text if r.status_code == 200 else r.text[:2000])
        return Response(url, 0, last_body, error=last_err or "failed", headers=last_headers)
