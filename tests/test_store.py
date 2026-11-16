import json
import sqlite3

import pytest

from repopilot.retrieval import chunk_file, search
from repopilot.store import Store
from repopilot.migrate import snapshot

META = {"id": "test/repo", "sha": "abc", "demo": True}
FILES = [{"path": "README.md", "content": "Installation\nRun python -m app\nGetting started"}]


def test_retrieval_lines_and_chinese_query():
    chunks = chunk_file("README.md", "\n".join(str(i) for i in range(150)))
    assert [(c["start"], c["end"]) for c in chunks] == [(1, 65), (54, 118), (107, 150)]
    assert search(chunk_file(**FILES[0]), "如何安装启动")[0]["path"] == "README.md"
    assert search([], "安装") == []


def test_session_isolation_regeneration_and_delete():
    s = Store(":memory:")
    s.save_repository(META, FILES)
    a = s.default_session(META["id"])
    b = s.create_session(META["id"], "第二个")
    c = s.add_chat(META["id"], {"question": "启动", "answer": "first"}, a["id"])
    assert s.history(META["id"], b["id"]) == []
    s.add_chat(META["id"], {"question": "启动", "answer": "second"}, a["id"], c["id"])
    assert len(s.history(META["id"], a["id"])) == 1
    assert s.sessions(META["id"], "second")[0]["id"] == a["id"]
    with pytest.raises(ValueError):
        s.add_chat(META["id"], {}, b["id"], c["id"])
    assert s.history(META["id"], a["id"])[0]["answer"] == "second"
    s.delete_session(META["id"], a["id"])
    s.delete_session(META["id"], b["id"])
    assert len(s.sessions(META["id"])) == 1
    assert s.file(META["id"], "../../.env") is None
    s.close()


def test_reindex_rolls_back_on_invalid_files():
    s = Store(":memory:")
    before = s.save_repository(META, FILES)
    with pytest.raises(sqlite3.IntegrityError):
        s.save_repository(META, FILES * 2)
    assert s.get(META["id"]) == before
    assert s.files(META["id"]) == FILES
    s.close()


def test_legacy_node_database_migration(tmp_path):
    path = tmp_path / "legacy.db"
    db = sqlite3.connect(path)
    db.executescript("CREATE TABLE repositories(id TEXT PRIMARY KEY,metadata TEXT); CREATE TABLE chats(id INTEGER PRIMARY KEY,repo TEXT,payload TEXT);")
    db.execute("INSERT INTO repositories VALUES (?,?)", (META["id"], json.dumps(META)))
    db.execute("INSERT INTO chats VALUES (1,?,?)", (META["id"], json.dumps({"question": "old", "answer": "preserved"})))
    db.commit()
    db.close()
    for _ in range(2):
        s = Store(path)
        assert s.history(META["id"])[0]["answer"] == "preserved"
        assert len(s.sessions(META["id"])) == 1
        s.close()


def test_snapshot_includes_wal_and_refuses_overwrite(tmp_path):
    source = tmp_path / "source.db"
    target = tmp_path / "copy.db"
    s = Store(source)
    s.save_repository(META, FILES)
    s.add_chat(META["id"], {"question": "test", "answer": "copied"})
    snapshot(source, target)
    copy = Store(target)
    assert copy.history(META["id"])[0]["answer"] == "copied"
    copy.close()
    with pytest.raises(ValueError):
        snapshot(source, target)
    assert s.history(META["id"])[0]["answer"] == "copied"
    s.close()
