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
from .explorer import search_files
from .github import import_github, parse_repo
from .models import MODEL_OPTIONS, discover_models, model_config, select_model
from .store import Store, now

ROOT = Path(__file__).resolve().parent.parent
DEMO = json.loads((Path(__file__).parent / "demo.json").read_text(encoding="utf-8"))
ASSETS = {"/": "index.html", **{f"/{name}": name for name in ("app.js", "style.css", "render.js", "sse.js", "reader.js", "model-order.js", "explorer.js", "checklist.js", "discovery.js", "vendor/marked.js", "vendor/purify.js")}}
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


def create_app(*, store=None, importer=import_github, agent=ask_agent, port=None, discovery_transport=None):
    port = port or int(os.getenv("PORT", "3211"))
    owned_store = store is None
    store = store or Store(Path(os.getenv("DATA_DIR", str(ROOT / "data"))) / "repopilot.db")
    checklists = ChecklistService(store)
    discovery = DiscoveryService(store, transport=discovery_transport)
    jobs, tasks = {}, set()
    state = {"busy": False}

    @asynccontextmanager
    async def lifespan(app):
        yield
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
            if len(raw) > 20000:
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
        return repo

    async def import_job(value, job):
        try:
            result = await importer(value, lambda message, progress: job.update(message=message, progress=progress))
            repo = store.save_repository(result["meta"], result["files"])
            job.update(state="done", progress=100, message="索引完成", repo=repo)
        except Exception as e:
            job.update(state="error", error=str(e) if isinstance(e, ValueError) else "仓库导入失败，请重试。")
        finally:
            release()

    async def chat(request, body, streaming):
        idle()
        repo = get_repo(body.get("repo"))
        session = store.default_session(repo["id"]) if "sessionId" not in body else (
            store.get_session(repo["id"], body["sessionId"]) if isinstance(body["sessionId"], str) else None)
        if not session:
            raise ApiError(404, "会话不存在或不属于当前仓库。")
        context = store.history(repo["id"], session["id"])
        replace_id = body.get("regenerateId")
        if "regenerateId" in body:
            if type(replace_id) is not int or not context or context[-1]["id"] != replace_id:
                raise ApiError(400, "只能重新生成当前会话最后一条回答。")
            question = context[-1]["question"]
            context = context[:-1]
        else:
            question = body.get("question")
        if not isinstance(question, str) or not question.strip() or len(question) > 2000:
            raise ApiError(400, "请输入 1–2000 字的问题。")
        config = select_model(body.get("model"), body.get("effort"))
        kwargs = dict(repo=repo, files=store.files(repo["id"]), chunks=store.chunks(repo["id"]), question=question,
                      history=context, force_extractive=body.get("mode") == "extractive", config=config)
        state["busy"] = True

        def save(result):
            return store.add_chat(repo["id"], {"question": question, **result, "createdAt": now()}, session["id"], replace_id)

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
                    return {"modelConfigured": bool(cfg["key"]), "model": cfg["model"], "models": options, "backend": "python"}
                if path == "/api/models":
                    return {"models": await discover_models()}
                if path == "/api/discover":
                    if q.get("refresh", "0") not in ("0", "1"):
                        raise ApiError(400, "刷新参数无效。")
                    return await discovery.get(source=q.get("source", "trending"), language=q.get("language", ""),
                                               since=q.get("since", "daily"), topic=q.get("topic", ""), refresh=q.get("refresh") == "1")
                if path == "/api/repos":
                    return store.list()
                if path == "/api/checklist":
                    return checklists.view(get_repo(q.get("repo")))
                if path == "/api/search":
                    repo = get_repo(q.get("repo"))
                    try:
                        limit = int(q.get("limit", "20"))
                    except ValueError as e:
                        raise ApiError(400, "结果数量需为 1–50。") from e
                    result = search_files(store.files(repo["id"]), q.get("q", ""), mode=q.get("mode", "literal"),
                                          path_filter=q.get("path", ""), limit=limit)
                    return {**result, "repo": repo["id"], "sha": repo["sha"], "partial": repo.get("partial", False)}
                if path == "/api/file":
                    repo = get_repo(q.get("repo"))
                    file = store.file(repo["id"], q.get("path", ""))
                    if not file:
                        raise ApiError(404, "该文件不在当前仓库索引中。")
                    return {**file, "sha": repo["sha"], "demo": repo.get("demo", False), "lines": len(file["content"].split("\n"))}
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
                    return {"repo": repo, "session": session, "sessions": store.sessions(repo["id"]),
                            "files": [{"path": f["path"], "lines": len(f["content"].split("\n"))} for f in store.files(repo["id"])],
                            "history": store.history(repo["id"], session["id"])}
                if path == "/api/sessions":
                    repo = get_repo(q.get("repo"))
                    return store.sessions(repo["id"], q.get("q", "")[:200])
            else:
                body = await body_json(request)
                if path in ("/api/checklist/generate", "/api/checklist/update"):
                    repo = get_repo(body.get("repo"))
                    if path.endswith("/generate"):
                        return checklists.generate(repo, body.get("planId"), body.get("revision"))
                    return checklists.update(repo, body)
                if path in ("/api/sessions/create", "/api/sessions/rename", "/api/sessions/delete"):
                    idle()
                    repo = get_repo(body.get("repo"))
                    action = path.rsplit("/", 1)[-1]
                    sid, title = body.get("sessionId"), body.get("title")
                    if action != "create" and (not isinstance(sid, str) or not store.get_session(repo["id"], sid)):
                        raise ApiError(404, "会话不存在。")
                    if action != "delete" and (not isinstance(title, str) or not 1 <= len(title.strip()) <= 80):
                        raise ApiError(400, "会话名称需为 1–80 字。")
                    if action == "create":
                        return JSONResponse(store.create_session(repo["id"], title.strip()), status_code=201)
                    if action == "rename":
                        store.rename_session(repo["id"], sid, title.strip())
                    else:
                        store.delete_session(repo["id"], sid)
                    return {"sessions": store.sessions(repo["id"])}
                if path == "/api/demo":
                    idle()
                    return store.get(DEMO["meta"]["id"]) or store.save_repository(DEMO["meta"], DEMO["files"])
                if path == "/api/import":
                    idle()
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
        except ChecklistConflict as e:
            return JSONResponse({"error": str(e)}, status_code=409)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        except Exception:
            logging.getLogger(__name__).exception("Request failed")
            return JSONResponse({"error": "请求处理失败，请查看服务端日志。"}, status_code=500)

    return app
