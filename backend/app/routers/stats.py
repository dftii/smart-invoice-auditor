"""Dashboard statistics endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas import DashboardStats
from app.services.stats_service import compute_dashboard_stats

router = APIRouter(prefix="/stats", tags=["Estadísticas"])


@router.get("/dashboard", response_model=DashboardStats, summary="Indicadores del panel de control")
def dashboard(db: Annotated[Session, Depends(get_db)]) -> DashboardStats:
    return compute_dashboard_stats(db)
