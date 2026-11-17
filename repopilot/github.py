"""Bounded, read-only public GitHub repository ingestion."""
import asyncio
import base64
import hashlib
import os
import re
from urllib.parse import quote

import httpx


def import_limits():
    def setting(name, default, minimum, maximum):
        raw = os.getenv(name, str(default))
        try:
            value = int(raw)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"{name} 必须为整数。") from exc
        if not minimum <= value <= maximum:
            raise ValueError(f"{name} 必须在 {minimum}–{maximum} 之间。")
        return value
    return {"maxFiles": setting("REPO_MAX_FILES", 1000, 1, 5000),
            "maxTotalBytes": setting("REPO_MAX_TOTAL_BYTES", 50_000_000, 1_000_000, 200_000_000),
            "maxFileBytes": 80000}


def select_files(candidates, limits):
    selected, reserved, file_skipped, byte_skipped = [], 0, 0, 0
    for file in candidates:
        if len(selected) >= limits["maxFiles"]:
            file_skipped += 1
        elif reserved + file["size"] > limits["maxTotalBytes"]:
            byte_skipped += 1
        else:
            selected.append(file)
            reserved += file["size"]
    return selected, file_skipped, byte_skipped


def parse_repo(value):
    if not isinstance(value, str):
        raise ValueError("请输入 GitHub 仓库地址或 owner/repo。")
    value = re.sub(r"^https://github\.com/", "", value.strip().rstrip("/"), flags=re.I)
    value = re.sub(r"\.git$", "", value)
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9-]*/[a-zA-Z0-9_.-]+", value) or value.split("/")[1] in (".", ".."):
        raise ValueError("仅支持 https://github.com/owner/repo 或 owner/repo。")
    return value


def eligible(file):
    path = file["path"]
    return (file.get("type") == "blob" and file.get("mode") != "120000" and file.get("size", 80001) <= 80000
            and not re.search(r"(^|/)(node_modules|vendor|dist|build|coverage|\.git|\.next)(/|$)", path, re.I)
            and not re.search(r"(^|/)(\.env[^/]*|.*\.(pem|key|p12)|package-lock\.json|pnpm-lock\.yaml|yarn\.lock)$", path, re.I)
            and bool(re.search(r"\.(md|mdx|txt|rst|js|jsx|mjs|cjs|ts|tsx|py|go|rs|java|rb|php|json|toml|yaml|yml|sh|css|html)$|(^|/)(Dockerfile|Makefile|LICENSE|README|CONTRIBUTING)$", path, re.I)))


def priority(file):
    path = file["path"]
    if re.search(r"(^|/)(readme|contributing)", path, re.I):
        return 0, path
    if path in ("package.json", "pyproject.toml", "Cargo.toml", "go.mod", "Dockerfile"):
        return 1, path
    if re.match(r"(src|lib|app)/", path):
        return 2, path
    return (3 if path.startswith("docs/") else 4), path


async def import_github(value, progress=lambda *_: None, *, transport=None, existing=None):
    repo = parse_repo(value)
    limits = import_limits()
    cached = {}
    for file in existing or []:
        raw = file["content"].encode("utf-8")
        blob = hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest()
        cached[(file["path"], blob)] = raw
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "RepoPilot-Python", "X-GitHub-Api-Version": "2022-11-28"}
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    async with httpx.AsyncClient(timeout=20, follow_redirects=False, transport=transport) as client:
        async def api(path):
            try:
                response = await client.get(f"https://api.github.com/repos/{repo}{path}", headers=headers)
            except httpx.HTTPError as e:
                raise ValueError("无法连接 GitHub，请检查网络或代理配置。") from e
            if response.status_code in (403, 429):
                raise ValueError("GitHub API 限流，请配置 GITHUB_TOKEN 后重试。")
            if not response.is_success:
                raise ValueError(f"GitHub 请求失败 ({response.status_code})，请检查仓库地址与网络。")
            return response.json()

        progress("读取仓库信息", 5)
        info = await api("")
        if info.get("private"):
            raise ValueError("MVP 仅支持导入公开仓库。")
        commit = await api(f"/commits/{quote(info['default_branch'], safe='')}")
        sha = commit["sha"]
        tree = await api(f"/git/trees/{sha}?recursive=1")
        candidates = sorted(filter(eligible, tree["tree"]), key=priority)
        selected, file_skipped, byte_skipped = select_files(candidates, limits)
        files, failed = [], []
        semaphore = asyncio.Semaphore(4)
        completed = 0
        indexed_bytes = 0
        actual_byte_skipped = []

        async def read_file(file):
            nonlocal completed, indexed_bytes
            async with semaphore:
                try:
                    path = quote(file["path"], safe="/")
                    raw = cached.get((file["path"], file["sha"]))
                    if raw is None:
                        try:
                            async with client.stream("GET", f"https://raw.githubusercontent.com/{repo}/{sha}/{path}", timeout=8) as response:
                                response.raise_for_status()
                                raw = bytearray()
                                async for part in response.aiter_bytes():
                                    raw.extend(part)
                                    if len(raw) > 80000:
                                        raise ValueError("file too large")
                        except (httpx.HTTPError, ValueError):
                            blob = await api(f"/git/blobs/{file['sha']}")
                            if blob.get("encoding") != "base64" or blob.get("size", 80001) > 80000:
                                raise ValueError("无法读取 Git blob。")
                            raw = base64.b64decode(blob["content"])
                    if len(raw) > 80000:
                        raise ValueError("file too large")
                    content = raw.decode("utf-8")
                    if "\0" in content:
                        raise ValueError("binary file")
                    if indexed_bytes + len(raw) > limits["maxTotalBytes"]:
                        actual_byte_skipped.append(file["path"])
                    else:
                        indexed_bytes += len(raw)
                        files.append({"path": file["path"], "content": content})
                except (ValueError, KeyError, httpx.HTTPError):
                    failed.append(file["path"])
                completed += 1
                progress(f"读取文件 {completed}/{len(selected)}", 15 + round(75 * completed / len(selected)))

        await asyncio.gather(*(read_file(f) for f in selected))
        if not files:
            raise ValueError("未能读取可索引的文本文件，请检查网络或更换仓库。")
        return {"meta": {"id": repo, "name": info["name"], "description": info.get("description") or "", "url": f"https://github.com/{repo}",
                         "sha": sha, "branch": info["default_branch"], "language": info.get("language") or "Mixed",
                         "totalEligible": len(candidates), "partial": bool(tree.get("truncated") or file_skipped or byte_skipped or actual_byte_skipped or failed),
                         "importLimits": limits, "indexedBytes": indexed_bytes,
                         "skippedFileLimit": file_skipped, "skippedByteLimit": byte_skipped + len(actual_byte_skipped),
                         "treeTruncated": bool(tree.get("truncated")),
                         "failedFiles": sorted(failed), "reusedFiles": sum((f["path"], f["sha"]) in cached for f in selected), "demo": False}, "files": sorted(files, key=lambda f: f["path"])}
