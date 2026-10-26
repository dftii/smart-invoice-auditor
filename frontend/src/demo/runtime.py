"""In-browser ASGI bridge for the SmartInvoice-FACe Auditor FastAPI backend (Pyodide).

Shipped inside backend.zip (next to app/) and extracted to /app by src/demo/worker.ts. The database starts empty:

    const runtime = pyodide.pyimport("runtime")
    await runtime.boot_js()      // shims + import app.main + ASGI lifespan startup (init_db)
    const r = await runtime.handle_js(method, path, query, headers, bodyUint8Array)

Public coroutine API:
    handle(method, path, query, headers, body_bytes) -> (status:int, headers:list[tuple[str,str]], body:bytes)
    handle_js(...)  same, but takes/returns JS-friendly values (JsProxy in, {status, headers, body:Uint8Array} out)
"""

from __future__ import annotations

import os
import sys
import types
from urllib.parse import quote

APP_ROOT = "/app"  # directory that contains the `app` package

_state: dict = {"app": None, "lifespan": None, "booted": False}


# --------------------------------------------------------------------------- environment / shims


def configure_env() -> None:
    os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/demo.db")
    os.environ.setdefault("UPLOAD_DIR", "/tmp/uploads")
    os.environ.setdefault("ENVIRONMENT", "production")
    os.environ.pop("ANTHROPIC_API_KEY", None)  # AI mode off -> DemoParserStrategy
    for p in (APP_ROOT,):
        if p not in sys.path:
            sys.path.insert(0, p)


def stub_missing_modules() -> list[str]:
    """Stub packages that are not installed in the browser (only needed while the backend imports them eagerly)."""
    stubbed = []
    for name in ("anthropic",):
        try:
            __import__(name)
        except ImportError:
            m = types.ModuleType(name)
            # `except anthropic.BadRequestError` is only evaluated on the AI path, which is disabled; give every
            # attribute a harmless exception class so even a stray lookup cannot crash with AttributeError.
            m.__getattr__ = lambda attr, _n=name: type(attr, (Exception,), {})  # type: ignore[attr-defined]
            sys.modules[name] = m
            stubbed.append(name)
    return stubbed


def patch_threadpool() -> None:
    """Pyodide has no threads: make anyio.to_thread.run_sync call the function inline on the event loop.

    Starlette (run_in_threadpool, FileResponse, UploadFile), FastAPI (sync endpoints, sync generator dependencies via
    contextmanager_in_threadpool) all go through ``anyio.to_thread.run_sync`` looked up at call time, so patching the
    module attribute is enough. Sync endpoints therefore block the (single) browser worker thread, which is fine here.
    """
    import anyio.to_thread

    async def run_sync(func, *args, abandon_on_cancel=False, cancellable=None, limiter=None):  # noqa: ARG001
        return func(*args)

    anyio.to_thread.run_sync = run_sync


# --------------------------------------------------------------------------- ASGI driver


async def _lifespan_start(app) -> None:
    import asyncio

    to_app: asyncio.Queue = asyncio.Queue()
    from_app: asyncio.Queue = asyncio.Queue()

    async def receive():
        return await to_app.get()

    async def send(message):
        await from_app.put(message)

    task = asyncio.ensure_future(app({"type": "lifespan", "asgi": {"version": "3.0"}, "state": {}}, receive, send))
    await to_app.put({"type": "lifespan.startup"})
    msg = await from_app.get()
    if msg["type"] != "lifespan.startup.complete":
        raise RuntimeError(f"lifespan startup failed: {msg}")
    _state["lifespan"] = (task, to_app, from_app)


async def asgi_request(app, method: str, path: str, query: str = "", headers=None, body: bytes = b""):
    """Run one HTTP request through an ASGI app without any network. Returns (status, [(k, v)], body)."""
    raw_headers = []
    has_host = False
    for k, v in (headers.items() if hasattr(headers, "items") else (headers or [])):
        k = str(k).lower()
        has_host |= k == "host"
        raw_headers.append((k.encode("latin-1"), str(v).encode("latin-1")))
    if not has_host:
        raw_headers.append((b"host", b"localhost"))
    if body and not any(k == b"content-length" for k, _ in raw_headers):
        raw_headers.append((b"content-length", str(len(body)).encode()))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method.upper(),
        "scheme": "http",
        "path": path,
        "raw_path": quote(path, safe="/%").encode("ascii"),
        "query_string": (query or "").lstrip("?").encode("latin-1"),
        "root_path": "",
        "headers": raw_headers,
        "client": ("127.0.0.1", 0),
        "server": ("localhost", 80),
        "state": {},
    }
    sent_body = False

    async def receive():
        nonlocal sent_body
        if not sent_body:
            sent_body = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    status = 500
    resp_headers: list = []
    chunks: list[bytes] = []

    async def send(message):
        nonlocal status, resp_headers
        if message["type"] == "http.response.start":
            status = message["status"]
            resp_headers = [(k.decode("latin-1"), v.decode("latin-1")) for k, v in message.get("headers", [])]
        elif message["type"] == "http.response.body":
            chunks.append(message.get("body", b""))

    await app(scope, receive, send)
    return status, resp_headers, b"".join(chunks)


# --------------------------------------------------------------------------- boot


async def boot() -> dict:
    """Idempotent: shims, import app.main, run ASGI lifespan startup (init_db creates the empty tables)."""
    if _state["booted"]:
        return {"already": True}
    configure_env()
    info = {"stubbed": stub_missing_modules()}
    patch_threadpool()
    import app.main  # noqa: PLC0415

    _state["app"] = app.main.app
    await _lifespan_start(app.main.app)
    _state["booted"] = True
    return info


# --------------------------------------------------------------------------- public API


async def boot_js() -> str:
    """JS entry point for boot(): returns a JSON string."""
    import json

    return json.dumps(await boot())


async def handle(method, path, query="", headers=None, body=b""):
    if not _state["booted"]:
        await boot()
    return await asgi_request(_state["app"], method, path, query, headers, bytes(body or b""))


async def handle_js(method, path, query, headers, body):
    """JS entry point. `headers`: plain JS object; `body`: Uint8Array/ArrayBuffer/null."""
    from pyodide.ffi import JsProxy, to_js
    from js import Object

    h = headers.to_py() if isinstance(headers, JsProxy) else (headers if isinstance(headers, dict) else {})
    if isinstance(body, JsProxy):
        b = bytes(body.to_py())  # Uint8Array / ArrayBuffer -> memoryview -> bytes
    elif isinstance(body, (bytes, bytearray, memoryview)):
        b = bytes(body)
    else:  # None / JS null / undefined
        b = b""
    status, rh, rb = await handle(method, path, query or "", h, b)
    return to_js({"status": status, "headers": rh, "body": rb}, dict_converter=Object.fromEntries)
