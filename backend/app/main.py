"""FastAPI application factory for SmartInvoice-FACe Auditor."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import ServiceUnavailableError, register_exception_handlers
from app.core.limits import UploadSizeLimitMiddleware
from app.core.logging import configure_logging
from app.core.security import require_api_key, warn_if_signing_unprotected
from app.db.database import get_db, init_db, make_session_factory
from app.routers import invoices, samples, stats, suppliers
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
    {"name": "Ejemplos", "description": "Facturas de ejemplo para probar la aplicación con un clic."},
    {"name": "Sistema", "description": "Estado del servicio."},
]


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    try:
        settings.upload_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:  # read-only or virtual file system: uploads will fail later with a clear error
        logger.warning("Could not create the upload directory %s: %s", settings.upload_dir, exc)
    init_db(application.state.engine)
    warn_if_signing_unprotected(settings)
    logger.info("%s %s started (extraction mode: %s)", settings.app_name, settings.app_version, get_extractor().mode)
    yield


def create_app(engine: Engine | None = None) -> FastAPI:
    """Build the application. `engine` overrides the database (tests, embedded runtimes); by default the
    engine comes from DATABASE_URL and is created lazily, so importing this module never opens a database."""
    settings = get_settings()
    application = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=DESCRIPTION,
        openapi_tags=OPENAPI_TAGS,
        lifespan=lifespan,
    )
    application.state.engine = engine
    application.state.session_factory = make_session_factory(engine) if engine is not None else None
    application.add_middleware(UploadSizeLimitMiddleware)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Content-Disposition"],
    )
    register_exception_handlers(application)
    for router in (invoices.router, suppliers.router, stats.router, samples.router):
        application.include_router(router, prefix=settings.api_prefix, dependencies=[Depends(require_api_key)])

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
            database="ok",
            extraction_mode=get_extractor().mode,
        )

    return application


app = create_app()
