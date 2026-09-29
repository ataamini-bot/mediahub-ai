from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.reporting import router as reporting_router
from app.api.operations import router as operations_router
from app.api.admin import router as admin_router
from app.api.admin_finance import router as admin_finance_router
from app.api.admin_statistics import router as admin_statistics_router
from app.api.downloads import router as downloads_router
from app.api.experience import router as experience_router
from app.api.payments import router as payments_router
from app.api.users import router as users_router
from app.api.customers import router as customers_router
from app.api.backups import router as backups_router
from app.api.broadcasts import router as broadcasts_router
from app.api.credit import router as credit_router
from app.services.system_monitor import (
    notify_backend_started,
    notify_backend_stopping,
)


@asynccontextmanager
async def lifespan(
    app: FastAPI,
):

    notify_backend_started()

    try:
        yield
    finally:
        notify_backend_stopping()


app = FastAPI(
    title="MediaHub AI API",
    description="Media downloading and AI content assistant platform",
    version="0.1.0",
    lifespan=lifespan,
)


app.include_router(downloads_router)
app.include_router(payments_router)
app.include_router(users_router)
app.include_router(admin_router)
app.include_router(admin_finance_router)
app.include_router(admin_statistics_router)
app.include_router(experience_router)
app.include_router(operations_router)
app.include_router(reporting_router)
app.include_router(customers_router)
app.include_router(backups_router)
app.include_router(broadcasts_router)
app.include_router(credit_router)


@app.get("/")
async def root():
    return {
        "name": "MediaHub AI",
        "status": "running",
        "version": "0.1.0",
    }


@app.get("/health")
async def health():
    return {
        "status": "healthy",
    }
