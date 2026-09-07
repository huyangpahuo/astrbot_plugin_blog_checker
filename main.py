"""AstrBot 博客检查器（高级版）

功能：
- 多博客管理：配置文件预置 + 命令动态增删
- RSS/Atom 自动探测，HTML CSS 选择器兜底，解析器可注册扩展
- 会话订阅：后台定时轮询，新文章自动推送到订阅的会话
- 持久化：推送记录与动态订阅保存在 data/plugin_data/ 下，重启不丢

指令（/blog 命令组）：
    blog check [博客名]      查看最新文章
    blog list [博客名]       列出博客 / 文章列表
    blog add <名字> <URL>    添加博客（可选 rss=<RSS地址> parser=<auto|rss|html>）
    blog remove <名字>       删除博客（仅限动态添加的）
    blog subscribe [名字]    订阅博客更新（不填则订阅全部）
    blog unsubscribe [名字]  取消订阅
    blog fetch               立即轮询一轮（管理员）
    blog reload              重载配置（管理员）
"""

import asyncio
from typing import Any

import astrbot.api.star as star_api
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageEventResult, filter
from astrbot.api.star import Context, Star, StarTools

from .blog_checker import Article, Blog, Fetcher, Renderer, Scheduler, Storage
from .blog_checker.parsers import parser_registry

PLUGIN_NAME = "astrbot_plugin_blog_checker"


class BlogCheckerPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

        data_dir = StarTools.get_data_dir(PLUGIN_NAME)
        self.storage = Storage(str(data_dir))
        self.fetcher = Fetcher(
            timeout=int(config.get("request_timeout", 15)),
            retries=int(config.get("request_retries", 2)),
        )
        self.renderer = Renderer(
            max_articles=int(config.get("max_articles", 5)),
            show_cover=bool(config.get("show_cover", True)),
            show_summary=bool(config.get("show_summary", True)),
        )
        self.scheduler = Scheduler(
            storage=self.storage,
            fetcher=self.fetcher,
            interval_minutes=float(config.get("check_interval", 30)),
            max_articles_per_push=int(config.get("max_articles_per_push", 3)),
            blogs_provider=self._all_blogs,
        )
        self.scheduler.on_new_articles = self._push_new_articles
        self._default_blog = str(config.get("default_blog", "")).strip()
        self._websearch_lock = asyncio.Lock()

    async def initialize(self):
        """框架在插件加载后调用：启动后台轮询。"""
        if bool(self.config.get("enable_push", True)):
            self.scheduler.start()
            logger.info("[blog_checker] 后台轮询已启动")

    async def terminate(self):
        await self.scheduler.stop()

    # ---------- 博客来源 ----------

    def _config_blogs(self) -> list[Blog]:
        raw = self.config.get("blogs", [])
        blogs: list[Blog] = []
        if isinstance(raw, list):
            for item in raw:
                if not isinstance(item, dict) or not item.get("name") or not item.get("url"):
                    continue
                blogs.append(Blog.from_dict(item, source="config"))
        return blogs

    def _all_blogs(self) -> list[Blog]:
        """配置博客 + 动态博客，按名字去重（配置优先）。"""
        merged: dict[str, Blog] = {}
        for b in self._config_blogs() + self.storage.get_blogs():
            merged.setdefault(b.name, b)
        return list(merged.values())

    def _find_blog(self, name: str) -> Blog | None:
        for b in self._all_blogs():
            if b.name == name:
                return b
        return None

    def _resolve_blog(self, name: str) -> Blog | None:
        if name:
            return self._find_blog(name)
        if self._default_blog:
            return self._find_blog(self._default_blog)
        blogs = self._all_blogs()
        return blogs[0] if blogs else None

    # ---------- 推送 ----------

    async def _push_new_articles(self, blog: Blog, articles: list[Article]) -> None:
        """调度器回调：把新文章推送到所有订阅了该博客的会话。"""
        for umo, blog_names in self.storage.all_subscriptions().items():
            if blog.name not in blog_names:
                continue
            for article in articles:
                try:
                    await self.context.send_message(umo, self.renderer.article_at(article))
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"[blog_checker] 推送到 {umo} 失败：{e}")

    # ---------- 指令 ----------

    @filter.command_group("blog", alias={"博客"})
    def blog(self):
        """博客检查器指令组"""

    @blog.command("check")
    async def blog_check(self, event: AstrMessageEvent, name: str = ""):
        """查看博客最新文章：blog check [博客名]"""
        target = self._resolve_blog(name)
        if not target:
            yield event.plain_result("还没有配置博客，用 blog add 添加或到 WebUI 配置。")
            return
        try:
            articles = await self.scheduler.fetch_articles(target, limit=1)
        except Exception as e:  # noqa: BLE001
            yield event.plain_result(f"抓取失败：{e}")
            return
        if not articles:
            yield event.plain_result("没有读到文章。")
            return
        self.storage.mark_seen(target.name, articles)
        yield event.chain_result(list(self.renderer.latest(articles[0]).chain))

    @blog.command("list")
    async def blog_list(self, event: AstrMessageEvent, name: str = ""):
        """blog list：列出所有博客；blog list <博客名>：列出该博客最近文章"""
        if name:
            target = self._find_blog(name)
            if not target:
                yield event.plain_result(f"没有找到博客「{name}」。")
                return
            try:
                articles = await self.scheduler.fetch_articles(target)
            except Exception as e:  # noqa: BLE001
                yield event.plain_result(f"抓取失败：{e}")
                return
            yield event.chain_result(list(self.renderer.list_articles(articles).chain))
        else:
            blogs = self._all_blogs()
            if not blogs:
                yield event.plain_result("还没有配置博客。")
                return
            lines = ["📰 已注册的博客："]
            subs = self.storage.all_subscriptions()
            for b in blogs:
                mark = "✅" if b.enabled else "⏸"
                count = sum(1 for names in subs.values() if b.name in names)
                lines.append(f"{mark} {b.name}（{b.parser}，订阅 {count}）")
            mine = self.storage.subscriptions_of(event.unified_msg_origin)
            if mine:
                lines.append(f"\n本会话订阅：{'、'.join(mine)}")
            yield event.plain_result("\n".join(lines))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @blog.command("add")
    async def blog_add(self, event: AstrMessageEvent, name: str, url: str, options: str = ""):
        """添加博客：blog add <名字> <URL> [rss=地址 parser=auto|rss|html]"""
        if self._find_blog(name):
            yield event.plain_result(f"博客「{name}」已存在。")
            return

        blog = Blog(name=name, url=url, source="dynamic")
        for token in options.split():
            if token.startswith("rss="):
                blog.rss_url = token[4:]
            elif token.startswith("parser="):
                p = token[7:].lower()
                if p not in ("auto", "rss", "html"):
                    yield event.plain_result("parser 只能是 auto / rss / html。")
                    return
                blog.parser = p

        # auto 模式先探测一次 RSS，把结果固化，之后轮询更快更稳
        if blog.parser == "auto" and not blog.rss_url:
            yield event.plain_result("正在探测 RSS 地址……")
            try:
                rss_url = await asyncio.get_running_loop().run_in_executor(
                    None, self.fetcher.discover_rss, url
                )
            except Exception:  # noqa: BLE001
                rss_url = ""
            if rss_url:
                blog.rss_url = rss_url
                yield event.plain_result(f"已探测到 RSS：{rss_url}")
            else:
                yield event.plain_result("未探测到 RSS，将使用 HTML 解析（可用 html_selectors 调整）。")

        self.storage.upsert_blog(blog)
        # 首轮抓取只记录不推送，避免刚添加就刷屏
        try:
            articles = await self.scheduler.fetch_articles(blog)
            self.storage.mark_seen(blog.name, articles)
            yield event.plain_result(f"已添加「{name}」，读到 {len(articles)} 篇文章。")
        except Exception as e:  # noqa: BLE001
            yield event.plain_result(f"已添加「{name}」，但首次抓取失败：{e}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @blog.command("remove")
    async def blog_remove(self, event: AstrMessageEvent, name: str):
        """删除动态添加的博客：blog remove <名字>"""
        if self._find_blog(name) is None:
            yield event.plain_result(f"没有找到博客「{name}」。")
            return
        if any(b.name == name and b.source == "config" for b in self._config_blogs()):
            yield event.plain_result(f"「{name}」来自 WebUI 配置，请到 WebUI 中移除。")
            return
        if self.storage.remove_blog(name):
            yield event.plain_result(f"已删除「{name}」。")
        else:
            yield event.plain_result(f"删除「{name}」失败。")

    @blog.command("subscribe")
    async def blog_subscribe(self, event: AstrMessageEvent, name: str = ""):
        """订阅博客更新推送：blog subscribe [博客名]（不填订阅全部）"""
        umo = event.unified_msg_origin
        if name:
            if not self._find_blog(name):
                yield event.plain_result(f"没有找到博客「{name}」。")
                return
            added = self.storage.subscribe(umo, name)
            yield event.plain_result(
                f"已订阅「{name}」的更新。" if added else f"本会话已订阅过「{name}」。"
            )
            return
        blogs = self._all_blogs()
        if not blogs:
            yield event.plain_result("还没有可订阅的博客。")
            return
        for b in blogs:
            self.storage.subscribe(umo, b.name)
        yield event.plain_result(f"已订阅全部 {len(blogs)} 个博客的更新。")

    @blog.command("unsubscribe")
    async def blog_unsubscribe(self, event: AstrMessageEvent, name: str = ""):
        """取消订阅：blog unsubscribe [博客名]（不填取消全部）"""
        umo = event.unified_msg_origin
        if name:
            ok = self.storage.unsubscribe(umo, name)
            yield event.plain_result(
                f"已取消订阅「{name}」。" if ok else f"本会话没有订阅「{name}」。"
            )
            return
        mine = self.storage.subscriptions_of(umo)
        if not mine:
            yield event.plain_result("本会话没有订阅任何博客。")
            return
        for n in mine:
            self.storage.unsubscribe(umo, n)
        yield event.plain_result("已取消本会话的全部订阅。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @blog.command("fetch")
    async def blog_fetch(self, event: AstrMessageEvent):
        """立即轮询一轮（管理员）：blog fetch"""
        count = await self.scheduler.poll_once()
        yield event.plain_result(f"轮询完成，发现 {count} 篇新文章。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @blog.command("reload")
    async def blog_reload(self, event: AstrMessageEvent):
        """重载配置并重启后台任务（管理员）：blog reload"""
        try:
            await self.terminate()
            self._apply_config()
            await self.initialize()
        except Exception as e:  # noqa: BLE001
            yield event.plain_result(f"重载失败：{e}")
            return
        yield event.plain_result("配置已重载，后台任务已重启。")

    @blog.command("parsers")
    async def blog_parsers(self, event: AstrMessageEvent):
        """列出可用的解析器"""
        names = parser_registry.names()
        yield event.plain_result("可用解析器：" + "、".join(names))

    def _apply_config(self):
        """把当前 config 应用到各组件（reload 用）。"""
        self.fetcher.timeout = int(self.config.get("request_timeout", 15))
        self.fetcher.retries = int(self.config.get("request_retries", 2))
        self.renderer.max_articles = int(self.config.get("max_articles", 5))
        self.renderer.show_cover = bool(self.config.get("show_cover", True))
        self.renderer.show_summary = bool(self.config.get("show_summary", True))
        self.scheduler.interval_minutes = max(1.0, float(self.config.get("check_interval", 30)))
        self.scheduler.max_articles_per_push = int(self.config.get("max_articles_per_push", 3))
        self._default_blog = str(self.config.get("default_blog", "")).strip()
