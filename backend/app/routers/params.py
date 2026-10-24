"""Query / path parameter types shared by the routers (bounded so out-of-range values answer 422, not 500)."""

from typing import Annotated

from fastapi import Depends, Path, Query
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas import MAX_ID, InvoiceFilters

DbSession = Annotated[Session, Depends(get_db)]
FilterParams = Annotated[InvoiceFilters, Query()]
PageParam = Annotated[int, Query(ge=1, le=MAX_ID, description="Número de página (empieza en 1)")]
PageSizeParam = Annotated[int, Query(ge=1, le=200, description="Elementos por página")]
InvoiceIdPath = Annotated[int, Path(ge=1, le=MAX_ID, description="Identificador de la factura")]
SupplierIdPath = Annotated[int, Path(ge=1, le=MAX_ID, description="Identificador del proveedor")]
