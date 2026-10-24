"""FastAPI application factory for SmartInvoice-FACe Auditor."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import ServiceUnavailableError, register_exception_handlers
from app.core.logging import configure_logging
from app.db.database import get_db, init_db
from app.routers import invoices, stats, suppliers
from app.schemas import HealthResponse
from app.services.extractor import get_extractor

logger = logging.getLogger(__name__)

DESCRIPTION = """
API de auditoría de facturas para proveedores de la Administración Pública española.

* **Extracción**: sube un PDF o una imagen y se extraen los datos con IA (Claude) o con el analizador de demostración.
* **Auditoría**: NIF/NIE/CIF, fechas, cuadre de bases, IVA e IRPF al céntimo, duplicados y datos DIR3 para FACe.
* **Exportación**: facturas validadas en formato **Facturae 3.2.2** (con firma XAdES opcional) y listados en CSV.
"""

OPENAPI_TAGS = [
    {"name": "Facturas", "description": "Subida, corrección, auditoría y exportación de facturas."},
    {"name": "Proveedores", "description": "Emisores de las facturas y su facturación acumulada."},
    {"name": "Estadísticas", "description": "Indicadores agregados para el panel de control."},
    {"name": "Sistema", "description": "Estado del servicio."},
]


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    init_db()
    logger.info("%s %s started (extraction mode: %s)", settings.app_name, settings.app_version, get_extractor().mode)
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=DESCRIPTION,
        openapi_tags=OPENAPI_TAGS,
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Content-Disposition"],
    )
    register_exception_handlers(application)
    for router in (invoices.router, suppliers.router, stats.router):
        application.include_router(router, prefix=settings.api_prefix)

    @application.get("/health", response_model=HealthResponse, tags=["Sistema"], summary="Comprobar el estado")
    def health(db: Annotated[Session, Depends(get_db)]) -> HealthResponse:
        try:
            db.execute(text("SELECT 1"))
        except SQLAlchemyError as exc:
            logger.error("Health check failed: %s", exc)
            raise ServiceUnavailableError("Base de datos no disponible") from exc
        return HealthResponse(
            status="ok",
            app=settings.app_name,
            version=settings.app_version,
            database=db.get_bind().dialect.name,
            extraction_mode=get_extractor().mode,
        )

    return application


app = create_app()
