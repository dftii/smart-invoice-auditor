"""ASGI middleware that rejects oversized uploads from the Content-Length header, before the body is read."""

import json

from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import get_settings

# Multipart framing (boundaries, headers) adds a little to the file size; the exact limit is still enforced
# on the file itself when it is read.
MULTIPART_OVERHEAD_BYTES = 64 * 1024


class UploadSizeLimitMiddleware:
    def __init__(self, app: ASGIApp, upload_path_suffix: str = "/invoices/upload") -> None:
        self.app = app
        self.upload_path_suffix = upload_path_suffix

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] == "POST" and scope["path"].endswith(self.upload_path_suffix):
            settings = get_settings()
            declared = self._content_length(scope)
            if declared is not None and declared > settings.max_upload_size_bytes + MULTIPART_OVERHEAD_BYTES:
                await self._reject(send, settings.max_upload_size_mb)
                return
        await self.app(scope, receive, send)

    @staticmethod
    def _content_length(scope: Scope) -> int | None:
        for name, value in scope["headers"]:
            if name == b"content-length":
                try:
                    return int(value)
                except ValueError:
                    return None
        return None

    @staticmethod
    async def _reject(send: Send, limit_mb: int) -> None:
        body = json.dumps({"detail": f"El archivo supera el tamaño máximo de {limit_mb} MB"}, ensure_ascii=False).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"connection", b"close"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
