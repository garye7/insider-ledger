"""
Thin, rate-limited HTTP client for SEC EDGAR.

SEC's fair-access guidance asks for:
  - An identifying User-Agent on every request (company/app name + contact email).
  - A self-imposed rate limit (SEC enforces ~10 req/sec per IP; we stay well under that).

Docs: https://www.sec.gov/os/webmaster-faq#developers
"""
from __future__ import annotations

import os
import time
import requests


class EdgarClient:
    def __init__(self, user_agent: str | None = None, min_interval: float = 0.15):
        self.user_agent = user_agent or os.environ.get("SEC_USER_AGENT")
        if not self.user_agent:
            raise RuntimeError(
                "SEC_USER_AGENT is not set. SEC requires an identifying User-Agent on "
                "every request, e.g. 'InsiderLedger/1.0 (yourname@example.com)'. "
                "Set it as an environment variable or a GitHub Actions secret."
            )
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": self.user_agent,
                "Accept-Encoding": "gzip, deflate",
                "Host": "www.sec.gov",  # overwritten per-request by requests as needed
            }
        )
        # ~6-7 req/sec ceiling. SEC's own guidance caps at 10 req/sec; we stay under it
        # deliberately since this script also runs concurrently in CI with no backoff logic
        # beyond this delay.
        self.min_interval = min_interval
        self._last_request = 0.0

    def get(self, url: str, **kwargs) -> requests.Response:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        resp = self.session.get(url, timeout=25, **kwargs)
        self._last_request = time.monotonic()
        resp.raise_for_status()
        return resp

    def get_json(self, url: str, **kwargs):
        return self.get(url, **kwargs).json()

    def get_text(self, url: str, **kwargs) -> str:
        return self.get(url, **kwargs).text
