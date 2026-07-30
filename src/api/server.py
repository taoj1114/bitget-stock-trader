"""FastAPI 应用入口"""

from dataclasses import asdict, is_dataclass
from datetime import datetime

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.encoders import jsonable_encoder

app = FastAPI(title="Bitget Stock Trader", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup():
    from src.api.dependencies import init_all
    init_all()


@app.get("/api/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


# ── 注册路由 ─────────────────────────────────

from src.api.routes.market import router as market_router
from src.api.routes.strategies import router as strategies_router
from src.api.routes.trading import router as trading_router
from src.api.routes.optimization import router as optimization_router

app.include_router(market_router, prefix="/api/v1")
app.include_router(strategies_router, prefix="/api/v1")
app.include_router(trading_router, prefix="/api/v1")
app.include_router(optimization_router, prefix="/api/v1")
