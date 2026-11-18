"""Repository-scoped topic pages built from immutable answer and evidence snapshots."""
import hashlib
import json
import sqlite3
import uuid

from .store import now


class WikiConflict(ValueError):
    pass


class Wiki:
    def __init__(self, store):
        self.store = store
        store.db.executescript("""
            CREATE TABLE IF NOT EXISTS wiki_pages (
                id TEXT PRIMARY KEY, repo TEXT NOT NULL, title TEXT NOT NULL,
                title_key TEXT NOT NULL, revision INTEGER NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE(repo, title_key)
            );
            CREATE TABLE IF NOT EXISTS wiki_entries (
                repo TEXT NOT NULL, page_id TEXT NOT NULL, revision INTEGER NOT NULL,
                origin_key TEXT NOT NULL, snapshot TEXT NOT NULL,
                PRIMARY KEY(page_id, revision), UNIQUE(page_id, origin_key)
            );
        """)

    def pages(self, repo):
        return [dict(row) for row in self.store.db.execute("""
            SELECT id, title, revision, updated_at AS updatedAt FROM wiki_pages
            WHERE repo=? ORDER BY updated_at DESC, rowid DESC
        """, (repo,))]

    def _page(self, repo, page_id):
        if not isinstance(page_id, str):
            raise ValueError("请选择专题页。")
        page = self.store._one("""
            SELECT id, title, revision, updated_at AS updatedAt FROM wiki_pages WHERE repo=? AND id=?
        """, (repo, page_id))
        if not page:
            raise ValueError("该项目中找不到此专题页。")
        return page

    def detail(self, repo, page_id):
        page = self._page(repo["id"], page_id)
        entries = self.store.db.execute("""
            SELECT snapshot FROM wiki_entries WHERE page_id=? AND repo=? ORDER BY revision
        """, (page_id, repo["id"]))
        return {**page, "entries": [json.loads(row[0]) for row in entries],
                "currentIndexVersion": repo["indexVersion"]}

    def delete(self, repo, page_id, revision):
        db = self.store.db
        with db:
            page = self._page(repo, page_id)
            if type(revision) is not int or revision != page["revision"]:
                raise WikiConflict("专题已更新，请重新打开后再删除。")
            db.execute("DELETE FROM wiki_entries WHERE repo=? AND page_id=?", (repo, page_id))
            db.execute("DELETE FROM wiki_pages WHERE repo=? AND id=?", (repo, page_id))
        return {"deleted": page_id}

    def draft(self, repo, chat_id):
        if type(chat_id) is not int:
            raise ValueError("回答 ID 无效。")
        chat = self.store.get_chat(repo["id"], chat_id)
        if not chat or not chat.get("answer", "").strip():
            raise ValueError("该项目中找不到可保存的回答。")
        snapshot = {key: chat.get(key) for key in (
            "sessionId", "question", "answer", "mode", "sources", "memoriesUsed",
            "repoSha", "indexVersion", "versionInferred"
        )}
        snapshot["chatId"] = chat_id
        fingerprint = hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()
        return {**snapshot, "originKey": fingerprint}

    def save(self, repo, body, *, merge):
        db = self.store.db
        with db:
            draft = self.draft(repo, body.get("chatId"))
            if body.get("originKey") != draft["originKey"]:
                raise WikiConflict("原回答已变化，请重新打开预览。")
            content = body.get("content", draft["answer"])
            if not isinstance(content, str) or not content.strip():
                raise ValueError("请填写要保存的结论。")
            if "content" in body and len(content) > 12000:
                raise ValueError("编辑后的结论最多 12000 字。")
            draft["content"] = content.strip()
            stamp = now()
            if merge:
                page = self._page(repo["id"], body.get("pageId"))
                page_id = page["id"]
                if type(body.get("revision")) is not int or body["revision"] != page["revision"]:
                    raise WikiConflict("专题页已更新，请重新预览后再合并。")
                duplicate = db.execute("SELECT 1 FROM wiki_entries WHERE page_id=? AND origin_key=?",
                                       (page_id, draft["originKey"])).fetchone()
                if duplicate:
                    raise WikiConflict("这条回答已经收录在此专题页。")
                revision = page["revision"] + 1
                db.execute("UPDATE wiki_pages SET revision=?, updated_at=? WHERE id=?", (revision, stamp, page_id))
            else:
                title = body.get("title")
                if not isinstance(title, str) or not 1 <= len(title.strip()) <= 120:
                    raise ValueError("专题标题需为 1–120 字。")
                title = title.strip()
                page_id, revision = str(uuid.uuid4()), 1
                try:
                    db.execute("INSERT INTO wiki_pages VALUES (?,?,?,?,?,?)",
                               (page_id, repo["id"], title, title.casefold(), revision, stamp))
                except sqlite3.IntegrityError as exc:
                    raise WikiConflict("项目中已有同名专题，请选择已有专题合并。") from exc
            db.execute("INSERT INTO wiki_entries VALUES (?,?,?,?,?)",
                       (repo["id"], page_id, revision, draft["originKey"], json.dumps(draft, ensure_ascii=False)))
        return {"id": page_id, "revision": revision}

