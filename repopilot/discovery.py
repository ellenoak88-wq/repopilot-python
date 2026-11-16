"""Public GitHub discovery feeds with bounded requests and persistent cache."""
import asyncio
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.parse import urlencode

import httpx

from .github import parse_repo

LANGUAGES = {"": "", "Python": "python", "JavaScript": "javascript", "TypeScript": "typescript",
             "Go": "go", "Rust": "rust", "Java": "java", "C++": "c++", "C#": "c#", "Ruby": "ruby"}
TOPICS = ("", "artificial-intelligence", "rag", "llm", "developer-tools", "web-framework")
TTL = 3600
COOLDOWN = 60


class DiscoveryUnavailable(Exception):
    pass


class Node:
    def __init__(self, tag, attrs):
        self.tag, self.attrs, self.children = tag, dict(attrs), []

    def nodes(self):
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.nodes()

    def text(self):
        if self.tag in ("script", "style", "svg"):
            return ""
        return " ".join(c.text() if isinstance(c, Node) else c for c in self.children)


class TrendingParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.articles, self.stack = [], []

    def handle_starttag(self, tag, attrs):
        if not self.stack and not (tag == "article" and "Box-row" in dict(attrs).get("class", "").split()):
            return
        node = Node(tag, attrs)
        if self.stack:
            self.stack[-1].children.append(node)
        else:
            self.articles.append(node)
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if self.stack:
            self.stack[-1].children.append(data)


def clean(value, limit=800):
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def number(value):
    match = re.fullmatch(r"([\d,]+(?:\.\d+)?)\s*([km]?)", clean(value).lower())
    if not match:
        return None
    return int(float(match[1].replace(",", "")) * {"": 1, "k": 1000, "m": 1000000}[match[2]])


def parse_trending(html):
    parser = TrendingParser()
    parser.feed(html)
    items, seen = [], set()
    for rank, article in enumerate(parser.articles, 1):
        nodes = list(article.nodes())
        heading = next((n for n in nodes if n.tag == "h2"), None)
        link = next((n for n in heading.nodes() if n.tag == "a"), None) if heading else None
        href = link.attrs.get("href", "") if link else ""
        try:
            if not href.startswith("/") or href.startswith("//"):
                continue
            repo = parse_repo(href[1:])
        except ValueError:
            continue
        if repo.lower() in seen:
            continue
        seen.add(repo.lower())
        description = next((clean(n.text()) for n in nodes if n.tag == "p"), "")
        language = next((clean(n.text(), 60) for n in nodes if n.attrs.get("itemprop") == "programmingLanguage"), "")
        metrics = {n.attrs.get("href"): number(n.text()) for n in nodes if n.tag == "a"}
        growth = next((re.search(r"([\d,.]+[km]?)\s+stars?\s+(?:today|this week|this month)", clean(n.text()), re.I)
                       for n in nodes if "float-sm-right" in n.attrs.get("class", "").split()), None)
        items.append(dict(id=repo, url=f"https://github.com/{repo}", description=description, language=language,
                          rank=rank, stars=metrics.get(f"/{repo}/stargazers"), forks=metrics.get(f"/{repo}/forks"),
                          periodStars=number(growth[1]) if growth else None))
    if not items and not (not parser.articles and "It looks like we don’t have any trending repositories" in html):
        raise DiscoveryUnavailable("GitHub Trending 页面暂时无法解析，请稍后重试或查看原榜单。")
    return items[:30]


class DiscoveryService:
    def __init__(self, store, *, transport=None, clock=time.time):
        self.store, self.transport, self.clock = store, transport, clock
        self.locks, self.attempts, self.failures = {}, {}, {}
        store.db.execute("CREATE TABLE IF NOT EXISTS discovery_cache (key TEXT PRIMARY KEY, payload TEXT NOT NULL, fetched_at REAL NOT NULL)")
        store.db.commit()

    async def get(self, *, source="trending", language="", since="daily", topic="", refresh=False):
        if source not in ("trending", "popular") or language not in LANGUAGES or since not in ("daily", "weekly", "monthly") or topic not in TOPICS:
            raise ValueError("推荐来源或筛选条件无效。")
        if source == "trending" and topic:
            raise ValueError("主题筛选仅适用于热门项目。")
        if source == "popular":
            since = "daily"  # Period is irrelevant for the popularity feed.
        key = json.dumps([source, language, since, topic])
        async with self.locks.setdefault(key, asyncio.Lock()):
            stamp = self.clock()
            row = self.store.db.execute("SELECT * FROM discovery_cache WHERE key=?", (key,)).fetchone()
            payload = json.loads(row["payload"]) if row else None
            age = stamp - row["fetched_at"] if row else float("inf")
            cooldown = stamp - self.attempts.get(key, float("-inf")) < COOLDOWN or age < COOLDOWN
            cached, warning = True, ""
            if cooldown and key in self.failures:
                warning = self.failures[key]
                if not payload:
                    raise DiscoveryUnavailable(warning)
            elif (refresh or age >= TTL) and not cooldown:
                self.attempts[key] = stamp
                try:
                    payload = await self.fetch(source, language, since, topic)
                except DiscoveryUnavailable as exc:
                    warning = str(exc)
                    self.failures[key] = warning
                    if not payload:
                        raise
                else:
                    self.failures.pop(key, None)
                    payload["fetchedAt"] = datetime.fromtimestamp(stamp, timezone.utc).isoformat()
                    with self.store.db:
                        self.store.db.execute("INSERT OR REPLACE INTO discovery_cache VALUES (?,?,?)", (key, json.dumps(payload), stamp))
                    cached, age = False, 0
            if refresh and cooldown and not warning:
                warning = "同一筛选条件每分钟最多刷新一次，当前显示缓存结果。"
            imported = {r["id"].lower(): r["id"] for r in self.store.list()}
            return {**payload, "items": [{**item, "importedId": imported.get(item["id"].lower())} for item in payload["items"]],
                    "cached": cached, "stale": bool(key in self.failures or age >= TTL), "warning": warning}

    async def fetch(self, source, language, since, topic):
        headers = {"User-Agent": "RepoPilot-Python"}
        if source == "trending":
            from urllib.parse import quote
            url = "https://github.com/trending" + ("/" + quote(LANGUAGES[language], safe="") if language else "")
            url += "?" + urlencode({"since": since})
            source_url = url
            headers["Accept"] = "text/html"
        else:
            cutoff = (datetime.fromtimestamp(self.clock(), timezone.utc) - timedelta(days=180)).date().isoformat()
            query = f"stars:>=1000 archived:false fork:false is:public pushed:>={cutoff}"
            if language:
                query += f" language:{language}"
            if topic:
                query += f" topic:{topic}"
            url = "https://api.github.com/search/repositories?" + urlencode({"q": query, "sort": "stars", "order": "desc", "per_page": 30})
            source_url = "https://github.com/search?" + urlencode({"q": query, "type": "repositories", "s": "stars", "o": "desc"})
            headers.update({"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
            if os.getenv("GITHUB_TOKEN"):
                headers["Authorization"] = "Bearer " + os.environ["GITHUB_TOKEN"]
        try:
            async with asyncio.timeout(30), httpx.AsyncClient(timeout=20, transport=self.transport, follow_redirects=False) as client:
                async with client.stream("GET", url, headers=headers) as response:
                    if response.status_code in (403, 429):
                        raise DiscoveryUnavailable("GitHub 请求被限制，请稍后重试；热门项目可配置 GITHUB_TOKEN 提高额度。")
                    response.raise_for_status()
                    raw = bytearray()
                    async for part in response.aiter_bytes():
                        raw.extend(part)
                        if len(raw) > 3_000_000:
                            raise DiscoveryUnavailable("GitHub 返回内容过大，请稍后重试。")
            partial = False
            if source == "trending":
                items = parse_trending(raw.decode("utf-8"))
            else:
                result = json.loads(raw)
                if not isinstance(result, dict) or not isinstance(result.get("items"), list):
                    raise ValueError("Invalid search response")
                partial = bool(result.get("incomplete_results"))
                items, seen = [], set()
                for rank, item in enumerate(result["items"][:30], 1):
                    if item.get("private") is not False or item.get("archived") or item.get("fork"):
                        continue
                    repo = parse_repo(item.get("full_name"))
                    if repo.lower() in seen:
                        continue
                    seen.add(repo.lower())
                    def count(name):
                        val = item.get(name)
                        return val if type(val) is int and val >= 0 else None
                    items.append(dict(id=repo, url=f"https://github.com/{repo}", description=clean(item.get("description")),
                                      language=clean(item.get("language"), 60), stars=count("stargazers_count"), forks=count("forks_count"),
                                      rank=rank, periodStars=None))
            return dict(source=source, sourceUrl=source_url, language=language, since=since, topic=topic, items=items, partial=partial)
        except (httpx.HTTPError, TimeoutError) as exc:
            raise DiscoveryUnavailable("无法连接 GitHub，请检查网络或代理后重试。") from exc
        except (ValueError, TypeError, AttributeError, KeyError) as exc:
            raise DiscoveryUnavailable("GitHub 返回格式异常，请稍后重试。") from exc
