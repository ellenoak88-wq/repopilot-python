# 第四轮扩展：GitHub 项目发现

点击侧栏「发现项目」，浏览推荐、选择筛选条件，再点击「导入并探索」。无需模型调用，也无需配置模型 API Key。

## 推荐来源与排序

- **GitHub Trending**：读取 GitHub 官方 Trending 页面，保留原榜单顺序；支持全部语言或常用语言，以及每日、每周、每月周期。卡片显示榜单提供的周期新增 Star 数，缺失数据标为未知。
- **热门项目 · Star 排行**：使用 GitHub 仓库搜索 API，查询公开、非归档、非 Fork、至少 1,000 Star、近 180 天有推送的仓库，按 Star 总数降序取前 30 个。支持语言，以及人工智能、RAG、大语言模型、开发工具、Web 框架主题。主题取决于仓库维护者填写的 GitHub topic，不是模型分类。

每张卡片显示仓库名称、简介、语言、Star、Fork 和 GitHub 链接。页面显示数据时间与来源链接；GitHub 搜索返回不完整标志时会提示。热门搜索不会作为 Trending 的静默替代。

参考：[GitHub Trending](https://github.com/trending)、[GitHub Search repositories API](https://docs.github.com/en/rest/search/search?apiVersion=2022-11-28#search-repositories)。Trending 使用网页结构解析，GitHub 改版可能导致读取失败。

## 刷新与网络异常

首次进入或切换筛选时请求数据。成功结果按筛选条件存入 SQLite `discovery_cache` 表，缓存有效期 1 小时；点击「刷新推荐」可提前更新，同一筛选条件至少间隔 60 秒。并发请求合并，避免重复抓取。

网络、限流或解析异常时，有缓存则展示历史缓存和明确提示，无缓存则显示错误并允许重试。应用没有后台轮询或定时推送。页面停留期间不会自动刷新，需要点击刷新按钮。缓存随 `./data` 数据卷持久化，Docker 重建后仍然保留。

可在服务端配置 `GITHUB_TOKEN` 提高热门搜索 API 额度；该令牌只发往 `api.github.com`，不发给 Trending 网页或浏览器。沿用现有 HTTPX 代理配置，不增加依赖或必填环境变量。

## 导入与已有数据

「导入并探索」复用现有异步导入流程，读取最多 100 个文本文件，完成后进入仓库探索。推荐本身不会自动下载或执行仓库代码。

已有仓库的按钮显示「已导入 · 打开」。服务端也会按仓库名忽略大小写检查是否存在，直接返回已有索引，保留会话、备注和清单进度，避免页面状态过旧导致重复导入。

首页手动输入地址的重新导入仍遵循原逻辑：替换索引并清除旧会话。推荐入口不会触发这种重建。

## API

`GET /api/discover` 参数：

| 参数 | 值 / 默认值 |
| --- | --- |
| `source` | `trending`（默认）、`popular` |
| `language` | 空字符串（全部）、Python、JavaScript、TypeScript、Go、Rust、Java、C++、C#、Ruby |
| `since` | `daily`（默认）、`weekly`、`monthly`；仅 Trending 使用 |
| `topic` | 空字符串（全部）、artificial-intelligence、rag、llm、developer-tools、web-framework；仅热门项目使用 |
| `refresh` | `0`（默认）或 `1` |

返回 `items`、`sourceUrl`、`fetchedAt`、`cached`、`stale`、`warning` 和 `partial`；每项包含 `importedId`，该值在请求时重新判断。非法参数返回 400；无可用缓存且上游失败时返回 503。

`POST /api/import` 增加可选布尔值 `reuseExisting`。推荐入口传 `true`；已有仓库直接返回 `state: done`（200），新仓库返回导入任务（202）。默认值为 `false`，兼容原有行为。

## 验证

自动测试共 55 项通过，包括新增的榜单解析、数值解析、不可信链接、请求头隔离、公开仓库过滤、缓存持久化、刷新间隔、并发合并、失败回退、参数校验、新仓库导入及已有数据保留测试。Docker 构建阶段运行相同测试。

浏览器验收覆盖真实 Trending、Python 语言筛选、RAG 热门项目、手动刷新、已有仓库打开、引用符号与 HTML 文本安全显示、异常/空列表/历史缓存提示、快速切换筛选的响应顺序，以及 390px 手机布局。浏览器异常场景使用拦截响应模拟，不会写入实际推荐缓存。
