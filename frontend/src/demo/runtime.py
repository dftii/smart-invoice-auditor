"""In-browser ASGI bridge for the SmartInvoice-FACe Auditor FastAPI backend (Pyodide).

Shipped inside backend.zip (next to app/) and extracted to /app by src/demo/worker.ts. The database starts empty:

    const runtime = pyodide.pyimport("runtime")
    await runtime.boot_js()      // shims + import app.main + ASGI lifespan startup (init_db)
    const r = await runtime.handle_js(method, path, query, headers, bodyUint8Array)

Extraction runs in Modo Demo (text parser) unless an upload carries the visitor's own Anthropic key in the
``x-demo-anthropic-key`` header: then it uses the backend's ClaudeVisionStrategy with BrowserAnthropicClient.

Public coroutine API:
    handle(method, path, query, headers, body_bytes) -> (status:int, headers:list[tuple[str,str]], body:bytes)
    handle_js(...)  same, but takes/returns JS-friendly values (JsProxy in, {status, headers, body:Uint8Array} out)
"""

from __future__ import annotations

import os
import sys
import types
from urllib.parse import quote

from starlette.requests import Request  # module level: FastAPI resolves demo_extractor's annotation from these globals

APP_ROOT = "/app"  # directory that contains the `app` package

_state: dict = {"app": None, "lifespan": None, "booted": False}


# --------------------------------------------------------------------------- environment / shims


def configure_env() -> None:
    os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/demo.db")
    os.environ.setdefault("UPLOAD_DIR", "/tmp/uploads")
    os.environ.setdefault("SAMPLES_DIR", f"{APP_ROOT}/samples")  # sample invoices shipped in backend.zip
    os.environ.setdefault("ENVIRONMENT", "production")
    os.environ.pop("ANTHROPIC_API_KEY", None)  # AI mode off -> DemoParserStrategy
    for p in (APP_ROOT,):
        if p not in sys.path:
            sys.path.insert(0, p)


def stub_missing_modules() -> list[str]:
    """The Anthropic SDK is not installed in the browser: register a stand-in ``anthropic`` module.

    ClaudeVisionStrategy only uses the SDK for its exception classes (``except anthropic.AuthenticationError`` ...) and
    for the client, which the demo replaces with BrowserAnthropicClient. The stand-in defines the same exception
    hierarchy as the SDK (APITimeoutError < APIConnectionError < APIError, the 4xx classes < APIStatusError), so the
    strategy's error handling and Spanish messages work unchanged.
    """
    try:
        __import__("anthropic")
        return []
    except ImportError:
        pass

    class APIError(Exception):
        def __init__(self, message: str, status_code: int | None = None) -> None:
            super().__init__(message)
            self.message = message
            self.status_code = status_code

    class APIConnectionError(APIError):
        pass

    class APITimeoutError(APIConnectionError):
        pass

    class APIStatusError(APIError):
        pass

    class BadRequestError(APIStatusError):
        pass

    class AuthenticationError(APIStatusError):
        pass

    class PermissionDeniedError(APIStatusError):
        pass

    class RateLimitError(APIStatusError):
        pass

    module = types.ModuleType("anthropic")
    for cls in (
        APIError,
        APIConnectionError,
        APITimeoutError,
        APIStatusError,
        BadRequestError,
        AuthenticationError,
        PermissionDeniedError,
        RateLimitError,
    ):
        cls.__module__ = "anthropic"
        setattr(module, cls.__name__, cls)
    sys.modules["anthropic"] = module
    return ["anthropic"]


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


# --------------------------------------------------------------------------- AI with the visitor's own API key

# The demo never ships an API key. A visitor may paste their own Anthropic key in the UI; the demo transport then sends
# it with each upload in this header, and only for that request the extractor uses ClaudeVisionStrategy (the same
# prompt, tool schema and normalization as the server) with a client that calls the Anthropic API from the browser.
AI_KEY_HEADER = "x-demo-anthropic-key"
ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"


def _api_error_message(text: str, status: int) -> str:
    import json

    try:
        error = json.loads(text).get("error") or {}
        message = str(error.get("message") or "").strip()
    except (ValueError, AttributeError):
        message = ""
    return message or f"HTTP {status}"


def _status_error_class(status: int) -> type:
    import anthropic

    return {
        400: anthropic.BadRequestError,
        401: anthropic.AuthenticationError,
        403: anthropic.PermissionDeniedError,
        429: anthropic.RateLimitError,
    }.get(status, anthropic.APIStatusError)


class _Messages:
    def __init__(self, client: BrowserAnthropicClient) -> None:
        self._client = client

    def create(self, **params):
        return self._client.post_message(params)


class BrowserAnthropicClient:
    """Synchronous stand-in for ``anthropic.Anthropic`` (only ``messages.create``) that runs in the Web Worker.

    The backend extraction code is synchronous, so the request uses a synchronous XMLHttpRequest, which browsers allow
    inside workers. The response is returned as attribute objects (``response.content[i].type``, ``.name``,
    ``.input``, ``response.stop_reason``), the shape ClaudeVisionStrategy reads from the SDK.
    """

    def __init__(self, api_key: str, timeout_seconds: float) -> None:
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self.messages = _Messages(self)

    def post_message(self, params: dict):
        import json
        import time

        import anthropic
        from js import XMLHttpRequest
        from pyodide.ffi import JsException

        xhr = XMLHttpRequest.new()
        xhr.open("POST", ANTHROPIC_MESSAGES_URL, False)
        xhr.timeout = int(self._timeout_seconds * 1000)
        xhr.setRequestHeader("content-type", "application/json")
        xhr.setRequestHeader("x-api-key", self._api_key)
        xhr.setRequestHeader("anthropic-version", ANTHROPIC_VERSION)
        # Required by the API for calls made from a browser; the key is the visitor's own and never leaves their tab
        # except towards api.anthropic.com.
        xhr.setRequestHeader("anthropic-dangerous-direct-browser-access", "true")
        started = time.monotonic()
        try:
            xhr.send(json.dumps(params))
        except JsException as exc:
            if time.monotonic() - started >= self._timeout_seconds * 0.95:
                raise anthropic.APITimeoutError("Request timed out") from exc
            raise anthropic.APIConnectionError("Connection error") from exc

        status = int(xhr.status)
        text = str(xhr.responseText or "")
        if status == 0:  # CORS rejection, offline, blocked by an extension...
            raise anthropic.APIConnectionError("Connection error")
        if status >= 400:
            raise _status_error_class(status)(_api_error_message(text, status), status)
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise anthropic.APIError("Invalid JSON in the API response") from exc
        blocks = [types.SimpleNamespace(**block) for block in data.get("content") or [] if isinstance(block, dict)]
        return types.SimpleNamespace(content=blocks, stop_reason=data.get("stop_reason"), model=data.get("model"))


def demo_extractor(request: Request):
    """Replaces the ``get_extractor`` dependency: Claude with the visitor's key when the header is present."""
    from app.core.config import get_settings
    from app.services.extraction import ClaudeVisionStrategy, InvoiceExtractor

    settings = get_settings()
    api_key = (request.headers.get(AI_KEY_HEADER) or "").strip()
    if not api_key:
        return InvoiceExtractor(settings)
    client = BrowserAnthropicClient(api_key, settings.ai_timeout_seconds)
    return InvoiceExtractor(settings, ClaudeVisionStrategy(settings, client=client))


def install_ai_override(app) -> None:
    from app.services.extraction import get_extractor

    app.dependency_overrides[get_extractor] = demo_extractor


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
    install_ai_override(app.main.app)
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
