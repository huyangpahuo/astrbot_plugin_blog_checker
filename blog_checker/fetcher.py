"""抓取器：HTTP 请求、重试、RSS 地址自动探测。"""

from __future__ import annotations

import re
import time
from urllib.parse import urljoin

import requests

from .models import Blog

# auto 模式下依次探测的常见 RSS 路径
COMMON_RSS_PATHS = (
    "atom.xml",
    "rss.xml",
    "rss2.xml",
    "feed.xml",
    "feed",
    "rss",
    "index.xml",
    "feed.json",
)

_RSS_MARKS = (b"<rss", b"<feed", b"<?xml")

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml,application/rss+xml;q=0.9,*/*;q=0.8",
}


class Fetcher:
    """同步 HTTP 抓取（由调用方放到线程池里跑，避免阻塞事件循环）。"""

    def __init__(self, timeout: int = 15, retries: int = 2):
        self.timeout = timeout
        self.retries = retries
        self._session = requests.Session()
        self._session.headers.update(DEFAULT_HEADERS)

    def get(self, url: str) -> requests.Response:
        """带重试的 GET。"""
        last_exc: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                resp = self._session.get(url, timeout=self.timeout)
                resp.raise_for_status()
                return resp
            except Exception as e:  # noqa: BLE001 - 统一重试后由调用方处理
                last_exc = e
                if attempt < self.retries:
                    time.sleep(0.5 * (attempt + 1))
        raise last_exc  # type: ignore[misc]

    def looks_like_feed(self, resp: requests.Response) -> bool:
        """粗略判断响应内容是不是 RSS/Atom/XML。"""
        ctype = resp.headers.get("Content-Type", "").lower()
        if "xml" in ctype or "rss" in ctype or "atom" in ctype:
            return True
        head = resp.content[:512].lstrip().lower()
        return any(mark in head for mark in _RSS_MARKS)

    def discover_rss(self, blog_url: str) -> str:
        """在博客主页和常见路径里探测 RSS 地址，找不到返回空串。"""
        # 1) 主页 <link rel="alternate"> 声明
        try:
            resp = self.get(blog_url)
            m = re.search(
                r'<link[^>]+rel=["\']alternate["\'][^>]*type=["\'][^"\]*'
                r'(?:rss|atom)\+xml["\'][^>]*href=["\']([^"\']+)["\']',
                resp.text,
                re.IGNORECASE,
            )
            if not m:
                m = re.search(
                    r'<link[^>]+href=["\']([^"\']+)["\'][^>]*'
                    r'type=["\'](?:application/)?(?:rss|atom)\+xml["\']',
                    resp.text,
                    re.IGNORECASE,
                )
            if m:
                return urljoin(blog_url, m.group(1))
        except Exception:
            pass

        # 2) 逐个试常见路径
        for path in COMMON_RSS_PATHS:
            candidate = urljoin(blog_url, path)
            try:
                resp = self.get(candidate)
                if self.looks_like_feed(resp):
                    return candidate
            except Exception:
                continue
        return ""
