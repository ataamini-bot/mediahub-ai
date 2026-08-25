from fastapi import FastAPI

from app.api.downloads import router as downloads_router
from app.api.users import router as users_router


app = FastAPI(
    title="MediaHub AI API",
    description="Media downloading and AI content assistant platform",
    version="0.1.0",
)


app.include_router(downloads_router)
app.include_router(users_router)


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
