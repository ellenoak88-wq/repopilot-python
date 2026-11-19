"""Background launcher for the repository's offline retrieval benchmark."""
import asyncio
import json
import os
import sys
from contextlib import suppress
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class Evaluation:
    def __init__(self, store):
        filename = store.db.execute("PRAGMA database_list").fetchone()[2]
        self.db = Path(filename) if filename else None
        folder = Path(os.environ.get("REPOPILOT_EVAL_DIR") or ROOT / "eval")
        self.snapshot = json.loads((folder / "loopforge.snapshot.json").read_text(encoding="utf-8"))
        self.job = None
        self.task = None

    def report(self, split):
        if split not in ("dev", "test"):
            raise ValueError("评测划分必须为 dev 或 test。")
        if not self.db:
            raise ValueError("评测需要持久化项目数据库。")
        return self.db.parent / "evaluations" / f"loopforge-{split}-compare.json"

    def supports(self, repo):
        return repo["id"] == self.snapshot["repo"]

    def matches(self, repo):
        return (repo["sha"], repo["indexVersion"]) == (self.snapshot["repo_sha"], self.snapshot["index_version"])

    def status(self, repo):
        if not self.supports(repo):
            return None
        return dict(matched=self.matches(repo), job=self.job,
                    reports=[split for split in ("dev", "test") if self.db and self.report(split).is_file()])

    def start(self, repo, split):
        output = self.report(split)
        if not self.supports(repo) or not self.matches(repo):
            raise ValueError("当前项目快照与评测数据集不符。")
        if self.task and not self.task.done():
            raise RuntimeError("已有评测正在运行。")
        self.job = dict(split=split, state="running", message="正在准备评测快照…")
        self.task = asyncio.create_task(self._run(self.job, output))
        return self.job

    async def _run(self, job, output):
        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "eval.run_loopforge", "--db", str(self.db), "--mode", "compare",
                "--split", job["split"], "--output", str(output), cwd=ROOT,
                env={**os.environ, "PYTHONIOENCODING":"utf-8"},
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            error = "评测失败，请检查索引状态与数据目录。"
            async for raw in process.stdout:
                line = raw.decode("utf-8", errors="replace").strip()
                if line.startswith(("bm25 · ", "hybrid · ")):
                    job["message"] = line
                elif line.startswith("评测失败："):
                    error = line if job["split"] == "dev" else "保留集评测失败，请检查快照、标注与向量索引。"
            if await process.wait():
                job.update(state="error", message=error)
            else:
                job.update(state="done", message="评测完成，报告已保存。")
        except OSError:
            job.update(state="error", message="无法启动评测进程或写入报告。")
        finally:
            if process and process.returncode is None:
                with suppress(ProcessLookupError):
                    process.kill()
                await process.wait()

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
