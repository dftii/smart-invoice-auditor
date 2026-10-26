"""Sample invoices to try the application with one click."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path
from fastapi.responses import FileResponse

from app.core.config import Settings, get_settings
from app.core.errors import NotFoundError
from app.schemas import SampleInvoice
from app.services import samples as service

router = APIRouter(prefix="/samples", tags=["Ejemplos"])

SettingsDep = Annotated[Settings, Depends(get_settings)]


@router.get("", response_model=list[SampleInvoice], summary="Listar las facturas de ejemplo")
def list_samples(settings: SettingsDep) -> list[SampleInvoice]:
    return service.list_samples(settings)


@router.get(
    "/{filename}",
    response_class=FileResponse,
    summary="Descargar una factura de ejemplo",
    responses={200: {"content": {"application/pdf": {}, "image/png": {}}}, 404: {"description": "Ejemplo no encontrado"}},
)
def get_sample(filename: Annotated[str, Path(max_length=100)], settings: SettingsDep) -> FileResponse:
    path = service.sample_path(settings, filename)
    if path is None:
        raise NotFoundError("Factura de ejemplo no encontrada")
    return FileResponse(path, media_type=service.media_type_of(filename), filename=filename)
