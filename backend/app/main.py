from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .config import settings

from .api import auth, config as config_api, pools, exchanges

app = FastAPI(
    title=settings.PROJECT_NAME,
    description="Scalpyn API - ZERO HARDCODE Quant Platform",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "https://scalpyn.vercel.app",
        "https://www.scalpyn.vercel.app",
    ],
    allow_origin_regex="https://scalpyn.*\.vercel\.app",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(config_api.router)
app.include_router(pools.router)
app.include_router(exchanges.router)

@app.get("/api/health")
async def health_check():
    return {"status": "ok"}
