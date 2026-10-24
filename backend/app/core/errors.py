"""Domain exceptions and the FastAPI exception handlers that map them to HTTP responses."""

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.services.extractor import ExtractionError
from app.services.face_exporter import FacturaeExportError

logger = logging.getLogger(__name__)


class AppError(Exception):
    """Base class for errors that carry an HTTP status and a Spanish, user-facing message."""

    status_code = 500
    default_detail = "Error interno del servidor"

    def __init__(self, detail: str | None = None) -> None:
        self.detail = detail or self.default_detail
        super().__init__(self.detail)


class NotFoundError(AppError):
    status_code = 404
    default_detail = "Recurso no encontrado"


class ConflictError(AppError):
    status_code = 409
    default_detail = "La operación entra en conflicto con el estado actual del recurso"


class InvalidInputError(AppError):
    status_code = 422
    default_detail = "Datos de entrada no válidos"


class PayloadTooLargeError(AppError):
    status_code = 413
    default_detail = "El archivo supera el tamaño máximo permitido"


class UnsupportedMediaTypeError(AppError):
    status_code = 415
    default_detail = "Tipo de archivo no soportado"


class ServiceUnavailableError(AppError):
    status_code = 503
    default_detail = "Servicio no disponible"


def _json_error(status_code: int, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": detail})


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_request: Request, exc: AppError) -> JSONResponse:
        return _json_error(exc.status_code, exc.detail)

    @app.exception_handler(ExtractionError)
    async def _extraction_error(_request: Request, exc: ExtractionError) -> JSONResponse:
        logger.warning("Extraction failed: %s", exc)
        message = str(exc) or "causa desconocida"
        return _json_error(422, f"No se pudo extraer la factura: {message}")

    @app.exception_handler(FacturaeExportError)
    async def _export_error(_request: Request, exc: FacturaeExportError) -> JSONResponse:
        message = str(exc) or "la factura no cumple los requisitos de exportación"
        return _json_error(409, message)
