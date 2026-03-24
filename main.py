#!/usr/bin/env python3
"""
CLAUDE TRADING AGENT v4.0 — AGGRESSIVE MULTI-ASSET
Crypto (5-15m) + Stocks (60m) + Forex (30m)
Parallel processing, auto-triggers, time stops
"""

import time
import json
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

from config import Config
from data.bybit import BybitClient
from data.coingecko import CoinGeckoClient
from data.cryptopanic import CryptoPanicClient
from data.coinglass import CoinglassClient
from data.stocks import StockClient
from data.forex import ForexClient
from analysis.indicators import compute_indicators
from analysis.smc import detect_smc
from analysis.liquidity import analyze_liquidity
from analysis.claude_brain import ClaudeBrain, TradeSignal
from analysis.scenario_engine import ScenarioEngine
from risk.manager import RiskManager
from trading.paper_engine import PaperEngine, compute_leverage
from trading.executor import Executor
from utils.logger import get_logger
from utils.state import State
from utils.trade_logger import TradeLogger
from utils.learning_log import LearningLog
from utils import display


# ── Auto-trigger scoring (no Claude needed) ──────────────────────────────────

def auto_trigger_score(df_15m, pair, logger):
    """Score automatic entry triggers. Returns (score_bonus, direction, reasons)."""
    try:
        close = df_15m["close"]
        high = df_15m["high"]
        low = df_15m["low"]
        volume = df_15m["volume"]
        price = float(close.iloc[-1])

        score = 0
        direction = None  # "BUY" or "SELL"
        reasons = []

        # RSI calculation
        delta = close.diff()
        gain = delta.clip(lower=0).ewm(com=13, adjust=False).mean()
        loss = (-delta.clip(upper=0)).ewm(com=13, adjust=False).mean()
        last_gain, last_loss = float(gain.iloc[-1]), float(loss.iloc[-1])
        if last_loss == 0:
            rsi = 100.0
        elif last_gain == 0:
            rsi = 0.0
        else:
            rsi = 100 - (100 / (1 + last_gain / last_loss))

        # Volume
        vol_current = float(volume.iloc[-1])
        vol_avg = float(volume.iloc[-20:].mean()) if len(volume) >= 20 else float(volume.mean())
        vol_ratio = vol_current / vol_avg if vol_avg > 0 else 0

        # RSI extreme crosses
        if rsi < 28:
            score += 40
            direction = "BUY"
            reasons.append(f"RSI={rsi:.0f}<28")
        elif rsi > 72:
            score += 40
            direction = "SELL"
            reasons.append(f"RSI={rsi:.0f}>72")

        # Volume spike + breakout
        if vol_ratio > 3 and len(close) > 5:
            h5 = float(high.iloc[-6:-1].max())
            l5 = float(low.iloc[-6:-1].min())
            if price > h5:
                score += 35
                direction = direction or "BUY"
                reasons.append(f"Vol{vol_ratio:.0f}x+breakout_high")
            elif price < l5:
                score += 35
                direction = direction or "SELL"
                reasons.append(f"Vol{vol_ratio:.0f}x+breakout_low")

        return score, direction, reasons
    except Exception as e:
        logger.warning(f"Auto-trigger failed for {pair}: {e}")
        return 0, None, []


# ── Time-based position stops ────────────────────────────────────────────────

def check_time_stops(state, executor, bybit, trade_logger, cfg, risk_mgr, logger, learning_log=None):
    """Close positions that exceeded their time limit."""
    now = datetime.now(timezone.utc)
    for pair in list(state.open_positions.keys()):
        pos = state.open_positions[pair]
        opened_str = pos.get("opened_at", "")
        if not opened_str:
            continue
        try:
            opened_at = datetime.fromisoformat(opened_str)
        except ValueError:
            continue

        strategy = pos.get("strategy", "SCALP")
        age_hours = (now - opened_at).total_seconds() / 3600

        # SCALP: 2h, SWING: 72h (3 days), POSITION: 72h max
        max_hours = {"SCALP": 2, "SWING": 72, "POSITION": 72}.get(strategy, 72)

        if age_hours > max_hours:
            logger.warning(f"TIME STOP: {pair} {strategy} open {age_hours:.1f}h > {max_hours}h")
            try:
                df = bybit.get_candles(pair, timeframe="1m", limit=1)
                price = float(df["close"].iloc[-1])
                result = executor._paper_engine.close_position(pair, price)
                if result and result.get("status") == "closed":
                    handle_close(result, pair, state, executor, risk_mgr,
                                 trade_logger, cfg, logger,
                                 positions_key="open_positions", asset_class="crypto",
                                 learning_log=learning_log)
            except Exception as e:
                logger.error(f"Time stop close failed for {pair}: {e}")


# ── Helpers ──────────────────────────────────────────────────────────────────

def pre_filter(bybit, pair, fear_greed_value, logger):
    """Aggressive pre-filter — pass more pairs to Claude."""
    try:
        df_15m = bybit.get_candles(pair, timeframe="15m", limit=200)
        close_15m = df_15m["close"]
        price = float(close_15m.iloc[-1])
        reasons = []

        volume = df_15m["volume"]
        vol_current = float(volume.iloc[-1])
        vol_avg = float(volume.iloc[-20:].mean()) if len(volume) >= 20 else float(volume.mean())
        vol_ratio = vol_current / vol_avg if vol_avg > 0 else 0

        delta = close_15m.diff()
        gain = delta.clip(lower=0).ewm(com=13, adjust=False).mean()
        loss = (-delta.clip(upper=0)).ewm(com=13, adjust=False).mean()
        last_gain, last_loss = float(gain.iloc[-1]), float(loss.iloc[-1])
        if last_loss == 0:
            rsi = 100.0
        elif last_gain == 0:
            rsi = 0.0
        else:
            rsi = 100 - (100 / (1 + last_gain / last_loss))

        if vol_ratio > 1.3:
            reasons.append(f"SCALP:vol {vol_ratio:.1f}x")
        if rsi < 35 or rsi > 65:
            reasons.append(f"SCALP:RSI {rsi:.0f}")

        # Mean reversion: 3+ consecutive red candles
        last_3 = close_15m.iloc[-4:-1].values if len(close_15m) > 4 else []
        if len(last_3) == 3:
            opens = df_15m["open"].iloc[-4:-1].values
            reds = sum(1 for i in range(3) if last_3[i] < opens[i])
            if reds >= 3:
                reasons.append("SCALP:3red_reversal")

        # Price broke 1h high/low (4 x 15m candles)
        if len(close_15m) > 4:
            h4 = float(df_15m["high"].iloc[-5:-1].max())
            l4 = float(df_15m["low"].iloc[-5:-1].min())
            if price > h4:
                reasons.append("SCALP:broke_high")
            elif price < l4:
                reasons.append("SCALP:broke_low")

        try:
            df_4h = bybit.get_candles(pair, timeframe="4h", limit=200)
            close_4h = df_4h["close"]
            e20 = float(close_4h.ewm(span=20, adjust=False).mean().iloc[-1])
            e50 = float(close_4h.ewm(span=50, adjust=False).mean().iloc[-1])
            e200 = float(close_4h.ewm(span=200, adjust=False).mean().iloc[-1])
            if e20 > e50 > e200:
                reasons.append("SWING:4h uptrend")
            elif e20 < e50 < e200:
                reasons.append("SWING:4h downtrend")
            if e20 > e200 and abs(price - e50) / e50 < 0.01:
                reasons.append("SWING:pullback_ema50")
        except Exception:
            pass

        if fear_greed_value < 20 or fear_greed_value > 80:
            reasons.append(f"POS:F&G {fear_greed_value}")

        passed = len(reasons) > 0
        reason_str = " | ".join(reasons) if reasons else f"No setup (RSI={rsi:.0f}, Vol={vol_ratio:.1f}x)"
        return passed, reason_str, price, df_15m

    except Exception as e:
        logger.warning(f"Pre-filter failed for {pair}: {e}")
        return False, f"Error: {e}", 0, None


def get_strategy_counts(open_positions):
    counts = {"SCALP": 0, "SWING": 0, "POSITION": 0}
    for pos in open_positions.values():
        strat = pos.get("strategy", "SCALP")
        if strat in counts:
            counts[strat] += 1
    return counts


def _get_all_positions(state):
    """Merge all asset class positions."""
    all_pos = dict(state.open_positions)
    for pos in state._data.get("stock_positions", {}).values():
        all_pos[pos.get("pair", "?")] = pos
    for pos in state._data.get("forex_positions", {}).values():
        all_pos[pos.get("pair", "?")] = pos
    return all_pos


# ── Close handler (shared) ───────────────────────────────────────────────────

def handle_close(result, pair, state, executor, risk_mgr, trade_logger,
                 cfg, logger, positions_key="open_positions",
                 asset_class="crypto", learning_log=None):
    """Handle a closed position — update state, log trade, display."""
    pnl = result["pnl_usdt"]
    state.paper_balance = executor._paper_engine.balance
    if pnl < 0:
        state.consecutive_losses += 1
        if risk_mgr.should_pause_after_loss(state.consecutive_losses):
            pause_until = (datetime.now(timezone.utc)
                           + timedelta(minutes=cfg.loss_streak_pause_minutes))
            state.paused_until = pause_until.isoformat()
            display.print_loss_streak_pause(pause_until.strftime("%H:%M UTC"))
            logger.warning(f"Loss streak pause until {pause_until}")
    else:
        state.consecutive_losses = 0
        state.winning_trades = state.winning_trades + 1
    state.total_trades = state.total_trades + 1
    state._data["daily_trades_today"] = state._data.get("daily_trades_today", 0) + 1
    initial = cfg.paper_initial_balance
    all_pos = {**state._data.get("open_positions", {}),
               **state._data.get("stock_positions", {}),
               **state._data.get("forex_positions", {})}
    locked = sum(p.get("size_usdt", 0) for p in all_pos.values())
    equity = state.paper_balance + locked
    state.daily_pnl_pct = round((equity - initial) / initial * 100, 3)

    positions = state._data.get(positions_key, {})
    pos_data = positions.get(pair, {})
    strategy_type = pos_data.get("strategy", "SCALP")
    entry_price = pos_data.get("entry")
    pos_leverage = pos_data.get("leverage", 1)
    pos_margin = pos_data.get("margin")

    if pair in positions:
        del positions[pair]
    state._data[positions_key] = positions

    trade_logger.log_close(
        pair=pair, exit_price=result["exit"],
        pnl_usdt=pnl, pnl_pct=result["pnl_pct"],
        result=result["result"],
        entry_price=entry_price,
        strategy_type=strategy_type,
        asset_class=asset_class,
        leverage=pos_leverage,
        margin=pos_margin,
    )
    if learning_log:
        learning_log.record_trade(
            pair=pair, strategy=strategy_type,
            side=result.get("side", pos_data.get("side", "unknown")),
            entry_reason=pos_data.get("entry_reason", ""),
            result=result["result"], pnl_pct=result["pnl_pct"],
            entry_price=entry_price or 0, exit_price=result["exit"],
            confidence=pos_data.get("confidence", 0),
            opened_at=pos_data.get("opened_at", ""),
            closed_at=result.get("closed_at", ""),
            btc_trend=state._data.get("last_btc_trend", "UNKNOWN"),
            htf_bias=pos_data.get("htf_bias", "UNKNOWN"),
        )
    display.print_position_closed(pair, pnl, result["result"])
    logger.info(f"[CLOSED] {pair} | P&L: ${pnl:+.2f} ({result['result']}) [{asset_class}]")
    state.save()


# ── Analyze single pair (for parallel execution) ─────────────────────────────

def compute_htf_bias(bybit, pair, logger):
    """Get 1h and 4h trend direction for higher-timeframe confirmation."""
    bias_1h = "UNKNOWN"
    bias_4h = "UNKNOWN"
    try:
        df_1h = bybit.get_candles(pair, timeframe="1h", limit=50)
        close_1h = df_1h["close"]
        ema20_1h = float(close_1h.ewm(span=20, adjust=False).mean().iloc[-1])
        ema50_1h = float(close_1h.ewm(span=50, adjust=False).mean().iloc[-1])
        price = float(close_1h.iloc[-1])
        if price > ema20_1h > ema50_1h:
            bias_1h = "BULL"
        elif price < ema20_1h < ema50_1h:
            bias_1h = "BEAR"
        else:
            bias_1h = "MIXED"
    except Exception as e:
        logger.warning(f"HTF 1h failed for {pair}: {e}")

    try:
        df_4h = bybit.get_candles(pair, timeframe="4h", limit=50)
        close_4h = df_4h["close"]
        ema20_4h = float(close_4h.ewm(span=20, adjust=False).mean().iloc[-1])
        ema50_4h = float(close_4h.ewm(span=50, adjust=False).mean().iloc[-1])
        price = float(close_4h.iloc[-1])
        if price > ema20_4h > ema50_4h:
            bias_4h = "BULL"
        elif price < ema20_4h < ema50_4h:
            bias_4h = "BEAR"
        else:
            bias_4h = "MIXED"
    except Exception as e:
        logger.warning(f"HTF 4h failed for {pair}: {e}")

    # Combined: both must agree
    if bias_1h == bias_4h and bias_1h in ("BULL", "BEAR"):
        return bias_1h
    return "MIXED"


def compute_btc_trend(bybit, logger):
    """Determine BTC macro trend from 4h EMA200."""
    try:
        df = bybit.get_candles("BTC/USDT", timeframe="4h", limit=210)
        close = df["close"]
        price = float(close.iloc[-1])
        ema200 = float(close.ewm(span=200, adjust=False).mean().iloc[-1])
        ema20 = float(close.ewm(span=20, adjust=False).mean().iloc[-1])
        ema50 = float(close.ewm(span=50, adjust=False).mean().iloc[-1])

        # Bull: price above EMA200 AND short-term EMAs rising
        if price > ema200 and ema20 > ema50:
            return "BULL", price, ema200
        # Bear: price below EMA200 OR short-term EMAs falling
        elif price < ema200 or ema20 < ema50:
            return "BEAR", price, ema200
        return "NEUTRAL", price, ema200
    except Exception as e:
        logger.warning(f"BTC trend check failed: {e}")
        return "UNKNOWN", 0, 0


def analyze_single_pair(pair, df_15m, current_price, brain, bybit, coingecko_data,
                        cryptopanic, coinglass, state, cfg, logger,
                        scenario_summary="none", btc_trend="UNKNOWN",
                        htf_bias_override=None, learning_summary="none"):
    """Analyze a single pair — runs in thread pool."""
    try:
        if df_15m is None:
            df_15m = bybit.get_candles(pair, timeframe="15m", limit=200)
            current_price = float(df_15m["close"].iloc[-1])

        indicators = compute_indicators(df_15m)
        smc = detect_smc(df_15m)

        # HTF confirmation per pair
        htf_bias = htf_bias_override or compute_htf_bias(bybit, pair, logger)

        perp_pair = BybitClient.to_perp(pair)
        try:
            ob_summary = bybit.get_orderbook_summary(pair)
            funding_rate = bybit.get_funding_rate(perp_pair)
        except Exception:
            ob_summary = {
                "best_bid": current_price, "best_ask": current_price,
                "bid_wall": {"price": 0, "size": 0},
                "ask_wall": {"price": 0, "size": 0},
                "top_bid_walls": [], "top_ask_walls": [],
                "thin_bid_zones": [], "thin_ask_zones": [],
                "bid_ask_ratio": 1.0, "raw_bids": [], "raw_asks": [],
            }
            funding_rate = 0.0

        base = pair.split("/")[0]
        try:
            news = cryptopanic.get_news(currencies=[base], limit=3)
        except Exception:
            news = []

        current_exposure_pct = sum(
            pos["size_usdt"] / max(state.paper_balance, 1) * 100
            for pos in state.open_positions.values()
        )

        context = {
            "pair": pair, "price": current_price,
            "asset_class": "crypto",
            "indicators": indicators, "smc": smc,
            "orderbook": ob_summary,
            "funding_rate": funding_rate,
            "open_interest": None,
            "fear_greed": coingecko_data["fear_greed"],
            "btc_dominance": coingecko_data["btc_dominance"],
            "news": news,
            "liquidation_levels": [],
            "liquidity_analysis": {"liquidity_sweeps": [], "stop_hunt_zones": [],
                                   "thin_bid_zones": [], "thin_ask_zones": [],
                                   "top_bid_walls": [], "top_ask_walls": []},
            "open_positions": state.open_positions,
            "remaining_exposure_pct": cfg.max_total_exposure_pct - current_exposure_pct,
            "multi_timeframe": {"tf_15m": indicators},
            "scenario_summary": scenario_summary,
            "btc_trend": btc_trend,
            "htf_bias": htf_bias,
            "learning_summary": learning_summary,
        }

        signal = brain.analyze(context)

        # Force SCALP only
        signal.strategy = "SCALP"

        # Attach trend info for risk manager
        signal._btc_trend = btc_trend
        signal._htf_bias = htf_bias

        return pair, signal, indicators, smc, current_price

    except Exception as e:
        logger.error(f"Analyze failed for {pair}: {e}")
        return pair, None, None, None, current_price


# ── Crypto cycle ─────────────────────────────────────────────────────────────

def run_crypto_cycle(cfg, bybit, coingecko, cryptopanic, coinglass,
                     brain, risk_mgr, executor, state, trade_logger, logger,
                     scenario_engine=None, learning_log=None):
    state.reset_daily_if_new_day()
    display.print_cycle_header(state.paper_balance, state.daily_pnl_pct, cfg.paper_trading)
    logger.info(f"CRYPTO CYCLE START — {datetime.now(timezone.utc).strftime('%H:%M UTC')}")

    try:
        fear_greed = coingecko.get_fear_greed()
        btc_dominance = coingecko.get_btc_dominance()
    except Exception as e:
        logger.warning(f"CoinGecko failed: {e}")
        fear_greed = {"value": 50, "label": "Neutral", "trend": "flat"}
        btc_dominance = 50.0

    fg_value = fear_greed.get("value", 50)
    if isinstance(fg_value, str):
        try:
            fg_value = int(fg_value)
        except ValueError:
            fg_value = 50

    coingecko_data = {"fear_greed": fear_greed, "btc_dominance": btc_dominance}

    # Check SL/TP on open crypto positions
    for pair in list(state.open_positions.keys()):
        try:
            df = bybit.get_candles(pair, timeframe="1m", limit=1)
            current_price = float(df["close"].iloc[-1])
            result = executor.check_exits(pair, current_price)
            if result and result.get("status") == "closed":
                handle_close(result, pair, state, executor, risk_mgr,
                             trade_logger, cfg, logger,
                             positions_key="open_positions",
                             asset_class="crypto",
                             learning_log=learning_log)
        except Exception as e:
            logger.error(f"Exit check failed for {pair}: {e}", exc_info=True)
            display.print_error(pair, e)

    # Time-based stops
    check_time_stops(state, executor, bybit, trade_logger, cfg, risk_mgr, logger, learning_log)

    # Orphan cleanup: close positions open >48h
    if hasattr(executor, '_paper_engine') and executor._paper_engine:
        for orphan_pair in executor._paper_engine.check_orphans(max_age_hours=48):
            if orphan_pair in state.open_positions:
                logger.warning(f"ORPHAN CLEANUP: {orphan_pair} open >48h, closing at market")
                try:
                    df = bybit.get_candles(orphan_pair, timeframe="1m", limit=1)
                    price = float(df["close"].iloc[-1])
                    result = executor._paper_engine.close_position(orphan_pair, price)
                    if result and result.get("status") == "closed":
                        handle_close(result, orphan_pair, state, executor, risk_mgr,
                                     trade_logger, cfg, logger,
                                     positions_key="open_positions", asset_class="crypto")
                except Exception as e:
                    logger.error(f"Orphan close failed for {orphan_pair}: {e}")

    # Margin health check
    margin_close_pair = executor.check_margin_health()
    if margin_close_pair:
        logger.warning(f"MARGIN HEALTH: closing smallest position {margin_close_pair}")
        try:
            df = bybit.get_candles(margin_close_pair, timeframe="1m", limit=1)
            price = float(df["close"].iloc[-1])
            result = executor._paper_engine.close_position(margin_close_pair, price)
            if result.get("status") == "closed":
                handle_close(result, margin_close_pair, state, executor, risk_mgr,
                             trade_logger, cfg, logger,
                             positions_key="open_positions", asset_class="crypto")
        except Exception as e:
            logger.error(f"Margin health close failed: {e}")

    # Pre-filter
    filtered_pairs = []
    all_prefilter_results = []
    for pair in cfg.trading_pairs:
        passed, reason, price, df_15m = pre_filter(bybit, pair, fg_value, logger)
        all_prefilter_results.append((pair, passed, reason, price))
        if passed:
            filtered_pairs.append((pair, df_15m, price))
        elif pair in state.open_positions:
            filtered_pairs.append((pair, df_15m, price))
        status = "✓" if passed else "✗"
        logger.info(f"[PRE-FILTER] {pair}: {status} — {reason}")

    display.print_prefilter_summary(all_prefilter_results, len(cfg.trading_pairs))

    strategy_counts = get_strategy_counts(state.open_positions)
    cycle_results = []

    # Adaptive confidence
    hour_utc = datetime.now(timezone.utc).hour
    daily_trades = state._data.get("daily_trades_today", 0)
    conf_adj = risk_mgr.compute_confidence_adjustment(
        daily_trades, cfg.daily_trade_target, hour_utc)
    state._data["confidence_adjustment"] = conf_adj
    if conf_adj > 0:
        logger.info(f"Confidence adjustment: -{conf_adj}pts (trades today: {daily_trades}/{cfg.daily_trade_target})")

    # FIX 1: BTC macro trend filter
    btc_trend, btc_price_now, btc_ema200 = compute_btc_trend(bybit, logger)
    state._data["last_btc_trend"] = btc_trend
    logger.info(f"BTC TREND: {btc_trend} | Price: ${btc_price_now:,.0f} | EMA200: ${btc_ema200:,.0f}")
    if btc_trend == "BEAR":
        logger.info("⚠ BTC BEAR — only SHORT signals allowed")
    elif btc_trend == "BULL":
        logger.info("✓ BTC BULL — only LONG signals allowed")

    # MiroFish scenario simulation
    scenario_summary = "none"
    if scenario_engine:
        fg_val_now = coingecko_data["fear_greed"].get("value", 50)
        if isinstance(fg_val_now, str):
            try:
                fg_val_now = int(fg_val_now)
            except ValueError:
                fg_val_now = 50
        fg_24h_change = state._data.get("fg_24h_change", 0)
        # Get BTC price from filtered pairs or state
        btc_price = 0
        for p, df, px in filtered_pairs:
            if p == "BTC/USDT":
                btc_price = px
                break
        if scenario_engine.should_run(fg_val_now, fg_24h_change, hour_utc=hour_utc):
            try:
                predictions = scenario_engine.run_macro_scenarios(
                    fear_greed=fg_val_now, btc_price=btc_price,
                    fg_24h_change=fg_24h_change,
                )
                if predictions:
                    logger.info(f"MIROFISH: {len(predictions)} scenarios simulated")
                    for pred in predictions:
                        logger.info(f"  → {pred.direction} {pred.confidence}% [{pred.timeframe}]")
            except Exception as e:
                logger.warning(f"MiroFish error: {e}")
        scenario_summary = scenario_engine.get_compact_summary()
        # Track F&G for 24h change detection
        prev_fg = state._data.get("last_fg_value", fg_val_now)
        state._data["fg_24h_change"] = fg_val_now - prev_fg
        state._data["last_fg_value"] = fg_val_now

    # Parallel analysis — max 5 Claude API calls at a time
    analysis_results = []
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {}
        for pair, df_15m, current_price in filtered_pairs:
            f = pool.submit(
                analyze_single_pair,
                pair, df_15m, current_price, brain, bybit, coingecko_data,
                cryptopanic, coinglass, state, cfg, logger,
                scenario_summary=scenario_summary,
                btc_trend=btc_trend,
                learning_summary=learning_log.get_pattern_summary(20) if learning_log else "none",
            )
            futures[f] = pair

        for future in as_completed(futures):
            try:
                result = future.result(timeout=60)
                if result:
                    analysis_results.append(result)
            except Exception as e:
                pair = futures[future]
                logger.error(f"Analysis timeout/error for {pair}: {e}")
                cycle_results.append((pair, "ERR", None, "NONE"))

    # Process results sequentially (state mutations)
    total_open = len(state.open_positions) + len(state._data.get("stock_positions", {})) + len(state._data.get("forex_positions", {}))
    for pair, signal, indicators, smc, current_price in analysis_results:
        if signal is None:
            cycle_results.append((pair, "ERR", None, "NONE"))
            continue

        strategy = getattr(signal, "strategy", "NONE")
        logger.info(
            f"{pair} | [{strategy}] {signal.decision} {signal.confidence}/100"
        )

        trade_note = ""
        block_reason = ""

        if signal.decision == "HOLD":
            pass  # no logging for HOLD
        elif pair in state.open_positions:
            block_reason = "already in position"
        else:
            can_trade, block_reason = risk_mgr.can_trade(
                signal=signal,
                daily_pnl_pct=state.daily_pnl_pct,
                consecutive_losses=state.consecutive_losses,
                paused_until=state.paused_until,
                current_exposure_pct=sum(
                    pos["size_usdt"] / max(state.paper_balance, 1) * 100
                    for pos in state.open_positions.values()
                ),
                strategy_counts=strategy_counts,
                confidence_adjustment=conf_adj,
                total_open_positions=total_open,
                btc_trend=btc_trend,
            )
            is_partial = False
            if not can_trade and "score" in block_reason.lower():
                is_partial = risk_mgr.is_partial_candidate(signal, conf_adj)
                if is_partial:
                    can_trade = True
                    block_reason = ""
            if can_trade:
                margin = risk_mgr.calculate_position_size(
                    state.paper_balance, signal.confidence, strategy,
                    partial=is_partial)
                leverage = compute_leverage(signal.confidence, strategy) if cfg.futures_enabled else 1
                result = executor.execute(signal=signal, size_usdt=margin,
                                          leverage=leverage, strategy=strategy)
                if result.get("status") == "filled":
                    notional = result.get("size_usdt", margin)
                    partial_tag = " (PARTIAL)" if is_partial else ""
                    state.open_positions[pair] = {
                        "side": "long" if signal.decision == "BUY" else "short",
                        "entry": signal.entry,
                        "size_usdt": notional,
                        "margin": margin,
                        "leverage": leverage,
                        "stop_loss": signal.stop_loss,
                        "take_profit": signal.take_profit,
                        "opened_at": datetime.now(timezone.utc).isoformat(),
                        "confidence": signal.confidence,
                        "strategy": strategy,
                        "asset_class": "crypto",
                        "liquidation_price": result.get("liquidation_price"),
                        "is_partial": is_partial,
                    }
                    state.paper_balance = executor._paper_engine.balance
                    state._data["daily_trades_today"] = state._data.get("daily_trades_today", 0) + 1
                    trade_logger.log_entry(signal=signal, size_usdt=notional,
                                           asset_class="crypto",
                                           leverage=leverage, margin=margin)
                    lev_str = f" {leverage}x" if leverage > 1 else ""
                    trade_note = (
                        f"PAPER {strategy}{lev_str}{partial_tag} "
                        f"{'LONG' if signal.decision == 'BUY' else 'SHORT'} "
                        f"@ ${signal.entry:,.2f} | margin ${margin:.0f} → ${notional:.0f}"
                    )
                    strategy_counts[strategy] = strategy_counts.get(strategy, 0) + 1
                    total_open += 1
                    state.save()
                else:
                    block_reason = result.get("reason", "rejected")
            else:
                trade_logger.log_blocked(pair, signal, block_reason,
                                         asset_class="crypto")

        if indicators:
            display.print_pair_block(
                pair=pair, price=current_price, indicators=indicators,
                smc=smc, fear_greed=fear_greed,
                liquidity_analysis={"liquidity_sweeps": [], "stop_hunt_zones": [],
                                    "thin_bid_zones": [], "thin_ask_zones": [],
                                    "top_bid_walls": [], "top_ask_walls": []},
                signal=signal, trade_note=trade_note,
                block_reason=block_reason,
            )
        conf = signal.confidence if signal.decision != "HOLD" else None
        cycle_results.append((pair, signal.decision, conf, strategy))

    for pair, passed, reason, price in all_prefilter_results:
        if not passed and pair not in state.open_positions:
            cycle_results.append((pair, "SKIP", None, "NONE"))

    next_interval = get_crypto_interval_minutes()
    next_run = (datetime.now(timezone.utc)
                + timedelta(minutes=next_interval)).strftime("%H:%M")
    final_counts = get_strategy_counts(state.open_positions)
    display.print_cycle_summary(
        results=cycle_results, balance=state.paper_balance,
        daily_pnl=state.daily_pnl_pct, next_run_time=next_run,
        strategy_counts=final_counts,
    )
    logger.info(
        f"CRYPTO CYCLE END | Balance: ${state.paper_balance:,.2f} | "
        f"Filtered: {len(filtered_pairs)}/{len(cfg.trading_pairs)} | "
        f"Next: {next_interval}min"
    )


# ── Dynamic intervals ────────────────────────────────────────────────────────

def get_crypto_interval_minutes():
    hour_utc = datetime.now(timezone.utc).hour
    if 13 <= hour_utc <= 21:      # NY session
        return 5
    elif 7 <= hour_utc <= 13:     # London session
        return 10
    else:                          # Asia session
        return 15


# ── Stock cycle ──────────────────────────────────────────────────────────────

def run_stock_cycle(cfg, stock_client, brain, risk_mgr, executor,
                    state, trade_logger, logger):
    if not StockClient.is_market_open():
        logger.info("STOCK CYCLE SKIP — market closed")
        return

    logger.info(f"STOCK CYCLE START — {datetime.now(timezone.utc).strftime('%H:%M UTC')}")
    print(f"\n{'─'*56}")
    print(f" STOCK SCAN — {datetime.now(timezone.utc).strftime('%H:%M UTC')}")
    print(f"{'─'*56}")

    stock_positions = state._data.setdefault("stock_positions", {})

    for sym in list(stock_positions.keys()):
        try:
            df = stock_client.get_candles(sym, period="5d", interval="1h")
            price = float(df["close"].iloc[-1])
            result = executor.check_exits(sym, price)
            if result and result.get("status") == "closed":
                handle_close(result, sym, state, executor, risk_mgr,
                             trade_logger, cfg, logger,
                             positions_key="stock_positions",
                             asset_class="stock")
        except Exception as e:
            logger.warning(f"Stock exit check failed {sym}: {e}")

    for sym in cfg.stock_symbols:
        try:
            df = stock_client.get_candles(sym, period="6mo", interval="1d")
            price = float(df["close"].iloc[-1])
            indicators = compute_indicators(df)
            smc = detect_smc(df)
            fundamentals = stock_client.get_fundamentals(sym)

            context = {
                "pair": sym, "price": price,
                "asset_class": "stock",
                "indicators": indicators, "smc": smc,
                "orderbook": {
                    "best_bid": price, "best_ask": price,
                    "bid_wall": {"price": 0, "size": 0},
                    "ask_wall": {"price": 0, "size": 0},
                    "bid_ask_ratio": 1.0,
                    "top_bid_walls": [], "top_ask_walls": [],
                    "thin_bid_zones": [], "thin_ask_zones": [],
                },
                "funding_rate": 0.0, "open_interest": 0,
                "fear_greed": {"value": 50, "label": "Neutral", "trend": "flat"},
                "btc_dominance": 0,
                "news": [], "liquidation_levels": [],
                "liquidity_analysis": {"liquidity_sweeps": [], "stop_hunt_zones": [],
                                       "thin_bid_zones": [], "thin_ask_zones": [],
                                       "top_bid_walls": [], "top_ask_walls": []},
                "open_positions": stock_positions,
                "remaining_exposure_pct": cfg.max_total_exposure_pct,
                "multi_timeframe": {"tf_15m": indicators},
            }

            signal = brain.analyze(context)
            strategy = getattr(signal, "strategy", "NONE")
            logger.info(f"STOCK {sym} | [{strategy}] {signal.decision} {signal.confidence}/100")

            if signal.decision != "HOLD" and sym not in stock_positions:
                total_open = len(state.open_positions) + len(stock_positions) + len(state._data.get("forex_positions", {}))
                can_trade, reason = risk_mgr.can_trade(
                    signal=signal, daily_pnl_pct=state.daily_pnl_pct,
                    consecutive_losses=state.consecutive_losses,
                    paused_until=state.paused_until,
                    current_exposure_pct=0,
                    total_open_positions=total_open,
                )
                if can_trade:
                    margin = risk_mgr.calculate_position_size(
                        state.paper_balance, signal.confidence, strategy)
                    leverage = compute_leverage(signal.confidence, strategy) if cfg.futures_enabled else 1
                    result = executor.execute(signal=signal, size_usdt=margin,
                                              leverage=leverage, strategy=strategy)
                    if result.get("status") == "filled":
                        notional = result.get("size_usdt", margin)
                        stock_positions[sym] = {
                            "pair": sym,
                            "side": "long" if signal.decision == "BUY" else "short",
                            "entry": signal.entry,
                            "size_usdt": notional,
                            "margin": margin,
                            "leverage": leverage,
                            "stop_loss": signal.stop_loss,
                            "take_profit": signal.take_profit,
                            "opened_at": datetime.now(timezone.utc).isoformat(),
                            "confidence": signal.confidence,
                            "strategy": strategy,
                            "asset_class": "stock",
                            "liquidation_price": result.get("liquidation_price"),
                        }
                        state.paper_balance = executor._paper_engine.balance
                        trade_logger.log_entry(signal=signal, size_usdt=notional,
                                               asset_class="stock",
                                               leverage=leverage, margin=margin)
                        lev_str = f" {leverage}x" if leverage > 1 else ""
                        print(f"  {sym}: {strategy}{lev_str} {signal.decision} @ ${signal.entry:,.2f} | margin ${margin:.0f} → ${notional:.0f}")
                        state.save()
                    else:
                        logger.info(f"  {sym}: Rejected — {result.get('reason')}")
                else:
                    logger.info(f"  {sym}: Blocked — {reason}")
            else:
                print(f"  {sym}: ${price:,.2f} — HOLD (score {signal.confidence})")

        except Exception as e:
            logger.warning(f"Stock error {sym}: {e}")
            print(f"  {sym}: ERROR — {str(e)[:50]}")

    print(f"{'─'*56}")
    logger.info("STOCK CYCLE END")


# ── Forex cycle ──────────────────────────────────────────────────────────────

def run_forex_cycle(cfg, forex_client, brain, risk_mgr, executor,
                    state, trade_logger, logger):
    if not ForexClient.is_market_open():
        logger.info("FOREX CYCLE SKIP — market closed")
        return

    logger.info(f"FOREX CYCLE START — {datetime.now(timezone.utc).strftime('%H:%M UTC')}")
    print(f"\n{'─'*56}")
    print(f" FOREX SCAN — {datetime.now(timezone.utc).strftime('%H:%M UTC')}")
    print(f"{'─'*56}")

    forex_positions = state._data.setdefault("forex_positions", {})

    for pair_key in list(forex_positions.keys()):
        try:
            yf_pair = forex_positions[pair_key].get("yf_pair", pair_key)
            df = forex_client.get_candles(yf_pair, period="5d", interval="1h")
            price = float(df["close"].iloc[-1])
            result = executor.check_exits(pair_key, price)
            if result and result.get("status") == "closed":
                handle_close(result, pair_key, state, executor, risk_mgr,
                             trade_logger, cfg, logger,
                             positions_key="forex_positions",
                             asset_class="forex")
        except Exception as e:
            logger.warning(f"Forex exit check failed {pair_key}: {e}")

    for yf_pair in cfg.forex_pairs:
        display_pair = ForexClient.pair_to_display(yf_pair)
        try:
            df = forex_client.get_candles(yf_pair, period="60d", interval="1h")
            price = float(df["close"].iloc[-1])
            indicators = compute_indicators(df)
            smc = detect_smc(df)
            rate_info = ForexClient.get_rate_differential(yf_pair)

            context = {
                "pair": display_pair, "price": price,
                "asset_class": "forex",
                "indicators": indicators, "smc": smc,
                "orderbook": {
                    "best_bid": price, "best_ask": price,
                    "bid_wall": {"price": 0, "size": 0},
                    "ask_wall": {"price": 0, "size": 0},
                    "bid_ask_ratio": 1.0,
                    "top_bid_walls": [], "top_ask_walls": [],
                    "thin_bid_zones": [], "thin_ask_zones": [],
                },
                "funding_rate": 0.0, "open_interest": 0,
                "fear_greed": {"value": 50, "label": "Neutral", "trend": "flat"},
                "btc_dominance": 0,
                "news": [], "liquidation_levels": [],
                "liquidity_analysis": {"liquidity_sweeps": [], "stop_hunt_zones": [],
                                       "thin_bid_zones": [], "thin_ask_zones": [],
                                       "top_bid_walls": [], "top_ask_walls": []},
                "open_positions": forex_positions,
                "remaining_exposure_pct": cfg.max_total_exposure_pct,
                "multi_timeframe": {"tf_15m": indicators},
            }

            signal = brain.analyze(context)
            strategy = getattr(signal, "strategy", "NONE")
            logger.info(f"FOREX {display_pair} | [{strategy}] {signal.decision} {signal.confidence}/100")

            if signal.decision != "HOLD" and display_pair not in forex_positions:
                total_open = len(state.open_positions) + len(state._data.get("stock_positions", {})) + len(forex_positions)
                can_trade, reason = risk_mgr.can_trade(
                    signal=signal, daily_pnl_pct=state.daily_pnl_pct,
                    consecutive_losses=state.consecutive_losses,
                    paused_until=state.paused_until,
                    current_exposure_pct=0,
                    total_open_positions=total_open,
                )
                if can_trade:
                    margin = risk_mgr.calculate_position_size(
                        state.paper_balance, signal.confidence, strategy)
                    leverage = compute_leverage(signal.confidence, strategy) if cfg.futures_enabled else 1
                    result = executor.execute(signal=signal, size_usdt=margin,
                                              leverage=leverage, strategy=strategy)
                    if result.get("status") == "filled":
                        notional = result.get("size_usdt", margin)
                        forex_positions[display_pair] = {
                            "pair": display_pair,
                            "yf_pair": yf_pair,
                            "side": "long" if signal.decision == "BUY" else "short",
                            "entry": signal.entry,
                            "size_usdt": notional,
                            "margin": margin,
                            "leverage": leverage,
                            "stop_loss": signal.stop_loss,
                            "take_profit": signal.take_profit,
                            "opened_at": datetime.now(timezone.utc).isoformat(),
                            "confidence": signal.confidence,
                            "strategy": strategy,
                            "asset_class": "forex",
                            "liquidation_price": result.get("liquidation_price"),
                        }
                        state.paper_balance = executor._paper_engine.balance
                        trade_logger.log_entry(signal=signal, size_usdt=notional,
                                               asset_class="forex",
                                               leverage=leverage, margin=margin)
                        lev_str = f" {leverage}x" if leverage > 1 else ""
                        print(f"  {display_pair}: {strategy}{lev_str} {signal.decision} @ {price:.5f} | margin ${margin:.0f} → ${notional:.0f}")
                        state.save()
                else:
                    logger.info(f"  {display_pair}: Blocked — {reason}")
            else:
                print(f"  {display_pair}: {price:.5f} — HOLD (score {signal.confidence})")

        except Exception as e:
            logger.warning(f"Forex error {yf_pair}: {e}")
            print(f"  {display_pair}: ERROR — {str(e)[:50]}")

    print(f"{'─'*56}")
    logger.info("FOREX CYCLE END")


# ── Weekly backtest ───────────────────────────────────────────────────────────

def maybe_run_weekly_backtest(cfg, bybit, logger, state):
    """Run backtest every Sunday if not already run this week."""
    now = datetime.now(timezone.utc)
    if now.weekday() != 6:  # 6 = Sunday
        return
    last_backtest = state._data.get("last_weekly_backtest", "")
    today = now.strftime("%Y-%m-%d")
    if last_backtest == today:
        return

    logger.info("WEEKLY BACKTEST: Running Sunday strategy review...")
    try:
        from backtest import Backtester
        from dataclasses import asdict

        df = bybit.get_candles("BTC/USDT", timeframe="15m", limit=200)
        bt = Backtester(initial_balance=cfg.paper_initial_balance)
        results = bt.run_all_strategies(df)

        # Save results
        output_path = os.path.join(cfg.log_dir, "backtest_results.json")
        prev_path = os.path.join(cfg.log_dir, "backtest_previous.json")
        if os.path.exists(output_path):
            os.replace(output_path, prev_path)

        combined = {
            "run_at": now.isoformat(),
            "pair": "BTC/USDT",
            "days": "~3 (200 bars)",
            "bars": len(df),
            "strategies": {k: asdict(v) for k, v in results.items()},
            "best_strategy": max(results, key=lambda k: results[k].win_rate),
        }
        with open(output_path, "w") as f:
            json.dump(combined, f, indent=2, default=str)

        for strat, result in results.items():
            logger.info(f"  BACKTEST {result.summary()}")

        # Compare to previous week
        if os.path.exists(prev_path):
            with open(prev_path) as f:
                prev = json.load(f)
            for strat in results:
                prev_wr = prev.get("strategies", {}).get(strat, {}).get("win_rate", 0)
                curr_wr = results[strat].win_rate
                diff = curr_wr - prev_wr
                logger.info(f"  {strat} WR change: {diff:+.1f}% vs last week")

        state._data["last_weekly_backtest"] = today
        state.save()
    except Exception as e:
        logger.error(f"Weekly backtest failed: {e}")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    cfg = Config()
    logger = get_logger(
        "agent", log_dir=cfg.log_dir, log_to_file=cfg.log_to_file,
        level=cfg.log_level, console=False,
    )

    bybit = BybitClient(api_key=cfg.bybit_api_key, secret=cfg.bybit_secret,
                        testnet=cfg.bybit_testnet)
    coingecko = CoinGeckoClient()
    cryptopanic = CryptoPanicClient(api_key=cfg.cryptopanic_api_key)
    coinglass = CoinglassClient()
    stock_client = StockClient(symbols=cfg.stock_symbols)
    forex_client = ForexClient(pairs=cfg.forex_pairs)
    brain = ClaudeBrain(api_key=cfg.anthropic_api_key, model=cfg.claude_model)
    risk_mgr = RiskManager(cfg)
    state = State(path="state.json", initial_balance=cfg.paper_initial_balance)
    trade_logger = TradeLogger(log_dir=cfg.log_dir)
    learning_log = LearningLog(log_dir=cfg.log_dir)

    state._data.setdefault("stock_positions", {})
    state._data.setdefault("forex_positions", {})

    paper_engine = PaperEngine(
        initial_balance=state.paper_balance,
        futures_enabled=cfg.futures_enabled,
        max_margin_per_trade_pct=cfg.max_margin_per_trade_pct,
        free_margin_buffer_pct=cfg.free_margin_buffer_pct,
        emergency_close_loss_pct=cfg.emergency_close_loss_pct,
    ) if cfg.paper_trading else None
    if paper_engine:
        for positions in [state.open_positions,
                          state._data.get("stock_positions", {}),
                          state._data.get("forex_positions", {})]:
            for pair, pos in positions.items():
                if pair not in paper_engine.positions:
                    leverage = pos.get("leverage", 1)
                    margin = pos.get("margin", pos["size_usdt"])
                    paper_engine.positions[pair] = {
                        "side": "buy" if pos["side"] == "long" else "sell",
                        "entry": pos["entry"],
                        "size_usdt": pos["size_usdt"],
                        "margin": margin,
                        "leverage": leverage,
                        "stop_loss": pos["stop_loss"],
                        "take_profit": pos["take_profit"],
                        "confidence": pos.get("confidence", 0),
                        "strategy": pos.get("strategy", "SCALP"),
                        "liquidation_price": pos.get("liquidation_price"),
                        "opened_at": pos.get("opened_at", ""),
                    }

    executor = Executor(
        paper_trading=cfg.paper_trading,
        paper_engine=paper_engine,
        bybit_client=None if cfg.paper_trading else bybit,
    )

    if not cfg.paper_trading:
        try:
            balance = bybit.get_balance()
            state.paper_balance = balance["USDT"]
        except Exception as e:
            logger.error(f"Failed to fetch live balance: {e}")

    scenario_engine = ScenarioEngine()

    display.print_startup(cfg, state.paper_balance)

    crypto_args = (cfg, bybit, coingecko, cryptopanic, coinglass,
                   brain, risk_mgr, executor, state, trade_logger, logger,
                   scenario_engine, learning_log)
    stock_args = (cfg, stock_client, brain, risk_mgr, executor,
                  state, trade_logger, logger)
    forex_args = (cfg, forex_client, brain, risk_mgr, executor,
                  state, trade_logger, logger)

    # Run initial cycles
    run_crypto_cycle(*crypto_args)
    run_stock_cycle(*stock_args)
    run_forex_cycle(*forex_args)

    # Check weekly backtest on startup
    maybe_run_weekly_backtest(cfg, bybit, logger, state)

    # Dynamic interval loop
    last_crypto = time.time()
    last_stock = time.time()
    last_forex = time.time()

    while True:
        now = time.time()
        crypto_interval = get_crypto_interval_minutes() * 60

        if now - last_crypto >= crypto_interval:
            try:
                run_crypto_cycle(*crypto_args)
            except Exception as e:
                logger.error(f"Crypto cycle error: {e}", exc_info=True)
            last_crypto = time.time()

        if now - last_stock >= cfg.stock_scan_interval_minutes * 60:
            try:
                run_stock_cycle(*stock_args)
            except Exception as e:
                logger.error(f"Stock cycle error: {e}", exc_info=True)
            last_stock = time.time()

        if now - last_forex >= cfg.forex_scan_interval_minutes * 60:
            try:
                run_forex_cycle(*forex_args)
            except Exception as e:
                logger.error(f"Forex cycle error: {e}", exc_info=True)
            last_forex = time.time()

        # End-of-day performance log
        hour_utc = datetime.now(timezone.utc).hour
        if hour_utc == 23 and datetime.now(timezone.utc).minute < 1:
            daily_trades = state._data.get("daily_trades_today", 0)
            logger.info(
                f"EOD REPORT: trades={daily_trades}/{cfg.daily_trade_target} | "
                f"P&L={state.daily_pnl_pct:.3f}% (target {cfg.daily_profit_target_pct}%)"
            )

        time.sleep(10)


if __name__ == "__main__":
    main()
