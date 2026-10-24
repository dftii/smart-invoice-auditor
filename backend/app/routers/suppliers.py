"""Supplier endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas import InvoiceFilters, InvoiceSummary, Page, SupplierCreate, SupplierUpdate, SupplierWithStats
from app.services import invoice_service as service

router = APIRouter(prefix="/suppliers", tags=["Proveedores"])

DbSession = Annotated[Session, Depends(get_db)]
FilterParams = Annotated[InvoiceFilters, Query()]
PageParam = Annotated[int, Query(ge=1, description="Número de página (empieza en 1)")]
PageSizeParam = Annotated[int, Query(ge=1, le=200, description="Elementos por página")]

NOT_FOUND_RESPONSE = {404: {"description": "Proveedor no encontrado"}}


@router.get("", response_model=Page[SupplierWithStats], summary="Listar proveedores con su facturación")
def list_suppliers(
    db: DbSession,
    search: Annotated[str | None, Query(description="Busca por nombre o NIF/CIF")] = None,
    page: PageParam = 1,
    page_size: PageSizeParam = 20,
) -> Page[SupplierWithStats]:
    return service.list_suppliers(db, search, page, page_size)


@router.post(
    "",
    response_model=SupplierWithStats,
    status_code=status.HTTP_201_CREATED,
    summary="Crear un proveedor",
    responses={409: {"description": "Ya existe un proveedor con ese NIF/CIF"}},
)
def create_supplier(payload: SupplierCreate, db: DbSession) -> SupplierWithStats:
    try:
        supplier = service.create_supplier(db, payload)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return service.get_supplier_with_stats(db, supplier.id)


@router.get(
    "/{supplier_id}",
    response_model=SupplierWithStats,
    summary="Detalle de un proveedor",
    responses=NOT_FOUND_RESPONSE,
)
def get_supplier(supplier_id: int, db: DbSession) -> SupplierWithStats:
    return service.get_supplier_with_stats(db, supplier_id)


@router.patch(
    "/{supplier_id}",
    response_model=SupplierWithStats,
    summary="Actualizar los datos de un proveedor",
    responses=NOT_FOUND_RESPONSE,
)
def update_supplier(supplier_id: int, payload: SupplierUpdate, db: DbSession) -> SupplierWithStats:
    try:
        supplier = service.get_supplier(db, supplier_id)
        service.update_supplier(db, supplier, payload)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return service.get_supplier_with_stats(db, supplier_id)


@router.get(
    "/{supplier_id}/invoices",
    response_model=Page[InvoiceSummary],
    summary="Facturas de un proveedor",
    responses=NOT_FOUND_RESPONSE,
)
def list_supplier_invoices(
    supplier_id: int,
    db: DbSession,
    filters: FilterParams,
    page: PageParam = 1,
    page_size: PageSizeParam = 20,
) -> Page[InvoiceSummary]:
    service.get_supplier(db, supplier_id)
    scoped = filters.model_copy(update={"supplier_id": supplier_id})
    return service.paginate_invoices(db, scoped, page, page_size)
