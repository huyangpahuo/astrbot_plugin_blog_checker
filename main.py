from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star

BLOG_URL = "https://funingna-wakawaka.github.io/"


class BlogCheckerPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)

    def get_cover_image(self, article_url: str):
        resp = requests.get(article_url, timeout=15)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")

        img = soup.find("img", src=True)
        if not img:
            return None

        return urljoin(article_url, img["src"])

    def get_latest_post(self):
        resp = requests.get(BLOG_URL, timeout=15)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        blocks = soup.find_all(["article", "section", "div"])

        for block in blocks:
            text = block.get_text(" ", strip=True)
            if "点击阅读" not in text:
                continue

            title_tag = block.find(["h1", "h2", "h3", "h4"])
            read_link = None

            for a in block.find_all("a", href=True):
                a_text = a.get_text(strip=True)
                if "点击阅读" in a_text or "阅读" in a_text:
                    read_link = a
                    break

            if title_tag and read_link:
                article_link = urljoin(BLOG_URL, read_link["href"])
                cover = self.get_cover_image(article_link)

                return {
                    "title": title_tag.get_text(strip=True),
                    "link": article_link,
                    "cover": cover
                }

        return None

    @filter.command("检查博客")
    async def check_blog(self, event: AstrMessageEvent):
        try:
            post = self.get_latest_post()
            if not post:
                yield event.plain_result("没有读取到最新文章")
                return

            msg = f"博客最新文章：\n《{post['title']}》\n{post['link']}"
            if post.get("cover"):
                msg += f"\n封面：{post['cover']}"

            yield event.plain_result(msg)
        except Exception as e:
            yield event.plain_result(f"检查失败：{e}")