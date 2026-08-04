import asyncio, time
from src.datasources.bitget.market import BitgetMarketSource
from src.analyzers.technical import TechnicalAnalyzer
from src.analyzers.market_regime import MarketRegimeDetector
from src.strategies.ai_native import AINativeDecisionMaker, AIInput

async def t():
    m = BitgetMarketSource()
    tech = TechnicalAnalyzer()
    reg = MarketRegimeDetector()
    ai = AINativeDecisionMaker()
    sym = 'GME'
    k5 = await m.get_klines(sym, '5m', 500)
    k1h = await m.get_klines(sym, '1H', 500)
    q = await m.get_quote(sym)
    i5 = tech.calculate(k5); r5 = reg.detect(k5)
    i1 = tech.calculate(k1h); r1 = reg.detect(k1h)
    inp = AIInput(
        symbol=sym, mark_price=q.mark_price, change_pct=q.change_pct*100,
        klines_1h=k1h, klines_4h=[], klines_1d=[],
        ind_1h=dict(rsi=i1.rsi14, ma10=i1.ma10, ma30=i1.ma30, macd=i1.macd,
                    atr=i1.atr14, adx=r1.adx, regime=r1.regime,
                    bb_position=0.5, volume_ratio=i1.volume_ratio, vwap=i1.vwap),
        ind_4h=None, ind_1d=None,
        ind_5m=dict(rsi=i5.rsi14, ma10=i5.ma10, ma30=i5.ma30, macd=i5.macd,
                    atr=i5.atr14, adx=r5.adx, regime=r5.regime,
                    bb_position=0.5, volume_ratio=i5.volume_ratio, vwap=i5.vwap),
        news=[], news_summary='', bench={}, open_interest=0, funding_rate=0,
        session='regular')

    # 方式A: 当前 (直接输出)
    t0 = time.time()
    sig_a = await ai.decide(inp)
    ta = time.time() - t0

    # 方式B: 先思考再决策
    prompt_b = (
        f"你是日内交易AI。分析 {sym} 并决策。\n"
        f"5m: RSI={inp.ind_5m['rsi']:.0f} MA10={inp.ind_5m['ma10']:.2f} MA30={inp.ind_5m['ma30']:.2f} "
        f"ATR={inp.ind_5m['atr']:.2f} VWAP={inp.ind_5m['vwap']:.2f} 量比={inp.ind_5m['volume_ratio']:.1f}\n"
        f"1H: RSI={inp.ind_1h['rsi']:.0f} ADX={inp.ind_1h['adx']:.0f} regime={inp.ind_1h['regime']}\n"
        f"现价 ${inp.mark_price:.2f} ({inp.change_pct:+.1f}%)\n"
        "请先逐步推理: 1)趋势方向 2)入场位置是否透支 3)风险收益比, 然后输出JSON决策。"
    )
    t0 = time.time()
    raw_b = await ai._call(system="你是日内交易分析师。先推理再决策。",
                           prompt=prompt_b, model=ai._model, temp=0.3,
                           max_tokens=2000, json_mode=False)
    tb = time.time() - t0
    result_b = AINativeDecisionMaker._parse_json(raw_b)

    print(f'=== GME 对比 ===')
    print(f'方式A(直接输出): {sig_a.action if sig_a else "None"} | 耗时 {ta:.1f}s')
    print(f'方式B(先思考):   {result_b.get("action") if result_b else "None"} | 耗时 {tb:.1f}s')
    if sig_a:
        print(f'  A: SL={sig_a.stop_loss} TP={sig_a.take_profits} | {str(sig_a.reason)[:60]}')
    if result_b:
        print(f'  B: SL={result_b.get("stop_loss")} TP={result_b.get("take_profit")} | {str(result_b.get("reason"))[:60]}')
    await m.close()

asyncio.run(t())
