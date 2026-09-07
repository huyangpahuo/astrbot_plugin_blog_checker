"""数据模型：博客与文章。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Article:
    """一篇文章的标准化表示（由解析器产出）。"""

    blog_name: str
    title: str
    link: str
    guid: str = ""
    author: str = ""
    published: str = ""
    summary: str = ""
    cover: str = ""

    def unique_key(self) -> str:
        """用于去重的唯一键：优先 guid，其次链接，最后标题。"""
        return self.guid or self.link or self.title

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# 动态添加博客时允许的解析器类型
PARSER_TYPES = ("auto", "rss", "html")


@dataclass
class Blog:
    """一个博客订阅源的配置。

    Attributes:
        name: 展示名与唯一标识。
        url: 博客主页地址（auto/html 模式的抓取入口）。
        rss_url: 显式指定的 RSS/Atom 地址；为空时 auto 模式会自动探测常见路径。
        parser: 解析器：auto / rss / html。
        enabled: 是否启用。
        html_selectors: html 解析器的 CSS 选择器，可覆盖默认值。
            键：item / title / link / date / cover。
        source: 来源标记，config=配置文件 / dynamic=命令动态添加。
    """

    name: str
    url: str
    rss_url: str = ""
    parser: str = "auto"
    enabled: bool = True
    html_selectors: dict[str, str] = field(default_factory=dict)
    source: str = "dynamic"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any], source: str = "dynamic") -> "Blog":
        known = {f for f in cls.__dataclass_fields__ if f != "source"}
        kwargs = {k: v for k, v in data.items() if k in known}
        blog = cls(**kwargs)
        blog.source = source
        return blog
