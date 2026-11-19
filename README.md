# RepoPilot Python

开源项目上手助手的独立 Python 版本。输入公开 GitHub 仓库后，检索代码和文档，让 Agent 生成带文件、行号引用的回答。后端为 **FastAPI + HTTPX + SQLite**；前端为原生 JavaScript，无需 Node.js 启动或构建。

## 功能

- 项目 Wiki 随导入、问答和同步在后台维护。打开已有项目也会补建。系统从当前源码取证，以项目默认模型归纳项目用途、配置、实现、测试与指南等有依据的专题，建立带原因的关联与反向链接。问答中涉及的新源码范围会补入相应专题，后续问答也可借助 Wiki 找到相关文件。
- 每个专题每批选取最多五段当前源码。后台先用模型编写，再单独调用一次模型按原文复核。证据相同会复用结果，发生变化的专题会重新编写。此前已收录且仍能逐字核对的其他片段可继续保留。生成与复核会使用模型额度，未配置密钥时提供本地源码导览。模型失败后两分钟再次打开项目可重试。旧手动专题仍在「项目 Wiki」中单独可读。

- 按固定 commit 导入公开 GitHub 仓库，优先 README、入口代码和贡献指南；默认最多 1,000 个文本文件、合计 50 MB，每个不超过 80 KB；数量可配置至 5,000 个，显示部分索引提示。
- BM25 + 本地多语言向量混合检索，RRF 排名融合；支持索引建立、过期检查、持久化和 BM25 回退。Agent 首次检索与补充搜索统一使用所选方式，最多 4 轮只读工具调用。参见 [第五轮扩展说明](docs/upgrade-05.md)。
- 默认混合检索会保留原问题并为常见中文概念补充英文检索词；指代明确的追问可带上同会话上一问。候选片段再按初始排名、文件名、符号定义以及实现、文档或测试意图本地重排。聊天每轮最多引用 5 个检索结果，执行记录会展示扩展词和候选数量。向量不可用时仍可对关键词候选重排，显式 BM25 模式可用于基线对照。
- Responses / Chat Completions 兼容接口，真实 SSE 流式输出、停止生成、引用核验与安全 Markdown 渲染。
- 内置 GPT-6.2 Sol、GPT-6.2 Astra、DeepSeek V4.1 Flash 和旧版 GPT 预设，支持思考强度、模型发现及手动输入。账号可用性由服务商决定。
- 源码阅读器、引用行高亮、文件内搜索、行号跳转、复制。
- 可折叠目录树、路径筛选与独立代码搜索，支持关键词和相关片段两种模式；无需调用模型。参见 [第二轮扩展说明](docs/upgrade-02.md)。
- 多会话创建与搜索；每条会话右侧菜单支持置顶、取消置顶、重命名和删除；重新生成最后一条回答与导出当前会话。
- 会话自动摘要从当前索引下的较早问答中提取相关话题和近期话题，与最近 4 轮问答一同用于追问。摘要显示在回答下方，由本地程序提取，无需额外调用模型。
- 项目分区：创建、重命名、项目拖放归组与折叠显示；左侧项目和分区均有操作菜单，项目支持置顶、移动与删除本地数据；同区项目可共享已验证学习档案，支持项目专属、分区共享和跨仓库通用三种范围。参见 [第七轮扩展说明](docs/upgrade-07.md)。
- 项目上手清单：根据仓库证据生成任务，支持完成进度、持久化备注、源码依据跳转与 Markdown 导出；索引变化时提示过期。参见 [第三轮扩展说明](docs/upgrade-03.md)。
- GitHub 项目发现：Trending 趋势榜与热门 Star 排行，语言/周期/主题筛选、手动刷新、缓存回退、一键导入与已有仓库直接打开。参见 [第四轮扩展说明](docs/upgrade-04.md)。
- 保留会话的仓库增量同步；历史回答绑定索引版本，旧引用保留原始摘录。
- 跨会话、跨仓库学习档案：从回答保存草稿、人工验证、来源复核、筛选与 Markdown 导出；问答可引用相关经验并保存快照。参见 [第六轮扩展说明](docs/upgrade-06.md)。
- 问答需要为所选模型配置 API Key，浏览源码和本地搜索可离线使用。SQLite 持久化，兼容 Node.js 版数据库及旧会话迁移。

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

GPT-6.2 使用现有的 `OPENAI_API_KEY` 与 `LLM_BASE_URL`，通过 Responses API 调用。DeepSeek V4.1 Flash 的模型 ID 是 `deepseek-flash`。设置 `DEEPSEEK_API_KEY` 后，该模型会走 DeepSeek 官方接口，其余模型仍走现有服务商。若现有网关已支持 `deepseek-flash`，可直接使用网关配置。Docker 部署修改密钥后需重新创建容器。

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

模块：`server.py` 提供 HTTP/SSE 接口；`agent.py` 编排 RAG 和工具调用；`stream.py` 解析模型流；`github.py` 导入仓库；`chunking.py` 根据文件结构切片，`retrieval.py` 执行 BM25；`store.py` 管理数据与会话；`models.py` 配置聊天模型；`embeddings.py` 运行本地向量模型；`hybrid.py` 管理向量索引和排名融合；`learning.py` 管理个人学习档案、版本复核与经验召回；`sections.py` 管理项目分区和归属。全部位于 `repopilot/`。

升级依赖后执行 `uv lock`，再用 `uv export --frozen --no-dev --no-emit-project --output-file requirements.txt` 和 `uv export --frozen --no-emit-project --output-file requirements-dev.txt` 同步 pip/Docker 锁定文件。

## 范围

面向个人本地使用，未实现多用户认证。只运行一个 Uvicorn worker，以保持导入和生成的操作锁；不要直接发布到公网。导入的仓库内容仅作为不可信证据，不会执行其中的代码或命令。首页手动重新导入和项目页同步均保留旧会话；旧回答保留版本和摘录，不作为新版源码的当前上下文。「发现项目」入口会复用已导入仓库，保留现有会话和进度。

Go、Python、JS/TS 等代码按类型、函数、方法或测试用例切片。Markdown 按标题层级，工作流与 Compose 按任务或服务，JSON、TOML、Makefile、Dockerfile 按键、配置表、目标或构建阶段切片。超长单元优先在语句或段落边界续分，缺乏可识别边界时采用最多 65 行、重叠 12 行的窗口。检索带上符号名或标题路径，引用仍对应源文件的原始行。切片算法更新会使旧向量索引过期，已有会话引用保留原始行号。

关键词检索使用 BM25；混合检索使用本地 FastEmbed 模型，向量存入 SQLite，无需外部向量数据库。首次建立向量索引需下载模型权重，缺失或过期时明确回退 BM25；代码导入默认最多 1,000 个文件及 50 MB 文本，可通过 `REPO_MAX_FILES`（1–5,000）与 `REPO_MAX_TOTAL_BYTES`（1,000,000–200,000,000）配置；构建前使用模型分词器预检窗口总数，默认上限为 150,000（`REPO_MAX_VECTOR_WINDOWS`）。向量分批写入 SQLite，只有全部成功才切换索引版本；失败可重试。查询按批读取，新旧格式均可检索。原生前端和第三方 Markdown 库来自 Node.js 版；第三方许可证保留在 `public/vendor/`。


## 导入容量

文件数和文本总量共同限制导入，按 README、入口、源码与文档优先级选择文件，继续过滤敏感文件、依赖目录和二进制文件。界面显示实际配置；部分索引提示区分数量限制、字节预算、下载失败与目录树不完整。修改 `.env` 后执行 `docker compose up -d --no-build --force-recreate` 应用配置。

新限制对之后的导入生效，不自动重建已有仓库。可以通过项目页“同步仓库”按新上限扩容，会话与原有清单均保留；变化后的清单和向量索引会提示过期。向量建立仍为显式操作，默认不对新增导入自动运行。
