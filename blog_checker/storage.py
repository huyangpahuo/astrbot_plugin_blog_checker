"""持久化存储：JSON 文件，记录已推送文章与动态订阅。

数据目录由框架提供（StarTools.get_data_dir），插件卸载重装后记录仍在。
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any

from .models import Article, Blog

SCHEMA_VERSION = 2


class Storage:
    """线程安全的 JSON 存储。

    文件结构::

        {
            "version": 2,
            "seen": {"博客名": ["文章唯一键", ...]},     # 每博客最多保留 max_seen 条
            "blogs": [...],                              # 动态添加的订阅
            "subscriptions": {"umo": ["博客名", ...]},   # 会话 -> 订阅的博客
        }
    """

    def __init__(self, data_dir: str, max_seen: int = 500):
        self.data_dir = data_dir
        self.max_seen = max_seen
        os.makedirs(data_dir, exist_ok=True)
        self._path = os.path.join(data_dir, "blog_checker_data.json")
        self._lock = threading.Lock()
        self._data: dict[str, Any] = {"version": SCHEMA_VERSION, "seen": {}, "blogs": [], "subscriptions": {}}
        self._load()

    # ---------- 基础 ----------

    def _load(self) -> None:
        try:
            with open(self._path, encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(raw, dict):
            return
        version = raw.get("version", 1)
        self._data["seen"] = raw.get("seen", {}) if isinstance(raw.get("seen"), dict) else {}
        # v1 迁移：旧版 seen 是 {blog_name: {key: title}} 平铺 dict
        if version < 2:
            self._data["seen"] = {
                k: list(v.keys()) if isinstance(v, dict) else list(v)
                for k, v in self._data["seen"].items()
            }
        self._data["blogs"] = raw.get("blogs", []) if isinstance(raw.get("blogs"), list) else []
        self._data["subscriptions"] = (
            raw.get("subscriptions", {}) if isinstance(raw.get("subscriptions"), dict) else {}
        )

    def _save(self) -> None:
        tmp = self._path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self._path)

    # ---------- 已推送文章 ----------

    def seen_keys(self, blog_name: str) -> set[str]:
        with self._lock:
            return set(self._data["seen"].get(blog_name, []))

    def mark_seen(self, blog_name: str, articles: list[Article]) -> None:
        """记录已处理的文章键，超出上限时丢弃最旧的（列表尾部）。"""
        with self._lock:
            keys = self._data["seen"].setdefault(blog_name, [])
            for a in articles:
                key = a.unique_key()
                if key not in keys:
                    keys.insert(0, key)
            del keys[self.max_seen:]
            self._save()

    # ---------- 动态博客 ----------

    def get_blogs(self) -> list[Blog]:
        with self._lock:
            return [Blog.from_dict(b, source="dynamic") for b in self._data["blogs"]]

    def upsert_blog(self, blog: Blog) -> bool:
        """按名字添加或更新动态博客。返回是否新增。"""
        with self._lock:
            for i, b in enumerate(self._data["blogs"]):
                if b.get("name") == blog.name:
                    self._data["blogs"][i] = blog.to_dict()
                    self._save()
                    return False
            self._data["blogs"].append(blog.to_dict())
            self._save()
            return True

    def remove_blog(self, name: str) -> bool:
        """删除动态博客，并清理所有会话对它的订阅。"""
        with self._lock:
            before = len(self._data["blogs"])
            self._data["blogs"] = [b for b in self._data["blogs"] if b.get("name") != name]
            removed = len(self._data["blogs"]) < before
            for umo in list(self._data["subscriptions"]):
                blogs = self._data["subscriptions"][umo]
                if name in blogs:
                    blogs.remove(name)
                    if not blogs:
                        del self._data["subscriptions"][umo]
            if removed:
                self._save()
            return removed

    # ---------- 会话订阅 ----------

    def subscribe(self, umo: str, blog_name: str) -> bool:
        """把会话umo订阅到博客。返回是否为新增订阅。"""
        with self._lock:
            blogs = self._data["subscriptions"].setdefault(umo, [])
            if blog_name in blogs:
                return False
            blogs.append(blog_name)
            self._save()
            return True

    def unsubscribe(self, umo: str, blog_name: str) -> bool:
        with self._lock:
            blogs = self._data["subscriptions"].get(umo, [])
            if blog_name not in blogs:
                return False
            blogs.remove(blog_name)
            if not blogs:
                del self._data["subscriptions"][umo]
            self._save()
            return True

    def subscriptions_of(self, umo: str) -> list[str]:
        with self._lock:
            return list(self._data["subscriptions"].get(umo, []))

    def all_subscriptions(self) -> dict[str, list[str]]:
        with self._lock:
            return {umo: list(blogs) for umo, blogs in self._data["subscriptions"].items()}
