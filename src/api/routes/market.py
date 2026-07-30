"""行情路由 — quotes / klines / symbols"""

from fastapi import APIRouter, HTTPException

from src.datasources.bitget.market import BitgetMarketSource
from src.datasources.bitget.symbols import BitgetSymbolSource

router = APIRouter(tags=["market"])


@router.get("/symbols")
async def get_symbols():
    src = BitgetSymbolSource()
    try:
        stocks = await src.get_stock_symbols()
        return {"total": len(stocks), "symbols": [
            {"symbol": s.symbol, "type": s.symbol_type,
             "leverage": f"{s.min_leverage}-{s.max_leverage}x"} for s in stocks[:50]
        ]}
    finally:
        await src.close()


@router.get("/quote/{symbol}")
async def get_quote(symbol: str):
    src = BitgetMarketSource()
    try:
        quote = await src.get_quote(symbol.upper())
        if not quote:
            raise HTTPException(404, f"无法获取 {symbol} 报价")
        return {"symbol": quote.symbol, "price": quote.price,
                "change_pct": quote.change_pct, "high_24h": quote.high_24h,
                "low_24h": quote.low_24h, "volume_24h": quote.volume_24h,
                "mark_price": quote.mark_price}
    finally:
        await src.close()


@router.get("/klines/{symbol}")
async def get_klines(symbol: str, interval: str = "1H", limit: int = 100):
    src = BitgetMarketSource()
    try:
        klines = await src.get_klines(symbol.upper(), interval, limit)
        return {"symbol": symbol, "interval": interval,
                "count": len(klines), "klines": [
                    {"ts": k.timestamp, "o": k.open, "h": k.high, "l": k.low, "c": k.close, "v": k.volume}
                    for k in klines
                ]}
    finally:
        await src.close()
