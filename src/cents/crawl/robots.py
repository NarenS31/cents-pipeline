"""robots.txt cache. Missing file (4xx) allows crawling; server/network failure disallows."""
from __future__ import annotations

from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser


class RobotsCache:
    def __init__(self, user_agent: str) -> None:
        self.user_agent = user_agent
        self._parsers: dict[str, RobotFileParser | None] = {}  # None = disallow all

    @staticmethod
    def origin(url: str) -> str:
        p = urlsplit(url)
        return f"{p.scheme}://{p.netloc}"

    def known(self, url: str) -> bool:
        return self.origin(url) in self._parsers

    def load(self, origin: str, status: int | None, body: str = "") -> None:
        """Record the result of fetching `origin/robots.txt` (status None = network error)."""
        if status is None or status >= 500 or status in (401, 403):
            self._parsers[origin] = None
        elif status >= 400:
            rp = RobotFileParser(); rp.parse([]); self._parsers[origin] = rp
        else:
            rp = RobotFileParser(); rp.parse(body.splitlines()); self._parsers[origin] = rp

    def allowed(self, url: str) -> bool:
        rp = self._parsers.get(self.origin(url))
        return bool(rp and rp.can_fetch(self.user_agent, url))

    def crawl_delay(self, url: str) -> float | None:
        rp = self._parsers.get(self.origin(url))
        d = rp.crawl_delay(self.user_agent) if rp else None
        return float(d) if d else None
