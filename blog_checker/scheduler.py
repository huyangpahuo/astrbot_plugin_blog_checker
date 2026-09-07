"""调度器：后台定时轮询博客，发现新文章后回调。

AstrBot v4.x 没有插件级 schedule 装饰器，这里用 asyncio 任务实现，
插件 terminate() 时统一取消，不会留下悬挂任务。
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Awaitable, Callable

from astrbot.api import logger

from .fetcher import Fetcher
from .models import Article, Blog
from .parsers import parser_registry
from .storage import Storage

# 轮询回调：blog + 新文章列表（调用方负责推送与标记）
NewArticlesCallback = Callable[[Blog, list[Article]], Awaitable[None]]


class Scheduler:
    def __init__(
        self,
        storage: Storage,
        fetcher: Fetcher,
        interval_minutes: float,
        max_articles_per_push: int = 5,
        blogs_provider: Callable[[], list[Blog]] | None = None,
    ):
        self.storage = storage
        self.fetcher = fetcher
        self.interval_minutes = max(1.0, float(interval_minutes))
        self.max_articles_per_push = max_articles_per_push
        # 博客来源由调用方决定（配置博客 + 动态博客的并集）
        self.blogs_provider = blogs_provider or storage.get_blogs
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self.on_new_articles: NewArticlesCallback | None = None

    # ---------- 抓取 ----------

    async def fetch_articles(self, blog: Blog, limit: int = 10) -> list[Article]:
        """抓取单个博客的文章列表。运行在线程池中，不阻塞事件循环。"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._fetch_articles_sync, blog, limit)

    def _fetch_articles_sync(self, blog: Blog, limit: int) -> list[Article]:
        errors: list[str] = []
        candidates: list[tuple[str, str]] = []  # (parser_name, url)

        if blog.rss_url:
            candidates.append(("rss", blog.rss_url))
        if blog.parser in ("auto", "rss"):
            for p in parser_registry.auto_candidates():
                if p is parser_registry.get("rss") and blog.rss_url:
                    continue  # 显式 rss_url 已排在候选首位
                if not blog.url:
                    continue
                # auto 探测 rss 时用 blog.url 作为探测入口
                candidates.append(("auto_rss", blog.url))
                break
        if blog.parser in ("auto", "html") and blog.url:
            candidates.append(("html", blog.url))

        for parser_name, url in candidates:
            try:
                if parser_name == "auto_rss":
                    rss_url = self.fetcher.discover_rss(blog.url)
                    if not rss_url:
                        errors.append("auto: 未探测到 RSS 地址")
                        continue
                    content = self.fetcher.get(rss_url).content
                    articles = parser_registry.get("rss").parse(blog, content, rss_url)
                else:
                    parser = parser_registry.get(parser_name)
                    content = self.fetcher.get(url).content
                    articles = parser.parse(blog, content, url)

                if articles:
                    return articles[:limit]
                errors.append(f"{parser_name}: 解析结果为空")
            except Exception as e:  # noqa: BLE001
                errors.append(f"{parser_name}: {e}")

        if errors:
            raise RuntimeError(f"[{blog.name}] " + "；".join(errors))
        raise RuntimeError(f"[{blog.name}] 没有可用的抓取方式（url 为空？）")

    # ---------- 轮询 ----------

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="blog-checker-scheduler")

    async def stop(self) -> None:
        self._stop.set()
        if self._task and not self._task.done():
            try:
                await asyncio.wait_for(asyncio.shield(self._task), timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                self._task.cancel()
        self._task = None

    async def _run(self) -> None:
        # 启动后稍等片刻再首轮抓取，避免挤占 AstrBot 启动过程
        await asyncio.sleep(10)
        while not self._stop.is_set():
            try:
                await self.poll_once()
            except Exception as e:  # noqa: BLE001
                logger.error(f"[blog_checker] 轮询异常：{e}")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_minutes * 60)
            except asyncio.TimeoutError:
                pass

    async def poll_once(self) -> int:
        """对所有启用博客抓取一轮，把新文章交给回调。返回发现的新文章总数。"""
        if not self.on_new_articles:
            return 0
        blogs = await self._all_blogs()
        total = 0
        for blog in blogs:
            if not blog.enabled:
                continue
            try:
                articles = await self.fetch_articles(blog)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[blog_checker] 抓取失败 {blog.name}: {e}")
                continue

            seen = self.storage.seen_keys(blog.name)
            fresh = [a for a in articles if a.unique_key() not in seen]
            if not fresh:
                continue
            # 首次见到某博客时不刷屏：只标记不推送
            if not seen:
                self.storage.mark_seen(blog.name, articles)
                continue
            fresh = fresh[: self.max_articles_per_push]
            await self._safe_callback(blog, fresh)
            self.storage.mark_seen(blog.name, fresh)
            total += len(fresh)
        return total

    def renderer_limit(self) -> int:
        return 5

    async def _all_blogs(self) -> list[Blog]:
        result = self.blogs_provider()
        if inspect.isawaitable(result):
            result = await result
        return list(result)

    async def _safe_callback(self, blog: Blog, articles: list[Article]) -> None:
        try:
            result = self.on_new_articles(blog, articles)
            if inspect.isawaitable(result):
                await result
        except Exception as e:  # noqa: BLE001
            logger.error(f"[blog_checker] 推送回调异常：{e}")
