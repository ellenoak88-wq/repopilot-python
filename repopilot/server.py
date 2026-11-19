"""FastAPI routes matching the original frontend's HTTP/SSE contract."""
import asyncio
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from .agent import ask_agent
from .checklist import ChecklistConflict, ChecklistService
from .discovery import DiscoveryService, DiscoveryUnavailable
from .embeddings import make_embedder
from .hybrid import HybridRetrieval
from .learning import LearningService, LearningConflict, memory_refs
from .conversation import HISTORY_LIMIT, RECENT_TURNS, summarize
from .sections import SectionConflict
from .explorer import search_files, validate_search
from .github import import_github, import_limits, parse_repo
from .models import MODEL_OPTIONS, discover_models, model_config, select_model
from .store import Store, now
from .wiki import Wiki, WikiConflict
from .auto_wiki import AutoWiki
from .wiki_author import write_articles

ROOT = Path(__file__).resolve().parent.parent
DEMO = json.loads((Path(__file__).parent / "demo.json").read_text(encoding="utf-8"))
ASSETS = {"/": "index.html", **{f"/{name}": name for name in ("app.js", "style.css", "render.js", "sse.js", "reader.js", "model-order.js", "explorer.js", "checklist.js", "discovery.js", "retrieval.js", "learning.js", "wiki.js", "sections.js", "session-list.js", "sidebar.js", "vendor/marked.js", "vendor/purify.js")}}
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer", "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
}


class ApiError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


class LocalOnlyMiddleware:
    """Pure ASGI middleware: no response buffering or interference with SSE."""
    def __init__(self, app, port):
        self.app, self.port = app, port

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope["headers"])
        ports = {str(self.port), os.getenv("PUBLIC_PORT", str(self.port))}
        allowed = {f"{host}:{port}" for host in ("localhost", "127.0.0.1") for port in ports}
        host = headers.get(b"host", b"").decode("latin-1")
        origin = headers.get(b"origin", b"").decode("latin-1")

        async def secure_send(message):
            if message["type"] == "http.response.start":
                message["headers"] = list(message.get("headers", [])) + [(k.lower().encode(), v.encode()) for k, v in SECURITY_HEADERS.items()]
            await send(message)

        if host not in allowed or (origin and origin not in {f"http://{h}" for h in allowed}):
            return await JSONResponse({"error": "仅允许本机同源访问。"}, status_code=403)(scope, receive, secure_send)
        await self.app(scope, receive, secure_send)


async def disconnected(receive):
    while (await receive())["type"] != "http.disconnect":
        pass


class CancellableStream(StreamingResponse):
    """Cancel provider even when it is waiting for its first token (ASGI 2.3/2.4)."""
    def __init__(self, content, release):
        super().__init__(content, media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})
        self.release = release

    async def __call__(self, scope, receive, send):
        sender = asyncio.create_task(self.stream_response(send))
        watcher = asyncio.create_task(disconnected(receive))
        try:
            done, _ = await asyncio.wait({sender, watcher}, return_when=asyncio.FIRST_COMPLETED)
            if sender in done:
                await sender
        except OSError:
            pass  # Client closed its socket.
        finally:
            sender.cancel()
            watcher.cancel()
            await asyncio.gather(sender, watcher, return_exceptions=True)
            await self.body_iterator.aclose()
            self.release()


def create_app(*, store=None, importer=import_github, agent=ask_agent, port=None, discovery_transport=None, embedder=None,
               wiki_author=write_articles):
    port = port or int(os.getenv("PORT", "3211"))
    owned_store = store is None
    store = store or Store(Path(os.getenv("DATA_DIR", str(ROOT / "data"))) / "repopilot.db")
    checklists = ChecklistService(store)
    learning = LearningService(store)
    wiki = Wiki(store)
    auto_wiki = AutoWiki(store, author=wiki_author)
    sections = learning.sections
    discovery = DiscoveryService(store, transport=discovery_transport)
    hybrid = HybridRetrieval(store, embedder or make_embedder(os.getenv("DATA_DIR", str(ROOT / "data"))))
    jobs, tasks = {}, set()
    state = {"busy": False}

    @asynccontextmanager
    async def lifespan(app):
        for row in store.db.execute("SELECT repo FROM wiki_editions WHERE dirty=1").fetchall():
            repo = store.get(row["repo"])
            if repo:
                auto_wiki.request(repo)
        yield
        await auto_wiki.close()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if owned_store:
            store.close()

    app = FastAPI(title="RepoPilot Python", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.add_middleware(LocalOnlyMiddleware, port=port)
    app.state.store = store

    def release():
        state["busy"] = False

    def idle():
        if state["busy"]:
            raise ApiError(409, "请等待当前任务完成。")

    async def body_json(request):
        if not request.headers.get("content-type", "").startswith("application/json"):
            raise ApiError(415, "请发送 JSON。")
        raw = bytearray()
        async for part in request.stream():
            raw.extend(part)
            if len(raw) > (64000 if request.url.path.startswith("/api/wiki/") else
                           40000 if request.url.path.startswith("/api/learning/") else 20000):
                raise ApiError(413, "请求过大。")
        try:
            body = json.loads(raw or b"{}")
        except (ValueError, UnicodeError) as e:
            raise ApiError(400, "JSON 格式错误。") from e
        if not isinstance(body, dict):
            raise ApiError(400, "JSON 必须为对象。")
        return body

    def get_repo(value):
        repo = store.get(value) if isinstance(value, str) else None
        if not repo:
            raise ApiError(404, "请先导入或选择仓库。")
        return sections.annotate(repo)

    async def import_job(value, job):
        try:
            existing = next((r for r in store.list() if r["id"].lower() == value.lower()), None)
            options = {"existing": store.files(existing["id"])} if importer is import_github and existing else {}
            result = await importer(value, lambda message, progress: job.update(message=message, progress=progress), **options)
            if existing:
                result = {**result, "meta": {**result["meta"], "id": existing["id"]}}
            repo = store.save_repository(result["meta"], result["files"])
            auto_wiki.request(repo)
            job.update(state="done", progress=100, message="索引完成", repo=repo)
        except Exception as e:
            job.update(state="error", error=str(e) if isinstance(e, ValueError) else "仓库导入失败，请重试。")
        finally:
            release()

    async def vector_job(repo, job, force):
        try:
            result = await hybrid.build(repo, lambda message, progress: job.update(message=message, progress=progress), force=force)
            job.update(state="done", progress=100, message="向量索引已就绪", index=result)
        except Exception as e:
            job.update(state="error", error=str(e) if isinstance(e, ValueError) else "向量索引建立失败，请检查模型下载网络、磁盘空间或服务端日志。")
        finally:
            release()

    async def chat(request, body, streaming):
        idle()
        repo = get_repo(body.get("repo"))
        session = store.default_session(repo["id"]) if "sessionId" not in body else (
            store.get_session(repo["id"], body["sessionId"]) if isinstance(body["sessionId"], str) else None)
        if not session:
            raise ApiError(404, "会话不存在或不属于当前仓库。")
        replace_id = body.get("regenerateId")
        if "regenerateId" in body:
            previous = store.history(repo["id"], session["id"])
            latest = previous[-1] if previous else None
            if type(replace_id) is not int or not latest or latest["id"] != replace_id:
                raise ApiError(400, "只能重新生成当前会话最后一条回答。")
            if latest.get("indexVersion") != repo["indexVersion"]:
                raise ApiError(409, "这条回答属于旧索引，请重新提问以使用当前源码。")
            question = latest["question"]
        else:
            question = body.get("question")
        if not isinstance(question, str) or not question.strip() or len(question) > 2000:
            raise ApiError(400, "请输入 1–2000 字的问题。")
        if body.get("mode", "agent") != "agent":
            raise ApiError(400, "当前仅支持模型问答，请更新客户端后重试。")
        retrieval_mode = body.get("retrieval", "hybrid")
        if retrieval_mode not in ("hybrid", "bm25"):
            raise ApiError(400, "检索方式无效。")
        if "useLearning" in body and type(body["useLearning"]) is not bool:
            raise ApiError(400, "学习档案开关必须为布尔值。")
        use_learning = body.get("useLearning", True)
        context = store.context_history(repo["id"], session["id"], repo["indexVersion"],
                                        HISTORY_LIMIT, before=replace_id)
        context = learning.filter_history(repo, context, use_learning)
        summarized, summary = summarize(context[:-RECENT_TURNS], question)
        recent = context[-RECENT_TURNS:]
        inherited = set().union(*(memory_refs(turn) for turn in [*summarized, *recent]))
        memories = learning.recall(repo, question) if use_learning else []
        config = select_model(body.get("model"), body.get("effort"))
        indexed_chunks = store.chunks(repo["id"])

        async def retrieve(query, limit):
            previous = recent[-1]["question"] if recent and query == question else ""
            return await hybrid.retrieve(repo, indexed_chunks, query, limit, mode=retrieval_mode,
                                         previous_question=previous)
        kwargs = dict(repo=repo, files=store.files(repo["id"]), chunks=indexed_chunks, question=question, retriever=retrieve,
                      history=recent, summary=summary, memories=memories, config=config)
        if hints := auto_wiki.hints(repo, question):
            kwargs["wiki_hints"] = hints
        state["busy"] = True

        def save(result):
            payload = {"question": question, **result, "memoriesUsed": memories, "createdAt": now()}
            if summary:
                payload["sessionSummary"] = summary
            if inherited and result.get("mode") == "agent":
                payload["contextMemoryRefs"] = [
                    {"id": record_id, "revision": revision} for record_id, revision in sorted(inherited)
                ]
            saved = store.add_chat(repo["id"], payload, session["id"], replace_id)
            auto_wiki.request(repo, saved.get("sources") or [])
            return saved

        if not streaming:
            task = asyncio.create_task(agent(**kwargs))
            watcher = asyncio.create_task(disconnected(request.receive))
            try:
                done, _ = await asyncio.wait({task, watcher}, return_when=asyncio.FIRST_COMPLETED)
                if watcher in done:
                    return Response(status_code=499)
                return JSONResponse(save(await task))
            finally:
                task.cancel()
                watcher.cancel()
                await asyncio.gather(task, watcher, return_exceptions=True)
                release()

        async def events():
            queue = asyncio.Queue(maxsize=8192)

            def emit(event):
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull as e:
                    raise ValueError("客户端接收过慢，请重试。") from e

            async def produce():
                try:
                    result = await agent(**kwargs, on_event=emit)
                    await queue.put({"type": "complete", "result": result})
                except Exception as e:
                    await queue.put({"type": "error", "error": str(e) if isinstance(e, ValueError) else "模型响应处理失败，请重试。"})

            task = asyncio.create_task(produce())
            try:
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), 15)
                    except TimeoutError:
                        yield ": ping\n\n"
                        continue
                    if event["type"] == "complete":
                        await asyncio.sleep(0)  # Let pending disconnect cancellation run before committing.
                        event = {"type": "done", "result": save(event["result"])}
                    yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
                    if event["type"] in ("done", "error"):
                        break
            finally:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

        return CancellableStream(events(), release)

    @app.api_route("/{path:path}", methods=["GET", "POST"])
    async def route(request: Request, path: str):
        path = "/" + path
        q = request.query_params
        try:
            if request.method == "GET":
                if path in ASSETS:
                    name = ASSETS[path]
                    media = "text/javascript" if name.endswith(".js") else "text/css" if name.endswith(".css") else "text/html"
                    return FileResponse(ROOT / "public" / name, media_type=media)
                if path == "/api/health":
                    return {"status": "ok"}
                if path == "/api/status":
                    cfg = model_config()
                    options = MODEL_OPTIONS[:]
                    if not any(m["id"] == cfg["model"] for m in options):
                        options.append({"id": cfg["model"], "label": cfg["model"] + " · 服务端配置", "efforts": []})
                    return {"modelConfigured": bool(cfg["key"] or cfg["deepseek_key"]), "model": cfg["model"], "models": options, "backend": "python", "importLimits": import_limits()}
                if path == "/api/models":
                    return {"models": await discover_models()}
                if path == "/api/discover":
                    if q.get("refresh", "0") not in ("0", "1"):
                        raise ApiError(400, "刷新参数无效。")
                    return await discovery.get(source=q.get("source", "trending"), language=q.get("language", ""),
                                               since=q.get("since", "daily"), topic=q.get("topic", ""), refresh=q.get("refresh") == "1")
                if path == "/api/wiki/auto":
                    repo = get_repo(q.get("repo"))
                    auto_wiki.request(repo)
                    return auto_wiki.view(repo)
                if path == "/api/wiki":
                    return wiki.pages(get_repo(q.get("repo"))["id"])
                if path == "/api/wiki/detail":
                    return wiki.detail(get_repo(q.get("repo")), q.get("id"))
                if path == "/api/learning/export":
                    return Response(learning.export(), media_type="text/markdown; charset=utf-8",
                                    headers={"Content-Disposition": 'attachment; filename="learning-history.md"'})
                if path == "/api/learning/detail":
                    return learning.get(q.get("id"))
                if path == "/api/learning":
                    try:
                        offset, limit = int(q.get("offset", "0")), int(q.get("limit", "50"))
                    except ValueError as exc:
                        raise ApiError(400, "分页参数无效。") from exc
                    return learning.list(query=q.get("q", ""), repo=q.get("repo", ""), status=q.get("status", ""),
                                         scope=q.get("scope", ""), kind=q.get("kind", ""), section_id=q.get("sectionId", ""), offset=offset, limit=limit)
                if path == "/api/sections":
                    return sections.list()
                if path == "/api/repos":
                    return sections.repositories()
                if path == "/api/retrieval/status":
                    return hybrid.status(get_repo(q.get("repo")))
                if path == "/api/checklist":
                    return checklists.view(get_repo(q.get("repo")))
                if path == "/api/search":
                    repo = get_repo(q.get("repo"))
                    try:
                        limit = int(q.get("limit", "20"))
                    except ValueError as e:
                        raise ApiError(400, "结果数量需为 1–50。") from e
                    mode = q.get("mode", "literal")
                    query, path_filter = validate_search(q.get("q", ""), mode, q.get("path", ""), limit)
                    indexed_files = store.files(repo["id"])
                    retrieved = None
                    if mode == "hybrid":
                        retrieved = await hybrid.retrieve(repo, store.chunks(repo["id"]), query, 150, path_filter=path_filter)
                    result = search_files(indexed_files, query, mode=mode, path_filter=path_filter, limit=limit,
                                          candidates=retrieved["items"] if retrieved else None)
                    if retrieved:
                        result["retrieval"] = {k: retrieved[k] for k in ("requested", "actual", "warning", "rewrite", "candidateCount")}
                        result["candidateLimit"] = 150
                    return {**result, "repo": repo["id"], "sha": repo["sha"], "partial": repo.get("partial", False)}
                if path == "/api/file":
                    repo = get_repo(q.get("repo"))
                    file = store.file(repo["id"], q.get("path", ""))
                    if not file:
                        raise ApiError(404, "该文件不在当前仓库索引中。")
                    return {**file, "sha": repo["sha"], "indexVersion": repo["indexVersion"],
                            "demo": repo.get("demo", False), "lines": len(file["content"].split("\n"))}
                if path.startswith("/api/jobs/"):
                    job = jobs.get(path.rsplit("/", 1)[-1])
                    if not job:
                        raise ApiError(404, "任务不存在。")
                    return job
                if path == "/api/repo":
                    repo = get_repo(q.get("id"))
                    session = store.get_session(repo["id"], q["session"]) if "session" in q else store.default_session(repo["id"])
                    if not session:
                        raise ApiError(404, "会话不存在或不属于当前仓库。")
                    auto_wiki.request(repo)
                    return {"repo": repo, "session": session, "sessions": store.sessions(repo["id"]),
                            "files": [{"path": f["path"], "lines": len(f["content"].split("\n"))} for f in store.files(repo["id"])],
                            "history": store.history(repo["id"], session["id"])}
                if path == "/api/sessions":
                    repo = get_repo(q.get("repo"))
                    return store.sessions(repo["id"], q.get("q", "")[:200])
            else:
                body = await body_json(request)
                if path == "/api/wiki/draft":
                    return wiki.draft(get_repo(body.get("repo")), body.get("chatId"))
                if path == "/api/wiki/delete":
                    return wiki.delete(get_repo(body.get("repo"))["id"], body.get("pageId"), body.get("revision"))
                if path in ("/api/wiki/create", "/api/wiki/merge"):
                    idle()
                    merge = path.endswith("/merge")
                    saved = wiki.save(get_repo(body.get("repo")), body, merge=merge)
                    return JSONResponse(saved, status_code=200 if merge else 201)
                if path in ("/api/repos/pin", "/api/repos/delete"):
                    idle()
                    repo = get_repo(body.get("repo"))
                    if path.endswith("/pin"):
                        return sections.pin_repository(repo, body.get("pinned"))
                    result = sections.delete_repository(repo, body.get("indexVersion"))
                    auto_wiki.forget(repo["id"])
                    return result
                if path in ("/api/sections/create", "/api/sections/rename", "/api/sections/delete", "/api/sections/assign"):
                    idle()
                    if path.endswith("/create"):
                        return JSONResponse(sections.create(body.get("name")), status_code=201)
                    if path.endswith("/rename"):
                        return sections.rename(body)
                    if path.endswith("/delete"):
                        return sections.delete(body)
                    return sections.assign(body)
                if path in ("/api/learning/create", "/api/learning/update", "/api/learning/delete", "/api/learning/from-chat"):
                    idle()
                    if path.endswith("/from-chat"):
                        return learning.from_chat(get_repo(body.get("repo")), body.get("chatId"))
                    if path.endswith("/create"):
                        return JSONResponse(learning.create(body), status_code=201)
                    if path.endswith("/delete"):
                        return learning.delete(body)
                    return learning.update(body)
                if path == "/api/retrieval/build":
                    idle()
                    repo = get_repo(body.get("repo"))
                    if "force" in body and type(body["force"]) is not bool:
                        raise ApiError(400, "重建参数必须为布尔值。")
                    if len(jobs) >= 30:
                        jobs.pop(next(iter(jobs)))
                    job = {"id": str(uuid.uuid4()), "state": "running", "progress": 0, "message": "准备建立向量索引"}
                    jobs[job["id"]] = job
                    state["busy"] = True
                    task = asyncio.create_task(vector_job(repo, job, body.get("force", False)))
                    tasks.add(task)
                    task.add_done_callback(tasks.discard)
                    return JSONResponse(job, status_code=202)
                if path in ("/api/checklist/generate", "/api/checklist/update"):
                    repo = get_repo(body.get("repo"))
                    if path.endswith("/generate"):
                        return checklists.generate(repo, body.get("planId"), body.get("revision"))
                    return checklists.update(repo, body)
                if path in ("/api/sessions/create", "/api/sessions/rename", "/api/sessions/delete", "/api/sessions/pin"):
                    idle()
                    repo = get_repo(body.get("repo"))
                    action = path.rsplit("/", 1)[-1]
                    sid, title = body.get("sessionId"), body.get("title")
                    if action != "create" and (not isinstance(sid, str) or not store.get_session(repo["id"], sid)):
                        raise ApiError(404, "会话不存在。")
                    if action in ("create", "rename") and (not isinstance(title, str) or not 1 <= len(title.strip()) <= 80):
                        raise ApiError(400, "会话名称需为 1–80 字。")
                    if action == "create":
                        return JSONResponse(store.create_session(repo["id"], title.strip()), status_code=201)
                    if action == "pin":
                        store.pin_session(repo["id"], sid, body.get("pinned"))
                    elif action == "rename":
                        store.rename_session(repo["id"], sid, title.strip())
                    else:
                        store.delete_session(repo["id"], sid)
                    return {"sessions": store.sessions(repo["id"])}
                if path == "/api/demo":
                    idle()
                    repo = store.get(DEMO["meta"]["id"]) or store.save_repository(DEMO["meta"], DEMO["files"])
                    auto_wiki.request(repo)
                    return repo
                if path in ("/api/import", "/api/repo/sync"):
                    idle()
                    if path.endswith("/sync"):
                        current = get_repo(body.get("repo"))
                        if current.get("demo"):
                            raise ApiError(400, "内置示例无需同步 GitHub。")
                        value = parse_repo(current["id"])
                    else:
                        value = parse_repo(body.get("url"))
                    if "reuseExisting" in body and type(body["reuseExisting"]) is not bool:
                        raise ApiError(400, "复用参数必须为布尔值。")
                    if body.get("reuseExisting"):
                        existing = next((r for r in store.list() if r["id"].lower() == value.lower()), None)
                        if existing:
                            return {"state": "done", "progress": 100, "message": "仓库已导入", "repo": existing}
                    if len(jobs) >= 30:
                        jobs.pop(next(iter(jobs)))
                    job = {"id": str(uuid.uuid4()), "state": "running", "progress": 0, "message": "准备导入"}
                    jobs[job["id"]] = job
                    state["busy"] = True
                    task = asyncio.create_task(import_job(value, job))
                    tasks.add(task)
                    task.add_done_callback(tasks.discard)
                    return JSONResponse(job, status_code=202)
                if path in ("/api/chat", "/api/chat/stream"):
                    return await chat(request, body, path.endswith("/stream"))
            raise ApiError(404, "接口不存在。")
        except ApiError as e:
            return JSONResponse({"error": e.message}, status_code=e.status)
        except DiscoveryUnavailable as e:
            return JSONResponse({"error": str(e)}, status_code=503)
        except (ChecklistConflict, LearningConflict, SectionConflict, WikiConflict) as e:
            return JSONResponse({"error": str(e)}, status_code=409)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        except Exception:
            logging.getLogger(__name__).exception("Request failed")
            return JSONResponse({"error": "请求处理失败，请查看服务端日志。"}, status_code=500)

    return app
