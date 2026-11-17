# Docker 部署

在项目目录运行 `docker compose up -d --build --wait`，访问 <http://127.0.0.1:3211>。基础镜像使用 Docker 官方 Python 镜像在 AWS Public ECR 的副本，便于网络受限环境拉取。

## 配置

从 `.env.example` 创建 `.env`，填写模型配置。`REPOPILOT_PORT` 控制宿主机端口，容器内固定 3211。`REPOPILOT_DATA_DIR` 控制宿主机数据库目录，默认 `./data`。Linux bind mount 目录需允许 UID/GID 1000 写入，例如先 `mkdir -p data`，必要时由管理员设置该目录所有者。

宿主机代理需要通过 `DOCKER_HTTPS_PROXY=http://host.docker.internal:端口` 传入；容器中的 `127.0.0.1` 指容器自身。此配置仅用于运行时网络。如果构建时 pip 也需要代理，可使用：

```powershell
docker compose build --build-arg HTTPS_PROXY=http://host.docker.internal:10808
docker compose up -d --no-build --wait
```

按本机实际代理端口调整。不要把 API Key 写入 Dockerfile 或构建参数。`.dockerignore` 仅允许源码、前端、锁定依赖和测试进入镜像构建上下文。

## 运维

```powershell
docker compose ps
docker compose logs --tail 50
docker compose restart
docker compose down
```

`down` 停止并删除容器，保留宿主机 `data`。健康检查使用 `/api/health`。容器根文件系统只读，数据库写入 `/app/data`，临时文件写入 `/tmp`。

不要复制正在使用的 SQLite 主文件来代替完整备份；使用 `python -m repopilot.migrate` 创建一致性快照。Python 版与 Node.js 版使用不同 Compose 项目、镜像、端口和数据库目录。


## 本地向量模型

混合检索使用 CPU，不要求 GPU 或 Embedding API Key。首次建立向量索引需下载模型；完整缓存位于 `/app/data/models`，随现有数据卷保留。SQLite 的 `vector_indexes` 表也随卷持久化。模型权重不会进入 Git 或构建上下文。现有 Docker HTTP/HTTPS 代理配置同样用于模型下载；`HF_HUB_DISABLE_XET=1` 使用常规 HTTP 下载。索引完成后，缓存完整时可离线查询。


学习档案与会话共用 `/app/data/repopilot.db`，现有挂载目录即可持久化，无需新增服务。更新前可用 `python -m repopilot.migrate` 在线备份，首次启动自动补充历史版本字段；参见 [第六轮扩展说明](upgrade-06.md)。


v0.8 首次启动会新增项目分区表，并为旧会话增加默认未置顶的字段。仓库、会话和学习档案沿用原 SQLite 文件；参见 [分区与会话菜单说明](upgrade-07.md)。
