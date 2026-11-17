"""SQLite storage; schema remains compatible with the Node.js edition.

Used synchronously on one ASGI event loop, in a single worker process. No
transaction contains awaits, so mutations are atomic relative to requests.
"""
import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .retrieval import chunk_file


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def index_version(sha, files):
    digest = hashlib.sha256(str(sha).encode())
    for file in sorted(files, key=lambda f: f["path"]):
        digest.update(json.dumps([file["path"], file["content"]], ensure_ascii=False).encode())
    return digest.hexdigest()


class Store:
    def __init__(self, filename: str | Path):
        if str(filename) != ":memory:":
            Path(filename).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(filename), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS repositories (id TEXT PRIMARY KEY, metadata TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS files (repo TEXT, path TEXT, content TEXT, PRIMARY KEY(repo,path));
            CREATE TABLE IF NOT EXISTS chats (id INTEGER PRIMARY KEY, repo TEXT, payload TEXT);
            CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, repo TEXT NOT NULL,
                title TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        """)
        if "session_id" not in [r["name"] for r in self.db.execute("PRAGMA table_info(chats)")]:
            self.db.execute("ALTER TABLE chats ADD COLUMN session_id TEXT")
        with self.db:
            for row in self.db.execute("SELECT id FROM repositories").fetchall():
                session = self.default_session(row["id"])
                self.db.execute("UPDATE chats SET session_id=? WHERE repo=? AND session_id IS NULL", (session["id"], row["id"]))
            self.db.execute("CREATE INDEX IF NOT EXISTS chats_session_idx ON chats(session_id,id)")
            for repo in self.list():
                if not repo.get("indexVersion"):
                    repo["indexVersion"] = index_version(repo.get("sha"), self.files(repo["id"]))
                    self.db.execute("UPDATE repositories SET metadata=? WHERE id=?", (json.dumps(repo, ensure_ascii=False), repo["id"]))
                for row in self.db.execute("SELECT id,payload FROM chats WHERE repo=?", (repo["id"],)).fetchall():
                    item = json.loads(row["payload"])
                    if "indexVersion" not in item:
                        item.update(repoSha=repo.get("sha"), indexVersion=repo["indexVersion"], versionInferred=True)
                        self.db.execute("UPDATE chats SET payload=? WHERE id=?", (json.dumps(item, ensure_ascii=False), row["id"]))

    def _one(self, sql, args=()):
        row = self.db.execute(sql, args).fetchone()
        return dict(row) if row else None

    def _session(self, repo, title="新会话"):
        stamp = now()
        item = dict(id=str(uuid.uuid4()), repo=repo, title=title, created_at=stamp, updated_at=stamp)
        self.db.execute("INSERT INTO sessions VALUES (?,?,?,?,?)", tuple(item.values()))
        return item

    def create_session(self, repo, title="新会话"):
        with self.db:
            return self._session(repo, title)

    def default_session(self, repo):
        return self._one("SELECT * FROM sessions WHERE repo=? ORDER BY updated_at DESC,rowid DESC LIMIT 1", (repo,)) or self._session(repo, "默认会话")

    def get_session(self, repo, session_id):
        return self._one("SELECT * FROM sessions WHERE repo=? AND id=?", (repo, session_id))

    def save_repository(self, meta, files):
        # Validate before any mutation. Duplicate paths must not silently overwrite.
        if len({f["path"] for f in files}) != len(files):
            raise sqlite3.IntegrityError("Duplicate file paths")
        previous = self.get(meta["id"])
        old = {f["path"]: f["content"] for f in self.files(meta["id"])}
        new = {f["path"]: f["content"] for f in files}
        stamp = now()
        item = {**meta, "fileCount": len(files), "chunkCount": sum(len(chunk_file(f["path"], f["content"])) for f in files),
                "indexedAt": stamp, "indexVersion": index_version(meta.get("sha"), files)}
        item["lastSync"] = {"at": stamp, "previousSha": previous.get("sha") if previous else None,
                            "added": len(new.keys() - old.keys()), "removed": len(old.keys() - new.keys()),
                            "updated": sum(old[p] != new[p] for p in old.keys() & new.keys()),
                            "unchanged": sum(old[p] == new[p] for p in old.keys() & new.keys())}
        with self.db:
            self.db.execute("INSERT INTO repositories VALUES (?,?) ON CONFLICT(id) DO UPDATE SET metadata=excluded.metadata",
                            (meta["id"], json.dumps(item, ensure_ascii=False)))
            self.db.executemany("DELETE FROM files WHERE repo=? AND path=?", [(meta["id"], p) for p in old.keys() - new.keys()])
            self.db.executemany("INSERT INTO files VALUES (?,?,?) ON CONFLICT(repo,path) DO UPDATE SET content=excluded.content",
                                [(meta["id"], p, content) for p, content in new.items() if old.get(p) != content])
            self.default_session(meta["id"])
        return item

    def get_chat(self, repo, chat_id):
        row = self._one("SELECT id,session_id,payload FROM chats WHERE repo=? AND id=?", (repo, chat_id))
        return {**json.loads(row["payload"]), "id": row["id"], "sessionId": row["session_id"]} if row else None

    def list(self):
        return [json.loads(r[0]) for r in self.db.execute("SELECT metadata FROM repositories ORDER BY rowid DESC")]

    def get(self, repo):
        row = self._one("SELECT metadata FROM repositories WHERE id=?", (repo,))
        return json.loads(row["metadata"]) if row else None

    def files(self, repo):
        return [dict(r) for r in self.db.execute("SELECT path,content FROM files WHERE repo=? ORDER BY path", (repo,))]

    def file(self, repo, path):
        return self._one("SELECT path,content FROM files WHERE repo=? AND path=?", (repo, path))

    def chunks(self, repo):
        return [c for f in self.files(repo) for c in chunk_file(f["path"], f["content"])]

    def sessions(self, repo, query=""):
        sql = """SELECT s.*, (SELECT COUNT(*) FROM chats c WHERE c.session_id=s.id) AS messageCount
            FROM sessions s WHERE s.repo=? AND (instr(lower(s.title),lower(?))>0 OR EXISTS
            (SELECT 1 FROM chats c WHERE c.session_id=s.id AND
            (instr(lower(json_extract(c.payload,'$.question')),lower(?))>0 OR
             instr(lower(json_extract(c.payload,'$.answer')),lower(?))>0)))
            ORDER BY s.updated_at DESC,s.rowid DESC"""
        return [dict(r) for r in self.db.execute(sql, (repo, query, query, query))]

    def rename_session(self, repo, session_id, title):
        with self.db:
            self.db.execute("UPDATE sessions SET title=?,updated_at=? WHERE repo=? AND id=?", (title, now(), repo, session_id))

    def delete_session(self, repo, session_id):
        with self.db:
            self.db.execute("DELETE FROM chats WHERE repo=? AND session_id=?", (repo, session_id))
            self.db.execute("DELETE FROM sessions WHERE repo=? AND id=?", (repo, session_id))
            self.default_session(repo)

    def history(self, repo, session_id=None):
        session_id = session_id or self.default_session(repo)["id"]
        rows = self.db.execute("SELECT id,session_id,payload FROM chats WHERE repo=? AND session_id=? ORDER BY id DESC LIMIT 20", (repo, session_id)).fetchall()
        return [{**json.loads(r["payload"]), "id": r["id"], "sessionId": r["session_id"]} for r in reversed(rows)]

    def add_chat(self, repo, payload, session_id=None, replace_id=None):
        session = self.get_session(repo, session_id) if session_id else self.default_session(repo)
        if not session:
            raise ValueError("会话不存在。")
        with self.db:
            current = self.get(repo) or {}
            payload = {"repoSha": current.get("sha"), "indexVersion": current.get("indexVersion"), **payload}
            encoded = json.dumps(payload, ensure_ascii=False)
            if replace_id is not None:
                latest = self._one("SELECT id FROM chats WHERE repo=? AND session_id=? ORDER BY id DESC LIMIT 1", (repo, session["id"]))
                if not latest or latest["id"] != replace_id:
                    raise ValueError("只能重新生成当前会话最后一条回答。")
                self.db.execute("UPDATE chats SET payload=? WHERE id=? AND session_id=?", (encoded, replace_id, session["id"]))
                chat_id = replace_id
            else:
                chat_id = self.db.execute("INSERT INTO chats(repo,payload,session_id) VALUES (?,?,?)", (repo, encoded, session["id"])).lastrowid
            self.db.execute("UPDATE sessions SET updated_at=? WHERE id=?", (now(), session["id"]))
        return {**payload, "id": chat_id, "sessionId": session["id"]}

    def close(self):
        self.db.close()
