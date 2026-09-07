"""解析器注册表：RSS/Atom 与 HTML 两套内置解析器，支持注册扩展。

扩展新解析器的方式：

    from blog_checker.parsers import parser_registry, BaseParser

    @parser_registry.register("jsonfeed")
    class JsonFeedParser(BaseParser):
        def parse(self, blog, content, base_url) -> list[Article]:
            ...
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .models import Article, Blog

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def strip_html(text: str, limit: int = 120) -> str:
    """去掉 HTML 标签、压缩空白并截断，用于摘要展示。"""
    if not text:
        return ""
    text = _TAG_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    if len(text) > limit:
        text = text[:limit].rstrip() + "…"
    return text


def format_time(value: Any) -> str:
    """把 feedparser 的 struct_time / datetime / 字符串统一为本地可读时间。"""
    if not value:
        return ""
    try:
        if isinstance(value, str):
            for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                try:
                    value = datetime.strptime(value[:19], fmt)
                    break
                except ValueError:
                    continue
        if isinstance(value, datetime):
            if value.tzinfo is not None:
                value = value.astimezone()
            return value.strftime("%Y-%m-%d %H:%M")
        # feedparser 的 struct_time 视为 UTC
        ts = datetime(*value[:6], tzinfo=timezone.utc)
        return ts.astimezone().strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(value)


class BaseParser(ABC):
    """解析器接口：把抓取到的原始内容转换为 Article 列表。"""

    #: 是否可以作为 auto 模式的候选（rss 可以，html 是最终兜底）
    auto_candidate: bool = True

    @abstractmethod
    def parse(self, blog: Blog, content: bytes | str, base_url: str) -> list[Article]:
        """解析内容，返回文章列表（建议按最新在前排序）。"""


class RssParser(BaseParser):
    """RSS 2.0 / Atom 解析器（feedparser）。"""

    auto_candidate = True

    def parse(self, blog: Blog, content: bytes | str, base_url: str) -> list[Article]:
        import feedparser

        parsed = feedparser.parse(content)
        articles: list[Article] = []
        for entry in parsed.entries:
            link = entry.get("link", "")
            title = entry.get("title", "").strip() or "（无标题）"
            author = entry.get("author", "") or ""
            published = format_time(
                entry.get("published_parsed") or entry.get("updated_parsed")
            )
            summary_html = entry.get("summary", "") or entry.get("description", "")

            articles.append(
                Article(
                    blog_name=blog.name,
                    title=title,
                    link=urljoin(base_url, link),
                    guid=entry.get("id", "") or link or title,
                    author=author,
                    published=published,
                    summary=strip_html(summary_html),
                    cover=self._find_cover(entry, base_url),
                )
            )
        return articles

    @staticmethod
    def _find_cover(entry: Any, base_url: str) -> str:
        # 1) media:content / enclosure
        for key in ("media_content", "media_thumbnail", "enclosures"):
            media = entry.get(key) or []
            for item in media:
                href = item.get("url") or item.get("href") or ""
                if href:
                    return urljoin(base_url, href)
        # 2) 正文里第一张 <img>
        html = entry.get("content", [{}])
        content_html = ""
        if isinstance(html, list) and html:
            content_html = html[0].get("value", "")
        content_html = content_html or entry.get("summary", "")
        if content_html:
            soup = BeautifulSoup(content_html, "html.parser")
            img = soup.find("img", src=True)
            if img:
                return urljoin(base_url, img["src"])
        return ""


class HtmlParser(BaseParser):
    """HTML 页面解析器：通过 CSS 选择器抽取文章列表，作为无 RSS 博客的兜底。"""

    auto_candidate = False

    DEFAULT_SELECTORS = {
        "item": "article, .post-item, .post-card, .post-block, .post",
        "title": "h1, h2, h3, h4",
        "link": "a[href]",
        "date": "time, .post-date, .date",
        "cover": "img[src]",
    }

    def parse(self, blog: Blog, content: bytes | str, base_url: str) -> list[Article]:
        selectors = {**self.DEFAULT_SELECTORS, **(blog.html_selectors or {})}
        soup = BeautifulSoup(content, "html.parser")

        blocks = soup.select(selectors["item"])
        if not blocks:
            # 兜底：找包含"阅读"字样链接的容器（兼容部分 Hexo 主题）
            blocks = [
                a.parent for a in soup.find_all("a", href=True)
                if a.find_parent(["article", "section", "div"]) and ("阅读" in a.get_text(strip=True))
            ]
            blocks = [b for b in blocks if b]

        articles: list[Article] = []
        seen_links: set[str] = set()
        for block in blocks:
            title_tag = block.select_one(selectors["title"])
            if not title_tag:
                continue
            title = title_tag.get_text(strip=True)
            if not title:
                continue

            link = ""
            link_tag = title_tag.find("a", href=True) or block.select_one(selectors["link"])
            if link_tag and link_tag.get("href"):
                link = urljoin(base_url, link_tag["href"])
            if not link or link in seen_links:
                continue
            seen_links.add(link)

            date_tag = block.select_one(selectors["date"])
            published = ""
            if date_tag:
                published = date_tag.get("datetime") or date_tag.get_text(strip=True)

            cover = ""
            img = block.select_one(selectors["cover"])
            if img and img.get("src"):
                cover = urljoin(base_url, img["src"])
                # 跳过常见的图标/头像
                if any(k in cover.lower() for k in ("favicon", "avatar", "icon")):
                    cover = ""

            articles.append(
                Article(
                    blog_name=blog.name,
                    title=title,
                    link=link,
                    guid=link,
                    published=published,
                    summary=strip_html(block.get_text(" ", strip=True)),
                    cover=cover,
                )
            )
        return articles


class ParserRegistry:
    """解析器注册表。用 @parser_registry.register("名字") 注册新解析器。"""

    def __init__(self) -> None:
        self._parsers: dict[str, BaseParser] = {}

    def register(self, name: str) -> Callable[[type[BaseParser]], type[BaseParser]]:
        def decorator(parser_cls: type[BaseParser]) -> type[BaseParser]:
            self._parsers[name] = parser_cls()
            return parser_cls

        return decorator

    def get(self, name: str) -> BaseParser | None:
        return self._parsers.get(name)

    def names(self) -> list[str]:
        return list(self._parsers.keys())

    def auto_candidates(self) -> list[BaseParser]:
        """auto 模式下的候选解析器，按注册顺序。"""
        return [p for p in self._parsers.values() if p.auto_candidate]


parser_registry = ParserRegistry()
parser_registry.register("rss")(RssParser)
parser_registry.register("html")(HtmlParser)
