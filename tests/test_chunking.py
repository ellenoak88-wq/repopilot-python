import json

import pytest

from repopilot.chunking import CHUNKER_VERSION, chunk_file, search_text
from repopilot.store import Store


@pytest.mark.parametrize(("path", "content", "targets"), [
    ("main.go", "package m\n// Run starts it.\nfunc (w *Worker) Run() {}\nfunc Stop() {}", ["Worker.Run", "Stop"]),
    ("index.ts", "export function a() { return 1 }\nexport const b = () => 2", ["a", "b"]),
    ("script.sh", "one() { echo 1; }\ntwo() { echo 2; }", ["one", "two"]),
    ("Makefile", "start:\n\tgo run .\ntest:\n\tgo test ./...", ["start", "test"]),
    ("Dockerfile", "FROM node AS web\nRUN npm ci\nFROM python\nCOPY --from=web /out /out", ["FROM node AS web", "FROM python"]),
    ("pyproject.toml", "[build-system]\nrequires=[]\n[project.scripts]\napp='cli:main'", ["build-system", "project.scripts"]),
    ("workflow.yml", "jobs:\n  linux:\n    runs-on: linux\n  mac:\n    runs-on: mac", ["jobs.linux", "jobs.mac"]),
    ("package.json", '{\n "scripts": {\n "build": "a",\n "test": "b"\n }\n}', ["scripts / build", "scripts / test"]),
    ("page.css", "div { color: red }\nspan { color: blue }", ["div", "span"]),
])
def test_sibling_units(path, content, targets):
    chunks = chunk_file(path, content)
    first, second = (next(c for c in chunks if c["scope"] == scope) for scope in targets)
    assert first["end"] < second["start"]
    for c in chunks:
        assert c["text"] == "\n".join(content.split("\n")[c["start"]-1:c["end"]])


def test_decorator_nested_scope_and_continuation():
    code = "class Runner:\n    @classmethod\n    def run(cls):\n        def check():\n            return 1\n        return check()"
    chunks = chunk_file("core.py", code)
    assert [(c["scope"], c["start"], c["end"]) for c in chunks] == [
        ("Runner", 1, 1), ("Runner.run", 2, 3), ("Runner.run.check", 4, 5), ("Runner.run", 6, 6)]
    assert "Runner.run" in search_text(chunks[-1]) and "Runner.run" not in chunks[-1]["text"]


def test_markdown_protects_examples_and_keeps_context_on_continuations():
    doc = "# Guide\n## Install\n```sh\n# comment\ncmd\n```\n\n" + ("next paragraph\n\n" * 7) + "## FAQ\nanswer"
    chunks = chunk_file("README.md", doc, size=8, overlap=2)
    assert len([c for c in chunks if c["scope"] == "Guide / Install"]) > 1
    assert any("# comment\ncmd" in c["text"] for c in chunks)
    assert chunks[-1]["scope"] == "Guide / FAQ"
    assert all(c["end"]-c["start"] < 8 for c in chunks)


def test_migration_refreshes_layout_preserving_source_version(tmp_path):
    db = tmp_path / "store.db"
    store = Store(db)
    meta = store.save_repository({"id": "r", "sha": "s"}, [{"path": "a.py", "content": "def a(): pass\ndef b(): pass"}])
    old = dict(meta, chunkCount=1)
    old.pop("chunkerVersion")
    with store.db:
        store.db.execute("UPDATE repositories SET metadata=?", (json.dumps(old),))
    store.close()
    store = Store(db)
    current = store.get("r")
    assert (current["chunkerVersion"], current["chunkCount"], current["indexVersion"]) == (CHUNKER_VERSION, 2, meta["indexVersion"])
    store.close()
