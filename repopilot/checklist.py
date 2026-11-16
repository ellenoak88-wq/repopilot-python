"""Evidence-based onboarding checklists with manual, durable progress."""
import hashlib
import json
import re
import uuid
from pathlib import PurePosixPath

from .store import now

STAGES = [
    ("understand", "了解项目"), ("setup", "安装与启动"), ("code", "阅读代码"),
    ("test", "验证与测试"), ("contribute", "第一次贡献"),
]


class ChecklistConflict(ValueError):
    pass


def index_signature(repo, files):
    digest = hashlib.sha256(repo["sha"].encode())
    for file in sorted(files, key=lambda f: f["path"]):
        digest.update(json.dumps([file["path"], file["content"]], ensure_ascii=False).encode())
    return digest.hexdigest()


def build_tasks(files):
    files = sorted(files, key=lambda f: (f["path"].count("/"), f["path"].lower()))
    docs = [f for f in files if re.search(r"\.(md|mdx|rst|txt)$|(^|/)(README|CONTRIBUTING)$", f["path"], re.I)]
    readme = next((f for f in docs if re.match(r"readme(?:\.|$)", PurePosixPath(f["path"]).name, re.I)), None)
    tasks = []

    def add(stage, title, description, file, start=1):
        lines = file["content"].split("\n")
        end = min(start + 19, len(lines))
        # Keep a section boundary when possible, without asserting the text is executable.
        heading = re.match(r"^(#{1,6})\s", lines[start - 1])
        if heading:
            for i in range(start, end):
                other = re.match(r"^(#{1,6})\s", lines[i])
                if other and len(other[1]) <= len(heading[1]):
                    end = i
                    break
        text = "\n".join(lines[start - 1:end])
        key = hashlib.sha256(json.dumps([stage, file["path"], start, end, text]).encode()).hexdigest()[:24]
        tasks.append({"id": key, "stage": stage, "title": title, "description": description,
                      "source": {"path": file["path"], "start": start, "end": end, "text": text[:2400], "truncated": len(text) > 2400},
                      "completed": False, "note": ""})

    def section(pattern):
        ordered = ([readme] if readme else []) + [f for f in docs if f is not readme]
        for file in ordered:
            lines = file["content"].split("\n")
            hits = [(i + 1, line) for i, line in enumerate(lines) if re.search(pattern, line, re.I)]
            if hits:
                # Headings are better anchors than incidental mentions in prose.
                start = next((n for n, line in hits if re.match(r"^#{1,6}\s", line)), hits[0][0])
                return file, start
        return None

    if readme:
        add("understand", "阅读项目说明", "根据原文记录项目用途、使用场景和已知限制。", readme)
    elif docs:
        add("understand", "阅读现有项目文档", "当前索引未找到 README，可先从这份文档了解项目。", docs[0])

    setup = section(r"\b(installation|install|setup|quick\s?start|getting started|usage)\b|安装|启动|环境要求")
    if setup:
        add("setup", "核对安装与启动步骤", "先阅读文档中的环境要求和运行说明，再按自己的环境操作；清单不会执行命令。", *setup)
    else:
        manifest = next((f for f in files if PurePosixPath(f["path"]).name in ("package.json", "pyproject.toml", "requirements.txt", "Cargo.toml", "go.mod", "Dockerfile")), None)
        if manifest:
            add("setup", "核对依赖与项目配置", "未找到明确的安装章节，可先核对这份配置；具体启动方式仍需确认。", manifest)

    candidates = [f for f in files if re.search(r"(^|/)(__main__|main|app|server|index)\.(py|js|mjs|ts|tsx|go|rs|java)$", f["path"], re.I)
                  and not re.search(r"(^|/)(test|tests|docs|examples?)/", f["path"], re.I)]
    for file in candidates[:2]:
        add("code", f"阅读入口候选：{file['path']}", "这是根据文件名找到的入口阅读候选，请结合导入和调用关系确认实际入口。", file)
    if not candidates:
        source = next((f for f in files if re.search(r"\.(py|js|ts|tsx|jsx|go|rs|java|rb|php)$", f["path"], re.I)), None)
        if source:
            add("code", f"从源码开始阅读：{source['path']}", "记录主要函数和依赖关系；此文件仅作为阅读起点，不代表已确认的应用入口。", source)

    testing = section(r"\b(test|tests|testing|pytest|unittest)\b|测试|验证")
    if testing:
        add("test", "核对测试与验证方式", "查看原文中的测试说明，手动验证后再勾选完成。", *testing)
    else:
        sample = next((f for f in files if re.search(r"(^|/)(tests?/|test_[^/]+|[^/]+[._](test|spec)\.)", f["path"], re.I)), None)
        if sample:
            add("test", f"阅读测试样例：{sample['path']}", "了解断言与预期行为；当前索引中尚未确认测试运行命令。", sample)

    contribution = next((f for f in docs if re.match(r"contributing(?:\.|$)", PurePosixPath(f["path"]).name, re.I)), None)
    if contribution:
        add("contribute", "阅读贡献指南并选择首个任务", "根据仓库已有要求，记录适合自己的修改目标和验证方式。", contribution)
    else:
        found = section(r"\b(contributing|contribution|good first|pull request)\b|贡献|首个任务")
        if found:
            add("contribute", "核对贡献要求", "阅读已有贡献说明，再选择一个范围明确的任务。", *found)

    present = {task["stage"] for task in tasks}
    gaps = [{"stage": key, "title": title, "message": "当前索引未找到足够依据，可补充文档或通过代码搜索继续确认。"}
            for key, title in STAGES if key not in present]
    return tasks, gaps


class ChecklistService:
    def __init__(self, store):
        self.store = store
        self.store.db.execute("CREATE TABLE IF NOT EXISTS checklists (repo TEXT PRIMARY KEY, payload TEXT NOT NULL)")
        self.store.db.commit()

    def _read(self, repo):
        row = self.store.db.execute("SELECT payload FROM checklists WHERE repo=?", (repo,)).fetchone()
        return json.loads(row[0]) if row else None

    def view(self, repo):
        plan = self._read(repo["id"])
        signature = index_signature(repo, self.store.files(repo["id"]))
        return {"plan": plan, "stale": bool(plan and plan["indexSignature"] != signature),
                "repo": repo["id"], "currentSha": repo["sha"], "stages": [{"id": key, "title": title} for key, title in STAGES]}

    def _expect(self, plan, plan_id, revision):
        if plan_id is not None and not isinstance(plan_id, str):
            raise ValueError("清单 ID 格式无效。")
        if type(revision) is not int or revision < 0:
            raise ValueError("清单版本格式无效。")
        if (plan["id"] if plan else None) != plan_id or (plan["revision"] if plan else 0) != revision:
            raise ChecklistConflict("清单已在其他页面更新，请刷新清单后重试。")

    def generate(self, repo, plan_id, revision):
        files = self.store.files(repo["id"])
        signature = index_signature(repo, files)
        tasks, gaps = build_tasks(files)
        with self.store.db:
            previous = self._read(repo["id"])
            self._expect(previous, plan_id, revision)
            if previous and previous["indexSignature"] == signature:
                saved = {task["id"]: task for task in previous["tasks"]}
                for task in tasks:
                    if task["id"] in saved:
                        task.update(completed=saved[task["id"]]["completed"], note=saved[task["id"]]["note"])
            plan = {"id": str(uuid.uuid4()), "repo": repo["id"], "sha": repo["sha"], "indexSignature": signature,
                    "revision": 1, "createdAt": now(), "updatedAt": now(), "tasks": tasks, "gaps": gaps,
                    "partial": repo.get("partial", False), "fileCount": len(files), "method": "evidence"}
            self.store.db.execute("INSERT OR REPLACE INTO checklists VALUES (?,?)", (repo["id"], json.dumps(plan, ensure_ascii=False)))
        return self.view(repo)

    def update(self, repo, body):
        task_id = body.get("taskId")
        if not isinstance(task_id, str):
            raise ValueError("任务 ID 格式无效。")
        if "completed" not in body and "note" not in body:
            raise ValueError("请提供完成状态或备注。")
        if "completed" in body and type(body["completed"]) is not bool:
            raise ValueError("完成状态必须为布尔值。")
        if "note" in body and (not isinstance(body["note"], str) or len(body["note"]) > 1000):
            raise ValueError("备注最多 1000 字。")
        signature = index_signature(repo, self.store.files(repo["id"]))
        with self.store.db:
            plan = self._read(repo["id"])
            self._expect(plan, body.get("planId"), body.get("revision"))
            if not plan:
                raise ValueError("请先生成上手清单。")
            if plan["indexSignature"] != signature:
                raise ChecklistConflict("仓库索引已变化，请重新生成清单后记录进度。")
            task = next((t for t in plan["tasks"] if t["id"] == task_id), None)
            if not task:
                raise ValueError("任务不存在或不属于当前清单。")
            for key in ("completed", "note"):
                if key in body:
                    task[key] = body[key]
            plan["revision"] += 1
            plan["updatedAt"] = now()
            self.store.db.execute("UPDATE checklists SET payload=? WHERE repo=?", (json.dumps(plan, ensure_ascii=False), repo["id"]))
        return self.view(repo)
