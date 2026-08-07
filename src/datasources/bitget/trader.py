"""Bitget 实盘交易 API 封装

支持:
    - 开多/开空 (市价单)
    - 平仓 (市价)
    - 查持仓
    - 查账户余额
    - 设止盈止损

API: Bitget V3 Mix (USDT-M Futures)
文档: https://www.bitget.com/api-doc/contract/mix/overview

认证: API Key + Secret + Passphrase, HMAC-SHA256 签名
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.bitget.com"


@dataclass
class BitgetCredentials:
    api_key: str
    secret_key: str
    passphrase: str

    @classmethod
    def from_env(cls) -> "BitgetCredentials":
        return cls(
            api_key=os.environ.get("BITGET_API_KEY", ""),
            secret_key=os.environ.get("BITGET_SECRET_KEY", ""),
            passphrase=os.environ.get("BITGET_PASSPHRASE", ""),
        )

    @property
    def valid(self) -> bool:
        return bool(self.api_key and self.secret_key and self.passphrase)


@dataclass
class RealPosition:
    symbol: str
    side: str       # long / short
    quantity: float
    entry_price: float
    mark_price: float
    margin: float
    unrealized_pnl: float
    leverage: int


@dataclass
class RealAccount:
    equity: float          # 净值 USDT
    available: float       # 可用
    used_margin: float     # 已用保证金
    unrealized_pnl: float  # 未实现盈亏


class BitgetTrader:
    """Bitget 实盘交易客户端"""

    def __init__(self, credentials: Optional[BitgetCredentials] = None):
        self._creds = credentials or BitgetCredentials.from_env()
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def ready(self) -> bool:
        return self._creds.valid

    def _sign(self, timestamp: str, method: str, path: str, body: str = "") -> str:
        """HMAC-SHA256 签名。"""
        message = f"{timestamp}{method.upper()}{path}{body}"
        mac = hmac.new(
            self._creds.secret_key.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        )
        return base64.b64encode(mac.digest()).decode()

    def _headers(self, method: str, path: str, body: str = "") -> dict:
        ts = str(int(time.time() * 1000))
        return {
            "ACCESS-KEY": self._creds.api_key,
            "ACCESS-SIGN": self._sign(ts, method, path, body),
            "ACCESS-TIMESTAMP": ts,
            "ACCESS-PASSPHRASE": self._creds.passphrase,
            "Content-Type": "application/json",
        }

    async def _request(self, method: str, path: str, body: dict = None) -> dict:
        if not self._client:
            self._client = httpx.AsyncClient(timeout=15)
        body_str = json.dumps(body) if body else ""
        headers = self._headers(method, path, body_str)
        url = f"{BASE_URL}{path}"
        resp = await self._client.request(method, url, headers=headers, content=body_str)
        if resp.status_code != 200:
            logger.error("Bitget API %s %s → %d: %s", method, path, resp.status_code, resp.text[:200])
            return {"code": str(resp.status_code), "msg": resp.text[:200]}
        return resp.json()

    # ── 合约信息 (最低下单量/步进) ──────────
    _contract_cache: dict = {}

    async def get_contract_info(self, symbol: str) -> dict:
        """获取合约规格: minTradeNum(最小下单量) / sizeMultiplier(步进) / minTradeUSDT(最低价值)。"""
        sym = symbol.upper()
        if sym in self._contract_cache:
            return self._contract_cache[sym]
        try:
            if not self._client:
                self._client = httpx.AsyncClient(timeout=15)
            resp = await self._client.get(
                f"{BASE_URL}/api/v2/mix/market/contracts",
                params={"productType": "USDT-FUTURES", "symbol": f"{sym}USDT"},
                timeout=10,
            )
            data = resp.json().get("data", [{}])
            info = (data[0] if data else {}) or {}
            result = {
                "min_trade_num": float(info.get("minTradeNum", 0.01)),
                "size_multiplier": float(info.get("sizeMultiplier", 0.01)),
                "min_trade_usdt": float(info.get("minTradeUSDT", 5)),
            }
            self._contract_cache[sym] = result
            return result
        except Exception as e:
            logger.warning("contract info %s 失败: %s", symbol, e)
            return {"min_trade_num": 0.01, "size_multiplier": 0.01, "min_trade_usdt": 5}

    # ═══ 下单 ═══════════════════════════════════

    async def place_order(
        self,
        symbol: str,
        side: str,          # buy / sell
        trade_side: str,    # open / close
        quantity: float,
        price: float = 0,    # 0 = 市价
        leverage: int = 5,
    ) -> dict:
        """下单（市价/限价）。"""
        body = {
            "symbol": f"{symbol}USDT",
            "productType": "USDT-FUTURES",
            "marginMode": "crossed",
            "marginCoin": "USDT",
            "size": str(quantity),
            "side": side,               # buy / sell
            "tradeSide": trade_side,     # open / close
            "orderType": "market" if price <= 0 else "limit",
        }
        if price > 0:
            body["price"] = str(price)
        if trade_side == "open":
            body["leverage"] = str(leverage)

        return await self._request("POST", "/api/v2/mix/order/place-order", body)

    async def close_position(self, symbol: str, hold_side: str = "", size: float = 0) -> dict:
        """市价平仓 (V2 正确端点: place-order + tradeSide=close)。

        hold_side: 持仓方向 long / short (决定平仓 side: 平多=sell, 平空=buy)
        size: 平仓数量 (0 = 全平)
        """
        side = "sell" if hold_side == "long" else "buy"
        body = {
            "symbol": f"{symbol}USDT",
            "productType": "USDT-FUTURES",
            "marginCoin": "USDT",
            "size": str(size) if size > 0 else "0",
            "side": side,
            "tradeSide": "close",
            "orderType": "market",
        }
        if hold_side:
            body["holdSide"] = hold_side
        return await self._request("POST", "/api/v2/mix/order/place-order", body)

    # ═══ 止盈止损 ═══════════════════════════════

    async def place_stop_order(
        self,
        symbol: str,
        hold_side: str,     # long / short (持仓方向)
        tpsl_side: str,     # buy / sell (平仓方向)
        trigger_price: float,
        quantity: float,
        plan_type: str = "pos_loss",  # pos_loss / pos_profit
    ) -> dict:
        """下止盈止损单。"""
        # 价格精度修复: AI计算可能产生浮点误差(450.5399999999999),
        # Bitget checkScale=2 要求最多2位小数 → 必须round, 否则挂单400失败
        trigger_price = round(float(trigger_price), 2)
        body = {
            "symbol": f"{symbol}USDT",
            "productType": "USDT-FUTURES",
            "marginCoin": "USDT",
            "holdSide": hold_side,
            "side": tpsl_side,
            "size": str(quantity),
            "triggerPrice": str(trigger_price),
            "orderType": "market",
            "planType": plan_type,
            "triggerType": "mark_price",
        }
        return await self._request("POST", "/api/v2/mix/order/place-tpsl-order", body)

    async def cancel_plan_order(
        self,
        symbol: str,
        hold_side: str,     # long / short
        plan_type: str = "pos_loss",  # pos_loss / pos_profit
    ) -> dict:
        """取消在途止盈止损计划单 (更新SL/TP前必须先取消旧单, 否则旧单残留触发)。"""
        body = {
            "symbol": f"{symbol}USDT",
            "productType": "USDT-FUTURES",
            "marginCoin": "USDT",
            "holdSide": hold_side,
            "planType": plan_type,
        }
        return await self._request("POST", "/api/v2/mix/order/cancel-plan-order", body)

    # ═══ 查询 ═══════════════════════════════════

    async def get_positions(self, symbol: str = "") -> list[RealPosition]:
        """查询持仓。"""
        params = {"productType": "USDT-FUTURES", "marginCoin": "USDT"}
        if symbol:
            params["symbol"] = f"{symbol}USDT"

        path = "/api/v2/mix/position/all-position?productType=USDT-FUTURES&marginCoin=USDT"
        result = await self._request("GET", path)

        data = result.get("data", [])
        if isinstance(data, dict):
            data = [data] if data else []

        positions = []
        for p in data:
            if float(p.get("total", 0)) <= 0:
                continue
            positions.append(RealPosition(
                symbol=p.get("symbol", "").replace("USDT", ""),
                side=p.get("holdSide", ""),
                quantity=float(p.get("total", 0)),
                entry_price=float(p.get("openPriceAvg", 0)),  # 均价
                mark_price=float(p.get("markPrice", 0)),
                margin=float(p.get("marginSize", 0)),
                unrealized_pnl=float(p.get("unrealizedPL", 0)),
                leverage=int(float(p.get("leverage", 5))),
            ))
        return positions

    async def get_account(self) -> RealAccount:
        """查询账户。"""
        path = "/api/v2/mix/account/accounts?productType=USDT-FUTURES&marginCoin=USDT"
        result = await self._request("GET", path)

        data = result.get("data", [])
        if isinstance(data, list) and data:
            data = data[0]  # 取第一个账户
        return RealAccount(
            equity=float(data.get("accountEquity", 0)),
            available=float(data.get("available", 0)),
            used_margin=float(data.get("margin", 0)),
            unrealized_pnl=float(data.get("unrealizedPL", 0)),
        )

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None
