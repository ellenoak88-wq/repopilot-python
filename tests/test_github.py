import base64

import httpx
import pytest

from repopilot.github import eligible, import_github, parse_repo


@pytest.mark.parametrize("value", ["http://localhost/a", "https://evil.com/a/b", "a/../b", "a/..", "a/.", None])
def test_invalid_repo(value):
    with pytest.raises(ValueError):
        parse_repo(value)


def test_filters_and_parser():
    assert parse_repo("https://github.com/acme/test.git/") == "acme/test"
    for path in [".env", "src/.env.local", "private.key", "node_modules/a.js", "vendor/a.py"]:
        assert not eligible(dict(path=path, type="blob", size=10))
    assert not eligible(dict(path="src/a.py", type="blob", size=10, mode="120000"))
    assert eligible(dict(path="src/a.py", type="blob", size=10))


async def test_import_pins_commit_and_falls_back_without_token_leak(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "test-secret")
    seen = []
    def handler(request):
        seen.append(str(request.url))
        if request.url.host == "raw.githubusercontent.com":
            assert "authorization" not in request.headers
            return httpx.Response(503)
        assert request.headers["authorization"] == "Bearer test-secret"
        path = request.url.path
        if path.endswith("/acme/repo"):
            data = dict(private=False, default_branch="main", name="repo")
        elif "/commits/" in path:
            data = {"sha": "pinned"}
        elif "/trees/" in path:
            data = {"truncated": False, "tree": [dict(path="README.md", type="blob", size=10, sha="blob")]}
        else:
            data = dict(encoding="base64", size=7, content=base64.b64encode(b"# hello").decode())
        return httpx.Response(200, json=data)
    result = await import_github("acme/repo", transport=httpx.MockTransport(handler))
    assert result["meta"]["sha"] == "pinned"
    assert result["files"][0]["content"] == "# hello"
    assert any("/pinned/README.md" in url for url in seen)
