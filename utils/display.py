"""
Terminal display for the Claude Trading Agent v3.0
Wall Street multi-strategy output.
"""
from datetime import datetime, timezone
from colorama import Fore, Style, init

init(autoreset=True)

W = 56
SEP = "─" * W


# ── Helpers ──────────────────────────────────────────────────────────────────

def _rsi_label(rsi: float) -> str:
    if rsi >= 70:
        return "overbought"
    if rsi <= 30:
        return "oversold"
    return "neutral"


def _ema_trend(ind: dict) -> str:
    e20, e50, e200 = ind["ema20"], ind["ema50"], ind["ema200"]
    if e20 > e50 > e200:
        return "UP"
    if e20 < e50 < e200:
        return "DOWN"
    return "MIXED"


def _vol_arrow(trend: str) -> str:
    return {"increasing": "↑", "decreasing": "↓"}.get(trend, "→")


def _decision_color(decision: str) -> str:
    return {"BUY": Fore.GREEN, "SELL": Fore.RED, "HOLD": Fore.YELLOW,
            "SKIP": Fore.CYAN}.get(decision, "")


def _strategy_color(strategy: str) -> str:
    return {
        "SCALP": Fore.MAGENTA,
        "SWING": Fore.CYAN,
        "POSITION": Fore.BLUE,
    }.get(strategy, Fore.YELLOW)


def _two_col(left: str, right: str, width: int = W) -> str:
    gap = width - len(left) - len(right)
    return left + " " * max(1, gap) + right


def _clip(text: str, max_len: int = 52) -> str:
    text = text.strip()
    return text[:max_len] + "…" if len(text) > max_len else text


# ── Public API ────────────────────────────────────────────────────────────────

def print_startup(cfg, balance: float) -> None:
    mode = "PAPER" if cfg.paper_trading else "LIVE"
    print(f"\n{'═' * W}")
    print(f"  CLAUDE TRADING AGENT v3.0  WALL STREET  [{mode}]")
    print(f"{'═' * W}")
    pairs_str = ", ".join(p.split("/")[0] for p in cfg.trading_pairs)
    print(f"  Pairs   : {pairs_str}")
    print(f"  Model   : {cfg.claude_model}")
    print(f"  Balance : ${balance:,.2f} USDT")
    print(f"  Interval: {cfg.scan_interval_minutes}m")
    print(f"  Strategies: SCALP | SWING | POSITION")
    print(f"{'═' * W}\n")


def print_cycle_header(balance: float, daily_pnl: float, paper: bool) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    mode = "PAPER" if paper else "LIVE"
    pnl_color = Fore.GREEN if daily_pnl >= 0 else Fore.RED
    print(f"\n{SEP}")
    print(f" CYCLE — {now}   {mode}")
    print(
        f" Balance: ${balance:,.2f}  "
        f"P&L: {pnl_color}{daily_pnl:+.2f}%{Style.RESET_ALL}"
    )
    print(SEP)


def print_pair_block(
    pair: str,
    price: float,
    indicators: dict,
    smc: dict,
    fear_greed: dict,
    liquidity_analysis: dict,
    signal,
    trade_note: str = "",
    block_reason: str = "",
) -> None:
    rsi = indicators["rsi"]
    vol_ratio = indicators.get("volume_ratio", 1.0)
    vol_trend = indicators.get("volume_trend", "flat")
    vol_spike = indicators.get("volume_spike", False)
    sweep_count = len(liquidity_analysis.get("liquidity_sweeps", []))
    fvgs = smc.get("fvgs", [])
    bos_str = "✓" if smc.get("bos_detected") else "—"
    choch_str = "✓" if smc.get("choch_detected") else "—"
    fvg_str = str(len(fvgs)) if fvgs else "0"
    fg_val = fear_greed.get("value", "?")
    fg_label = fear_greed.get("label", "")

    # Header
    print(SEP)
    price_str = f"${price:,.2f}"
    print(_two_col(f" {pair}", price_str))
    print(SEP)

    # Technical summary row
    spike_tag = " ⚡" if vol_spike else ""
    print(_two_col(
        f" RSI: {rsi} ({_rsi_label(rsi)})",
        f"EMA: {_ema_trend(indicators)}"
    ))
    print(_two_col(
        f" Vol: {vol_ratio:.2f}x {_vol_arrow(vol_trend)}{spike_tag}",
        f"BOS:{bos_str} CHOCH:{choch_str} FVG:{fvg_str}"
    ))
    print(_two_col(
        f" Sweeps: {sweep_count}",
        f"F&G: {fg_val} ({fg_label})"
    ))

    # Signal output
    if signal is None:
        print(f" {Fore.RED}[ERROR]{Style.RESET_ALL} — analysis failed")
    elif signal.decision == "HOLD":
        print(f" {Fore.YELLOW}[HOLD]{Style.RESET_ALL} No setup meets criteria")
    else:
        strategy = getattr(signal, "strategy", "NONE")
        strat_color = _strategy_color(strategy)
        dec_color = _decision_color(signal.decision)

        print(f" {strat_color}[{strategy}]{Style.RESET_ALL} "
              f"{dec_color}{signal.decision}{Style.RESET_ALL} "
              f"Score: {signal.confidence}/100")

        # Signal breakdown
        breakdown = getattr(signal, "signal_breakdown", "")
        if breakdown:
            print(f"   {_clip(breakdown, 52)}")

        # Entry / SL / Targets
        if signal.entry:
            sl_str = f"${signal.stop_loss:,.2f}" if signal.stop_loss else "—"
            tp1_str = f"${signal.take_profit:,.2f}" if signal.take_profit else "—"
            print(f"   Entry: ${signal.entry:,.2f} | SL: {sl_str} | TP1: {tp1_str}")

            target_2 = getattr(signal, "target_2", None)
            if target_2:
                print(f"   TP2: ${target_2:,.2f}")

        # R:R and sizing
        rr_str = signal.risk_reward or "—"
        size_pct = getattr(signal, "size_pct", 0)
        hold_time = getattr(signal, "hold_time", "")
        if size_pct:
            print(f"   R:R {rr_str} | Size: {size_pct:.0f}% | Hold: {hold_time}")
        elif rr_str != "—":
            print(f"   R:R {rr_str}")

        # Thesis
        thesis = (signal.reasoning or "").strip()
        if thesis:
            print(f"   {Fore.WHITE}{_clip(thesis, 52)}{Style.RESET_ALL}")

        if block_reason:
            print(f"   {Fore.YELLOW}Blocked: {_clip(block_reason, 48)}{Style.RESET_ALL}")
        if trade_note:
            print(f"   {Fore.GREEN}{trade_note}{Style.RESET_ALL}")

    print(SEP)


def print_position_closed(pair: str, pnl: float, result_label: str) -> None:
    color = Fore.GREEN if pnl >= 0 else Fore.RED
    print(f" [CLOSED] {pair}  P&L: {color}${pnl:+.2f} ({result_label}){Style.RESET_ALL}")


def print_loss_streak_pause(pause_until: str) -> None:
    print(f" {Fore.YELLOW}[PAUSE]{Style.RESET_ALL} Loss streak — paused until {pause_until}")


def print_error(pair_or_label: str, reason: Exception) -> None:
    base = pair_or_label.split("/")[0] if "/" in pair_or_label else pair_or_label
    short = str(reason).split("\n")[0][:80]
    print(f" {Fore.RED}[ERROR]{Style.RESET_ALL} {base} — {short}")


def print_prefilter_summary(results: list, total: int) -> None:
    passed = [(p, r) for p, ok, r, _ in results if ok]
    skipped = [(p, r) for p, ok, r, _ in results if not ok]
    print(f"\n PRE-FILTER: {len(passed)}/{total} pairs → Claude")
    if passed:
        tags = [f"{Fore.GREEN}{p.split('/')[0]}{Style.RESET_ALL}" for p, _ in passed]
        print(f"  Pass: {', '.join(tags)}")
    if skipped:
        tags = [f"{Fore.CYAN}{p.split('/')[0]}{Style.RESET_ALL}" for p, _ in skipped]
        print(f"  Skip: {', '.join(tags)}")
    print()


def print_cycle_summary(
    results: list,
    balance: float,
    daily_pnl: float,
    next_run_time: str,
    strategy_counts: dict = None,
) -> None:
    now = datetime.now(timezone.utc).strftime("%H:%M UTC")
    print(f"\n CYCLE SUMMARY — {now}")

    # Strategy counts
    if strategy_counts:
        sc = strategy_counts
        print(f"  Slots: "
              f"{Fore.MAGENTA}SCALP {sc.get('SCALP', 0)}/3{Style.RESET_ALL} | "
              f"{Fore.CYAN}SWING {sc.get('SWING', 0)}/5{Style.RESET_ALL} | "
              f"{Fore.BLUE}POS {sc.get('POSITION', 0)}/2{Style.RESET_ALL}")

    row = []
    for i, item in enumerate(results):
        if len(item) == 4:
            pair, decision, conf, strategy = item
        else:
            pair, decision, conf = item
            strategy = None

        base = pair.split("/")[0]
        conf_str = f"{conf}" if conf is not None else "—"

        if strategy and strategy != "NONE" and decision != "HOLD":
            strat_tag = f"{_strategy_color(strategy)}{strategy[0]}{Style.RESET_ALL}"
            color = _decision_color(decision)
            row.append(f"{base} {strat_tag}{color}{decision}{Style.RESET_ALL} {conf_str}")
        else:
            color = _decision_color(decision)
            row.append(f"{base} {color}{decision}{Style.RESET_ALL} {conf_str}")

        if len(row) == 4 or i == len(results) - 1:
            print("  " + "  ".join(row))
            row = []

    pnl_color = Fore.GREEN if daily_pnl >= 0 else Fore.RED
    print(
        f" Balance: ${balance:,.2f} | "
        f"P&L: {pnl_color}{daily_pnl:+.2f}%{Style.RESET_ALL} | "
        f"Next: {next_run_time}"
    )
    print(SEP + "\n")
