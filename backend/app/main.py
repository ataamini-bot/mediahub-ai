from fastapi import FastAPI

app = FastAPI(
    title="MediaHub AI API",
    description="Media downloading and AI content assistant platform",
    version="0.1.0",
)


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
