# astrbot_plugin_blog_checker(用来学习astrbot)

AstrBot 博客检查器：多博客订阅、RSS/Atom 自动探测、HTML 兜底解析、定时轮询、新文章自动推送到订阅的会话。

## ✨ 功能

- **多博客管理**：WebUI 配置预置 + `blog add` 命令动态添加，互不冲突
- **智能解析**：优先 RSS/Atom（自动探测 `atom.xml` / `rss.xml` 等常见路径及页面 `<link>` 声明），无 RSS 的站点用 CSS 选择器 HTML 兜底
- **解析器可扩展**：内置 `rss` / `html` 两个解析器，支持 `@parser_registry.register("名字")` 注册自定义解析器
- **订阅推送**：任意会话（群聊/私聊）可订阅博客，后台定时轮询，新文章自动推送（含封面图）
- **防刷屏**：首次添加博客只记录不推送；单轮单博客推送条数可配置
- **持久化**：推送记录与动态订阅保存在 `data/plugin_data/astrbot_plugin_blog_checker/`，重启不丢
- **健壮性**：请求重试、超时配置、异常隔离（单个博客抓取失败不影响其他）

## 📦 安装

从 AstrBot 插件市场安装，或手动放到 `data/plugins/` 目录后重载。

依赖（首次加载会自动安装）：`requests`、`beautifulsoup4`、`feedparser`

## 🚀 快速开始

1. 在 WebUI 插件配置里填入博客地址（默认已预置一个），或直接用命令添加：

```
blog add 我的博客 https://example.com/
```

2. 在要接收推送的会话里订阅：

```
blog subscribe
```

完成。之后有新文章时会自动推送到该会话。

## 📖 指令

指令组为 `blog`（别名 `博客`）：

| 指令 | 权限 | 说明 |
| --- | --- | --- |
| `blog check [博客名]` | 所有人 | 查看最新一篇文章（带封面图） |
| `blog list` | 所有人 | 列出所有博客及订阅情况 |
| `blog list <博客名>` | 所有人 | 列出该博客最近文章 |
| `blog subscribe [博客名]` | 所有人 | 订阅更新推送（不填订阅全部） |
| `blog unsubscribe [博客名]` | 所有人 | 取消订阅（不填取消全部） |
| `blog parsers` | 所有人 | 列出可用解析器 |
| `blog add <名> <URL> [rss=... parser=...]` | 管理员 | 添加博客，如 `blog add 博客 https://a.com parser=html` |
| `blog remove <名>` | 管理员 | 删除动态添加的博客 |
| `blog fetch` | 管理员 | 立即轮询一轮 |
| `blog reload` | 管理员 | 重载配置并重启后台任务 |

## ⚙️ 配置（WebUI）

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `enable_push` | true | 启用后台轮询与推送 |
| `check_interval` | 30 | 轮询间隔（分钟） |
| `default_blog` | "" | 不带参数时默认使用的博客 |
| `blogs` | [] | 预置博客列表（name/url/rss_url/parser/enabled） |
| `max_articles` | 5 | 文章列表最多显示条数 |
| `max_articles_per_push` | 3 | 单轮单博客最多推送条数 |
| `show_cover` | true | 推送携带封面图 |
| `show_summary` | true | 消息中显示摘要 |
| `request_timeout` | 15 | HTTP 超时（秒） |
| `request_retries` | 2 | 失败重试次数 |

## 🧩 扩展开发

### 自定义解析器

在插件目录新建 Python 文件（会被 `blog_checker/__init__.py` 引入或插件加载时导入）：

```python
from astrbot_plugin_blog_checker.blog_checker.parsers import parser_registry, Article

@parser_registry.register("jsonfeed")
class JsonFeedParser:
    def parse(self, blog, content, base_url):
        # 解析 content（bytes），返回 list[Article]
        ...
```

注册后在添加博客时 `parser=jsonfeed` 即可使用。

### 模块结构

```
blog_checker/
├── models.py     # Article / Blog 数据模型
├── parsers.py    # 解析器接口 + rss/html 内置实现 + 注册表
├── fetcher.py    # HTTP 抓取、重试、RSS 自动探测
├── storage.py    # JSON 持久化（推送记录、动态订阅）
├── renderer.py   # 消息渲染（改消息样式改这里）
└── scheduler.py  # 后台轮询调度
```

## 📄 License

MIT
