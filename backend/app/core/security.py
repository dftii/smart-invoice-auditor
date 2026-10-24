"""Optional API-key protection of the /api/v1 routes."""

import logging
import secrets
from typing import Annotated

from fastapi import Depends, Header

from app.core.config import Settings, get_settings
from app.core.errors import UnauthorizedError

logger = logging.getLogger(__name__)

API_KEY_HEADER = "X-API-Key"


def api_key_matches(settings: Settings, provided: str | None) -> bool:
    expected = settings.secret("api_key")
    if expected is None:
        return True
    return bool(provided) and secrets.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


def require_api_key(
    settings: Annotated[Settings, Depends(get_settings)],
    x_api_key: Annotated[str | None, Header(description="Clave de API (solo si el servidor tiene API_KEY)")] = None,
) -> None:
    """Dependency of every /api/v1 route: no-op unless API_KEY is configured."""
    if not api_key_matches(settings, x_api_key):
        raise UnauthorizedError("Clave de API ausente o no válida: envíe la cabecera X-API-Key")


def warn_if_signing_unprotected(settings: Settings) -> None:
    if settings.facturae_cert_path is not None and settings.api_key is None:
        logger.warning(
            "FACTURAE_CERT_PATH is set but API_KEY is not: Facturae signing is disabled until an API key is "
            "configured, so anonymous clients cannot sign invoices with the server certificate."
        )
