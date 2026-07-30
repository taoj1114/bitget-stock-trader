"""策略路由 — 策略列表 / 参数热更新 / 启用禁用"""

from dataclasses import asdict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.api.dependencies import get_registry

router = APIRouter(tags=["strategies"])


class UpdateParamsRequest(BaseModel):
    params: dict = {}


@router.get("/strategies")
async def get_strategies():
    registry = get_registry()
    result = {}
    for sid, s in registry.get_all().items():
        status = registry.get_status(sid)
        params_dict = asdict(s.params) if hasattr(s.params, "__dataclass_fields__") else {}
        result[sid] = {
            "id": sid, "name": s.name,
            "status": asdict(status) if status else {},
            "params": params_dict,
        }
    return result


@router.put("/strategies/{strategy_id}/params")
async def update_params(strategy_id: str, body: UpdateParamsRequest):
    registry = get_registry()
    s = registry.get(strategy_id)
    if not s:
        raise HTTPException(404, f"策略不存在: {strategy_id}")
    params = s.params
    for k, v in body.params.items():
        if hasattr(params, k):
            setattr(params, k, v)
    return {"strategy_id": strategy_id, "params": asdict(params) if hasattr(params, "__dataclass_fields__") else {}}


@router.put("/strategies/{strategy_id}/toggle")
async def toggle_strategy(strategy_id: str):
    registry = get_registry()
    s = registry.get(strategy_id)
    if not s:
        raise HTTPException(404, f"策略不存在: {strategy_id}")
    status = registry.get_status(strategy_id)
    if status and status.phase == "active":
        registry.pause(strategy_id)
        phase = "paused"
    else:
        registry.activate(strategy_id)
        phase = "active"
    return {"strategy_id": strategy_id, "new_phase": phase}
