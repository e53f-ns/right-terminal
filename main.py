#!/usr/bin/env python3
"""
CLAUDE TRADING AGENT v1.0
Autonomous crypto trading powered by Claude AI + Smart Money Concepts
"""

import time
import schedule
from datetime import datetime, timezone, timedelta

from config import Config
from data.bybit import BybitClient
from data.coingecko import CoinGeckoClient
from data.cryptopanic import CryptoPanicClient
from data.coinglass import CoinglassClient
from analysis.indicators import compute_indicators
from analysis.smc import detect_smc
from analysis.claude_brain import ClaudeBrain
from risk.manager import RiskManager
from trading.paper_engine import PaperEngine
from trading.executor import Executor
from utils.logger import get_logger
from utils.state import State
from utils.trade_logger import TradeLogger


def run_cycle(cfg, bybit, coingecko, cryptopanic, coinglass,
              brain, risk_mgr, executor, state, trade_logger, logger):
    state.reset_daily_if_new_day()
    logger.info("=" * 60)
    logger.info(f"CYCLE START — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    logger.info(f"Mode: {'PAPER' if cfg.paper_trading else 'LIVE'} | Balance: ${state.paper_balance:,.2f}")

    # ── Fetch macro context once per cycle ──────────────────────
    try:
        fear_greed = coingecko.get_fear_greed()
        btc_dominance = coingecko.get_btc_dominance()
    except Exception as e:
        logger.warning(f"CoinGecko fetch failed: {e}")
        fear_greed = {"value": 50, "label": "Neutral", "trend": "flat"}
        btc_dominance = 50.0

    # ── Check open positions for SL/TP hits ─────────────────────
    for pair in list(state.open_positions.keys()):
        try:
            df = bybit.get_candles(pair, timeframe="1m", limit=1)
            current_price = float(df["close"].iloc[-1])
            result = executor.check_exits(pair, current_price)
            if result and result.get("status") == "closed":
                pnl = result["pnl_usdt"]
                state.paper_balance = executor._paper_engine.balance
                if pnl < 0:
                    state.consecutive_losses += 1
                    if risk_mgr.should_pause_after_loss(state.consecutive_losses):
                        pause_until = (datetime.now(timezone.utc)
                                       + timedelta(minutes=cfg.loss_streak_pause_minutes))
                        state.paused_until = pause_until.isoformat()
                        logger.warning(
                            f"Loss streak pause: {cfg.loss_streak_pause_trades} losses. "
                            f"Pausing until {pause_until}"
                        )
                else:
                    state.consecutive_losses = 0
                    state.winning_trades = state.winning_trades + 1
                state.total_trades = state.total_trades + 1
                # Recalculate daily P&L
                initial = cfg.paper_initial_balance
                state.daily_pnl_pct = round((state.paper_balance - initial) / initial * 100, 3)
                del state.open_positions[pair]
                trade_logger.log_close(
                    pair=pair, exit_price=result["exit"],
                    pnl_usdt=pnl, pnl_pct=result["pnl_pct"],
                    result=result["result"],
                )
                logger.info(f"[CLOSED] {pair} | P&L: ${pnl:+.2f} ({result['result']})")
                state.save()
        except Exception as e:
            logger.error(f"Exit check failed for {pair}: {e}", exc_info=True)

    # ── Analyze each pair ────────────────────────────────────────
    for pair in cfg.trading_pairs:
        logger.info(f"\n── {pair} ──")
        try:
            df = bybit.get_candles(pair, timeframe="15m", limit=200)
            current_price = float(df["close"].iloc[-1])
            logger.info(f"Price: ${current_price:,.2f}")

            indicators = compute_indicators(df)
            smc = detect_smc(df)

            try:
                ob_summary = bybit.get_orderbook_summary(pair)
                funding_rate = bybit.get_funding_rate(pair)
                open_interest = bybit.get_open_interest(pair)
            except Exception as e:
                logger.warning(f"Orderbook/funding fetch failed: {e}")
                ob_summary = {
                    "best_bid": current_price, "best_ask": current_price,
                    "bid_wall": {"price": 0, "size": 0},
                    "ask_wall": {"price": 0, "size": 0},
                    "bid_ask_ratio": 1.0,
                }
                funding_rate = 0.0
                open_interest = None

            base = pair.split("/")[0]
            try:
                news = cryptopanic.get_news(currencies=[base], limit=5)
            except Exception:
                news = []

            try:
                liq_levels = coinglass.get_liquidation_levels(base)
            except Exception:
                liq_levels = []

            current_exposure_pct = sum(
                pos["size_usdt"] / state.paper_balance * 100
                for pos in state.open_positions.values()
            )

            context = {
                "pair": pair,
                "price": current_price,
                "indicators": indicators,
                "smc": smc,
                "orderbook": ob_summary,
                "funding_rate": funding_rate,
                "open_interest": open_interest,
                "fear_greed": fear_greed,
                "btc_dominance": btc_dominance,
                "news": news,
                "liquidation_levels": liq_levels,
                "open_positions": state.open_positions,
                "remaining_exposure_pct": cfg.max_total_exposure_pct - current_exposure_pct,
            }

            logger.info(
                f"RSI: {indicators['rsi']} | "
                f"EMA20/50/200: {indicators['ema20']:.0f}/{indicators['ema50']:.0f}/{indicators['ema200']:.0f}"
            )
            logger.info(
                f"SMC: BOS={smc['bos_detected']} CHOCH={smc['choch_detected']} | "
                f"F&G: {fear_greed['value']} ({fear_greed['label']})"
            )

            signal = brain.analyze(context)

            logger.info(f"[CLAUDE] Bias: {signal.bias} | Retail Trap: {signal.retail_trap}")
            logger.info(f"[CLAUDE] Decision: {signal.decision} | Confidence: {signal.confidence}%")
            logger.info(f"[CLAUDE] {signal.reasoning[:200]}")

            if signal.decision == "HOLD":
                trade_logger.log_hold(pair, signal)
                continue

            if pair in state.open_positions:
                logger.info(f"[SKIP] Already in position for {pair}")
                continue

            can_trade, block_reason = risk_mgr.can_trade(
                signal=signal,
                daily_pnl_pct=state.daily_pnl_pct,
                consecutive_losses=state.consecutive_losses,
                paused_until=state.paused_until,
                current_exposure_pct=current_exposure_pct,
            )

            if not can_trade:
                logger.info(f"[BLOCKED] {block_reason}")
                trade_logger.log_blocked(pair, signal, block_reason)
                continue

            size_usdt = risk_mgr.calculate_position_size(
                balance=state.paper_balance, confidence=signal.confidence
            )
            result = executor.execute(signal=signal, size_usdt=size_usdt)

            if result.get("status") == "filled":
                state.open_positions[pair] = {
                    "side": "long" if signal.decision == "BUY" else "short",
                    "entry": signal.entry,
                    "size_usdt": size_usdt,
                    "stop_loss": signal.stop_loss,
                    "take_profit": signal.take_profit,
                    "opened_at": datetime.now(timezone.utc).isoformat(),
                    "confidence": signal.confidence,
                }
                state.paper_balance = executor._paper_engine.balance
                trade_logger.log_entry(signal=signal, size_usdt=size_usdt)
                mode_label = "PAPER " if cfg.paper_trading else ""
                logger.info(
                    f"[{mode_label}ACTION] {signal.decision} {pair} @ ${signal.entry:,.2f} | "
                    f"SL: ${signal.stop_loss:,.2f} | TP: ${signal.take_profit:,.2f} | "
                    f"Size: ${size_usdt:.0f}"
                )
                state.save()
            else:
                logger.warning(f"Order rejected: {result.get('reason')}")

        except Exception as e:
            logger.error(f"Cycle error for {pair}: {e}", exc_info=True)

    logger.info(
        f"\nCYCLE END | Balance: ${state.paper_balance:,.2f} | "
        f"Daily P&L: {state.daily_pnl_pct:+.2f}%"
    )
    logger.info("=" * 60)


def main():
    cfg = Config()
    logger = get_logger("agent", log_dir=cfg.log_dir,
                        log_to_file=cfg.log_to_file, level=cfg.log_level)

    logger.info("╔══════════════════════════════════════════╗")
    logger.info("║      CLAUDE TRADING AGENT v1.0           ║")
    logger.info("╚══════════════════════════════════════════╝")
    logger.info(f"Mode: {'PAPER TRADING' if cfg.paper_trading else 'LIVE TRADING'}")
    logger.info(f"Pairs: {', '.join(cfg.trading_pairs)}")
    logger.info(f"Scan interval: {cfg.scan_interval_minutes} minutes")

    bybit = BybitClient(api_key=cfg.bybit_api_key, secret=cfg.bybit_secret,
                        testnet=cfg.bybit_testnet)
    coingecko = CoinGeckoClient()
    cryptopanic = CryptoPanicClient(api_key=cfg.cryptopanic_api_key)
    coinglass = CoinglassClient(api_key=cfg.coinglass_api_key)
    brain = ClaudeBrain(api_key=cfg.anthropic_api_key, model=cfg.claude_model)
    risk_mgr = RiskManager(cfg)
    state = State(path="state.json", initial_balance=cfg.paper_initial_balance)
    trade_logger = TradeLogger(log_dir=cfg.log_dir)

    paper_engine = PaperEngine(initial_balance=state.paper_balance) if cfg.paper_trading else None
    executor = Executor(
        paper_trading=cfg.paper_trading,
        paper_engine=paper_engine,
        bybit_client=None if cfg.paper_trading else bybit,
    )

    if cfg.paper_trading:
        logger.info(f"Paper balance: ${state.paper_balance:,.2f} USDT")
    else:
        try:
            balance = bybit.get_balance()
            logger.info(f"Live balance: ${balance['USDT']:,.2f} USDT available")
        except Exception as e:
            logger.error(f"Failed to fetch balance: {e}")

    args = (cfg, bybit, coingecko, cryptopanic, coinglass,
            brain, risk_mgr, executor, state, trade_logger, logger)

    # Run immediately on start
    run_cycle(*args)

    # Then schedule every N minutes
    schedule.every(cfg.scan_interval_minutes).minutes.do(run_cycle, *args)
    logger.info(f"Scheduler started — next cycle in {cfg.scan_interval_minutes} minutes.")

    while True:
        schedule.run_pending()
        time.sleep(10)


if __name__ == "__main__":
    main()
