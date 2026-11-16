# RepoPilot Python

开源项目上手助手的独立 Python 版本。输入公开 GitHub 仓库后，检索代码和文档，让 Agent 生成带文件、行号引用的回答。后端为 **FastAPI + HTTPX + SQLite**；前端为原生 JavaScript，无需 Node.js 启动或构建。

## 功能

- 按固定 commit 导入公开 GitHub 仓库，优先 README、入口代码和贡献指南；最多 100 个文本文件，每个不超过 80 KB，显示部分索引提示。
- BM25 检索与中文查询扩展；Agent 可继续搜索代码、读取文件、查看目录，最多 4 轮只读工具调用。
- Responses / Chat Completions 兼容接口，真实 SSE 流式输出、停止生成、引用核验与安全 Markdown 渲染。
- GPT-5.6、6、6.1 的 sol / astra / luna / terra 预设、思考强度、模型发现及手动输入。预设不保证账号或服务商支持，以实际接口返回为准。
- 源码阅读器、引用行高亮、文件内搜索、行号跳转、复制。
- 可折叠目录树、路径筛选与独立代码搜索，支持关键词和相关片段两种模式；无需调用模型。参见 [第二轮扩展说明](docs/upgrade-02.md)。
- 多会话创建、搜索、重命名、删除；重新生成最后一条回答；导出当前会话。
- 无 API Key 时返回真实检索摘录；SQLite 持久化，兼容 Node.js 版数据库及旧会话迁移。

## 本地运行

需要 Python 3.12+。推荐使用 uv：

```powershell
Copy-Item .env.example .env
# 按需在 .env 配置 OPENAI_API_KEY、LLM_BASE_URL、LLM_MODEL
uv sync --frozen
uv run python -m repopilot
```

Windows 也可执行 `pwsh -File start.ps1`。没有 uv 时：

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install --require-hashes -r requirements.txt
.venv/Scripts/python.exe -m repopilot
```

访问 <http://127.0.0.1:3211>。默认仅监听本机。环境变量优先于 `.env`，API Key 只在服务端使用。

## Docker

```powershell
docker compose up -d --build --wait
docker compose ps
docker compose logs --tail 50
```

容器名 `repopilot-python-app-1`，镜像 `repopilot-python:local`，默认端口 3211，与 Node.js 版 3210 独立。数据持久化到 `./data`。镜像构建时运行全部 Python 测试，生产镜像以非 root 用户启动，使用只读根文件系统。参见 [Docker 部署说明](docs/docker.md)。

## 复制 Node.js 版数据

Python 版使用独立数据库；不要让两个服务同时指向同一个数据库。目标文件必须不存在：

```powershell
uv run python -m repopilot.migrate --source E:/repopilot/data/repopilot.db --destination data/repopilot.db
```

SQLite 在线备份包含 WAL 内已提交的数据，不需要停止原服务。复制后两个版本的数据独立变化。首次启动自动迁移旧版会话，保留历史回答。`.env`、数据库和本地索引不提交到 GitHub。

## 开发与测试

```powershell
uv sync --frozen
uv run pytest -q
```

模块：`server.py` 提供 HTTP/SSE 接口；`agent.py` 编排 RAG 和工具调用；`stream.py` 解析模型流；`github.py` 导入仓库；`retrieval.py` 切片与 BM25；`store.py` 管理数据与会话；`models.py` 配置模型。全部位于 `repopilot/`。

升级依赖后执行 `uv lock`，再用 `uv export --frozen --no-dev --no-emit-project --output-file requirements.txt` 和 `uv export --frozen --no-emit-project --output-file requirements-dev.txt` 同步 pip/Docker 锁定文件。

## 范围

面向个人本地使用，未实现多用户认证。只运行一个 Uvicorn worker，以保持导入和生成的操作锁；不要直接发布到公网。导入的仓库内容仅作为不可信证据，不会执行其中的代码或命令。重新导入仓库会替换索引并清除该仓库的旧会话，避免混用不同提交的证据。

检索仍为 BM25，不含向量数据库或嵌入模型；代码索引最多 100 个文件。原生前端和第三方 Markdown 库来自 Node.js 版；第三方许可证保留在 `public/vendor/`。
