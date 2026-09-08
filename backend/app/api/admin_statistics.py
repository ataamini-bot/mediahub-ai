from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.internal_auth import require_internal_api_key
from app.db.session import get_db
from app.services.admin_access import AdminAccessDenied, AdminAccessService, PermissionCode
from app.services.admin_statistics import AdminStatisticsService


router = APIRouter(
    prefix="/admin/statistics",
    tags=["admin-statistics"],
    dependencies=[Depends(require_internal_api_key)],
)


@router.get("")
async def statistics(
    actor_telegram_id: int = Query(gt=0),
    section: str = Query(default="overview", pattern="^(overview|users|subscriptions|finance|downloads|charts|kpis|all)$"),
    period: str = Query(default="30d", pattern="^(today|7d|30d|all)$"),
    chart_range: str = Query(default="30d", pattern="^(today|7d|30d|3mo|1yr)$"),
    metric: str = Query(default="users", pattern="^(users|sales|subscriptions|downloads)$"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        await AdminAccessService(db).require_permission(
            actor_telegram_id,
            PermissionCode.STATISTICS_VIEW,
        )
        return await AdminStatisticsService(db).get(section, period, chart_range, metric)
    except AdminAccessDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
