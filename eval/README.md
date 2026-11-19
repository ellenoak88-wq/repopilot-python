# LoopForge 源码召回黄金集

本数据集覆盖 `Tencent/LoopForge` 的真实导入快照，固定 Git 提交和 RepoPilot 的文件集合指纹见 `loopforge.snapshot.json`。30 个中文问题由本轮根据仓库源码和文档编写；不含个人聊天记录。标注由 Codex 对照快照核验，尚未经过项目维护者独立复核。

## 题目与证据

`loopforge.jsonl` 每行一题。开发集 20 题（18 道可回答），保留测试集 10 题（9 道可回答）。同一主题的近似题放在同一个划分。题目涵盖安装、编译、配置、Runner、定时调度、遥测、计费和异常处理。3 道现场问题单列，`missing_evidence` 说明需要补充的信息。

每个 `evidence_groups` 条目对应一个必须证明的事实。组内 `any_of` 的任一片段即可证明它；组间全部命中才算完整。文件行号从 1 起、两端均包含；SHA-256 校验该范围的 UTF-8 原文（行间使用 LF，不附加末尾换行）。标注是已审核出处的集合，发现等价依据时可复核并加入。仓库更新后应在新版本重新核对范围。

## 使用

在 RepoPilot 项目根目录用 PowerShell 7 运行：

```powershell
.\.venv\Scripts\python.exe -m eval.run_loopforge
.\.venv\Scripts\python.exe -m eval.run_loopforge --mode bm25 --output bm25-dev.json
.\.venv\Scripts\python.exe -m eval.run_loopforge --mode hybrid --output hybrid-dev.json
```

也可以在已部署的容器内执行 `docker compose exec app python -m eval.run_loopforge` 校验，或加 `--mode hybrid` 评分。

默认从 `data/repopilot.db` 只读验证全部标注。使用其他数据目录时传 `--db`；模型缓存支持 `EMBEDDING_CACHE_DIR`。评分在数据库的一致性临时副本上进行。混合检索需先建好相同快照的真实向量索引；出现降级时命令会提示原因并停止评分。报告含逐题前五条路径和行号、各证据组首次出现的排名、平均证据召回率、完整命中率和 MRR@5。现场问题不计入召回分母，可单独评估 Agent 能否指出证据不足。

日常调参使用默认的 `dev`。锁定方案后再传 `--split test` 复测保留集。当前题目以独立首轮查询为单位，不衡量多轮追问或回答生成质量。

`loopforge.dev-baseline.json` 保存本次开发集对照。每道题按证据组列出首次命中名次，`null` 表示前五条未找到该组。标注复核接受了安装脚本生成的本地数据库连接地址作为 LF-004 的等价证据。保留测试集未参与此次评分。
