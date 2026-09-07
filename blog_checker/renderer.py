"""消息渲染：把文章列表渲染为 MessageChain 组件列表。"""

from __future__ import annotations

from astrbot.api.event import MessageChain

from .models import Article


class Renderer:
    """把数据渲染成消息组件。想改样式改这里就行。"""

    def __init__(self, max_articles: int = 5, show_cover: bool = True, show_summary: bool = True):
        self.max_articles = max_articles
        self.show_cover = show_cover
        self.show_summary = show_summary

    def article_text(self, article: Article, index: int | None = None) -> str:
        """单篇文章的文本块。"""
        prefix = f"{index}. " if index is not None else ""
        lines = [f"{prefix}《{article.title}》"]
        if article.author:
            lines.append(f"作者：{article.author}")
        if article.published:
            lines.append(f"时间：{article.published}")
        if self.show_summary and article.summary:
            lines.append(f"{article.summary}")
        lines.append(article.link)
        return "\n".join(lines)

    def latest(self, article: Article) -> MessageChain:
        """渲染“最新文章”消息：文本 + 封面图。"""
        chain = MessageChain()
        chain.message(f"📝 博客最新文章\n{self.article_text(article)}")
        if self.show_cover and article.cover:
            chain.url_image(article.cover)
        return chain

    def article_at(self, article: Article) -> MessageChain:
        """渲染推送消息：单篇。"""
        chain = MessageChain()
        chain.message(f"🔔 博客更新\n{self.article_text(article)}")
        if self.show_cover and article.cover:
            chain.url_image(article.cover)
        return chain

    def list_articles(self, articles: list[Article]) -> MessageChain:
        """渲染文章列表消息。"""
        chain = MessageChain()
        if not articles:
            chain.message("没有找到文章")
            return chain
        shown = articles[: self.max_articles]
        parts = [self.article_text(a, i + 1) for i, a in enumerate(shown)]
        text = f"📋 文章列表（共 {len(articles)} 篇）\n\n" + "\n\n".join(parts)
        if len(articles) > len(shown):
            text += f"\n\n（仅显示前 {len(shown)} 篇）"
        chain.message(text)
        return chain

    def blog_info(self, name: str, url: str, rss_url: str, parser: str, enabled: bool, subscribed_sessions: int) -> MessageChain:
        """渲染单个博客的信息。"""
        chain = MessageChain()
        status = "✅ 启用" if enabled else "⏸ 停用"
        lines = [f"📰 {name}", f"地址：{url}"]
        if rss_url:
            lines.append(f"RSS：{rss_url}")
        lines.append(f"解析器：{parser}")
        lines.append(f"状态：{status}")
        if subscribed_sessions:
            lines.append(f"订阅会话数：{subscribed_sessions}")
        chain.message("\n".join(lines))
        return chain
