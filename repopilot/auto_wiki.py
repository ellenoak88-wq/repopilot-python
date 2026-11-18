"""Maintain project reference pages in the background from indexed files and newly explored ranges."""
import asyncio
import hashlib
import json
import logging
import re
import time
from pathlib import PurePosixPath
from urllib.parse import quote

from .models import select_model
from .retrieval import search
from .store import now
from .wiki_author import normalize_article, write_articles


TOPICS = {"overview": "项目用途与概念", "setup": "安装与配置", "implementation": "实现与入口",
          "testing": "测试与验证", "guides": "使用与贡献指南"}


def topic_for(path):
    parts = PurePosixPath(path.lower())
    name = parts.name
    if name.startswith("readme") and len(parts.parts) == 1:
        return "overview"
    if re.search(r"(^|[/_.-])(tests?|specs?)([/_.-]|$)", path.lower()):
        return "testing"
    if name.startswith(("docker", "compose", "requirements", "package.json", "pyproject", "makefile",
                        "cargo.toml", "go.mod", ".env", "setup.", "pom.xml")) or parts.suffix in (".yaml", ".yml", ".toml"):
        return "setup"
    if parts.suffix in (".md", ".rst", ".adoc", ".txt"):
        return "guides"
    return "implementation"


def outline(key, sources):
    facts = []
    for s in sources:
        headings = re.findall(r"^#{1,4}\s+([^\n]{1,80})$", s["text"], re.M)[:3]
        symbols = re.findall(r"^\s*(?:export\s+)?(?:async\s+)?(?:def|class|function|func)\s+(\w+)", s["text"], re.M)[:4]
        names = headings or symbols
        label = "可定位的章节" if headings else "可定位的定义"
        text = f"{s['path']} 第 {s['start']}–{s['end']} 行"
        facts.append({"text": text + ("，" + label + "包括 " + "、".join(names) if names else "。"),
                      "sources": [s["id"]]})
    return {"title": TOPICS[key], "facts": facts, "links": []}


def wiki_config():
    try:
        return select_model()
    except ValueError:
        # A broken chat model preference must not prevent indexing or browsing repositories.
        return {"model": "", "key": ""}


class AutoWiki:
    def __init__(self, store, author=write_articles, config=wiki_config):
        self.store, self.author, self.config = store, author, config
        self.workers = {}
        self.limit = asyncio.Semaphore(1)
        self.store.db.executescript("""
            CREATE TABLE IF NOT EXISTS wiki_editions (
                repo TEXT PRIMARY KEY, version TEXT, engine TEXT, generation INTEGER, dirty INTEGER,
                pages TEXT, published_version TEXT, error TEXT, retry_at REAL
            );
            CREATE TABLE IF NOT EXISTS wiki_focus (
                repo TEXT, path TEXT, start INTEGER, PRIMARY KEY(repo,path,start)
            );
        """)

    def request(self, repo, sources=()):
        db = self.store.db
        rid, version = repo["id"], repo["indexVersion"]
        config = self.config()
        engine = config["model"] if config.get("key") else ""
        with db:
            row = self.store._one("SELECT version,engine,dirty,retry_at FROM wiki_editions WHERE repo=?", (rid,))
            if row is None:
                db.execute("INSERT INTO wiki_editions VALUES (?,?,?,0,1,'{}','','',0)", (rid, version, engine))
                # Upgrade existing projects using only source locations from recent saved turns.
                old = db.execute("""SELECT json_extract(payload,'$.sources') FROM chats
                                    WHERE repo=? ORDER BY id DESC LIMIT 30""", (rid,))
                sources = [s for r in reversed(old.fetchall()) for s in json.loads(r[0] or "[]")] + list(sources)
            points = []
            for source in sources:
                if not isinstance(source, dict) or not isinstance(source.get("path"), str):
                    continue
                start = source.get("start")
                if type(start) is not int or start < 1:
                    continue
                end = source.get("end", start)
                end = min(end, start + 99) if type(end) is int and end >= start else start
                points.extend((rid, source["path"], line) for line in reversed(range(start, end + 1, 28)))
            before = db.total_changes
            db.executemany("INSERT OR IGNORE INTO wiki_focus VALUES (?,?,?)", points)
            changed = db.total_changes != before or not row or (version, engine) != (row["version"], row["engine"])
            if changed:
                db.execute("UPDATE wiki_editions SET dirty=1,generation=generation+1,version=?,engine=?,retry_at=0 WHERE repo=?",
                           (version, engine, rid))
            elif not row["dirty"] or row["retry_at"] > time.time():
                return
        if rid not in self.workers:
            self.workers[rid] = asyncio.create_task(self._worker(rid))

    def forget(self, rid):
        task = self.workers.pop(rid, None)
        if task:
            task.cancel()

    async def close(self):
        tasks = list(self.workers.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def packets(self, repo):
        rid = repo["id"]
        db = self.store.db
        paths = [r[0] for r in db.execute("SELECT path FROM files WHERE repo=? ORDER BY length(path),path", (rid,))]
        focus = db.execute("SELECT path,start FROM wiki_focus WHERE repo=? ORDER BY rowid DESC", (rid,))
        paths_set = set(paths)
        selected = {k: [] for k in TOPICS}

        def include(path, start, limit):
            bucket = selected[topic_for(path)]
            if len(bucket) < limit and (path, start) not in bucket:
                bucket.append((path, start))

        for path in paths:
            include(path, 1, 2)
        for path, start in focus:
            if path in paths_set:
                include(path, start, 5)
        for path in paths:
            include(path, 1, 5)
        packets = {}
        for key, points in selected.items():
            sources = []
            for path, start in points:
                lines = self.store.file(rid, path)["content"].split("\n")[start-1:start+27]
                if not lines:
                    continue
                snippet = "\n".join(lines)[:1500]
                if not snippet.strip():
                    continue
                end = start + snippet.count("\n")
                url = None if repo.get("demo") or not repo.get("url") else (
                    f"{repo['url']}/blob/{repo['sha']}/{quote(path, safe='/')}#L{start}-L{end}")
                sources.append({"id": f"S{len(sources)+1}", "path": path, "start": start, "end": end, "text": snippet, "url": url})
            if sources:
                packets[key] = sources
        return packets

    def retain_evidence(self, repo, page, old, sampled, live_topics):
        """Keep previously learned facts outside this batch while their exact evidence still exists."""
        by_ref = {s["id"]: s for s in old.get("sources", [])}
        current = {(s["path"], s["start"], s["text"]): s["id"] for s in sampled}
        checked = {}

        def resolve(ref):
            source = by_ref[ref]
            path, start = source["path"], source["start"]
            identity = (path, start, source["text"])
            if identity in current:
                return current[identity]
            if ref not in checked:
                file = self.store.file(repo["id"], path)
                snippet = "\n".join(file["content"].split("\n")[start-1:start+27])[:1500] if file else None
                checked[ref] = snippet == source["text"]
            if not checked[ref]:
                return None
            new_ref = f"S{len(page['sources']) + 1}"
            url = None if repo.get("demo") or not repo.get("url") else (
                f"{repo['url']}/blob/{repo['sha']}/{quote(path, safe='/')}#L{start}-L{source['end']}")
            page["sources"].append({**source, "id": new_ref, "url": url})
            current[identity] = new_ref
            return new_ref

        base = set(current)
        for field, label in (("facts", "text"), ("links", "reason")):
            seen = {item[label] for item in page[field]}
            for item in old.get(field, []):
                if item[label] in seen or (field == "links" and item["to"] not in live_topics):
                    continue
                cited = [by_ref[r] for r in item["sources"]]
                if (page["engine"] or not old.get("engine")) and all(
                    (s["path"], s["start"], s["text"]) in base for s in cited
                ):
                    continue
                refs = [resolve(ref) for ref in item["sources"]]
                if all(refs):
                    page[field].append({**item, "sources": refs})
                    seen.add(item[label])

    async def build(self, repo, previous, config):
        packets = self.packets(repo)
        topics = {k: TOPICS[k] for k in packets}
        engine = config["model"] if config.get("key") else ""
        pages, changes = {}, {}
        for key, sources in packets.items():
            material = [{k: v for k, v in s.items() if k != "url"} for s in sources]
            signature = hashlib.sha256(json.dumps(["reviewed-v1", material, list(topics)], sort_keys=True).encode()).hexdigest()
            old = previous.get(key, {})
            if old.get("signature") == signature and old.get("engine") == engine:
                ids = {s["id"] for s in sources}
                pages[key] = {**old, "sources": list(sources),
                              **{f: [item for item in old[f] if set(item["sources"]) <= ids] for f in ("facts", "links")}}
            else:
                pages[key] = {**outline(key, sources), "signature": signature, "engine": "", "sources": list(sources)}
                changes[key] = material
        error = ""
        if changes and engine:
            try:
                async with asyncio.timeout(175):
                    result = await self.author(changes, topics, config)
                for key in changes:
                    try:
                        article = normalize_article(result[key], packets[key], topics, key)
                    except (KeyError, IndexError, ValueError, TypeError, AttributeError):
                        error = "部分专题未通过引用校验，已提供源码导览。两分钟后再次打开项目可重试。"
                        continue
                    pages[key].update(article, engine=engine)
            except Exception as exc:
                logging.getLogger(__name__).warning("Wiki author could not finish (%s)", type(exc).__name__)
                error = "部分专题尚未完成模型提炼，可先阅读源码导览。两分钟后再次打开项目会自动重试。"
        for key, page in pages.items():
            page["sha"] = repo.get("sha")
            old = previous.get(key, {})
            self.retain_evidence(repo, page, old, packets[key], topics)
            changed = any(page.get(k) != old.get(k) for k in ("facts", "links", "title", "signature", "engine"))
            page["revision"] = old.get("revision", 0) + int(changed)
            page["updatedAt"] = now() if changed else old.get("updatedAt")
        return pages, error

    async def _worker(self, rid):
        try:
            async with self.limit:
                while True:
                    row = self.store._one("SELECT * FROM wiki_editions WHERE repo=?", (rid,))
                    repo = self.store.get(rid)
                    if not row or not repo or not row["dirty"]:
                        return
                    pages, error = await self.build(repo, json.loads(row["pages"]), self.config())
                    with self.store.db:
                        updated = self.store.db.execute("""UPDATE wiki_editions SET pages=?,published_version=?,dirty=?,
                            error=?,retry_at=? WHERE repo=? AND generation=?""",
                            (json.dumps(pages, ensure_ascii=False), repo["indexVersion"], int(bool(error)), error,
                             time.time()+120 if error else 0, rid, row["generation"]))
                    if updated.rowcount:
                        return
        except Exception as exc:
            logging.getLogger(__name__).warning("Wiki maintenance failed (%s)", type(exc).__name__)
            with self.store.db:
                self.store.db.execute("UPDATE wiki_editions SET error=?,retry_at=?,dirty=1 WHERE repo=?",
                                      ("Wiki 更新中断，已保留上次版本。两分钟后再次打开项目可重试。",
                                       time.time()+120, rid))
        finally:
            if self.workers.get(rid) is asyncio.current_task():
                self.workers.pop(rid, None)

    def view(self, repo):
        row = self.store._one("SELECT * FROM wiki_editions WHERE repo=?", (repo["id"],))
        pages = json.loads(row["pages"]) if row else {}
        for page in pages.values():
            page["related"] = []
        for key, page in pages.items():
            links = list(page["links"])
            for target, other in pages.items():
                if target == key or any(link["to"] == target for link in links):
                    continue
                match = next(((s, ref["path"]) for s in page["sources"] for ref in other["sources"]
                              if ref["path"] in s["text"]), None)
                if match:
                    source, path = match
                    links.append({"to": target, "reason": "源码片段中提到 " + path, "sources": [source["id"]]})
            for link in links:
                target = link["to"]
                if target not in pages:
                    continue
                page["related"].append({"key": target, "label": pages[target]["title"], "reason": link["reason"],
                                        "sources": link["sources"]})
        for key, page in pages.items():
            for link in tuple(page["related"]):
                target = pages[link["key"]]
                if not any(existing["key"] == key for existing in target["related"]):
                    target["related"].append({"key": key, "label": page["title"], "reason": "被此专题引用",
                                              "sources": []})
        return {"pages": pages, "version": row["published_version"] if row else "",
                "currentVersion": repo["indexVersion"], "running": repo["id"] in self.workers,
                "error": row["error"] if row else ""}

    def hints(self, repo, question):
        view = self.view(repo)
        if view["version"] != repo["indexVersion"]:
            return []
        docs = [{"key": k, "path": p["title"], "text": " ".join(f["text"] for f in p["facts"])}
                for k, p in view["pages"].items() if p["engine"]]
        return [{"title": d["path"], "paths": list(dict.fromkeys(s["path"] for s in view["pages"][d["key"]]["sources"]))}
                for d in search(docs, question, 2)]

