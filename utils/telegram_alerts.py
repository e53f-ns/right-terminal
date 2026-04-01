"""
Telegram alerts for the trading agent.
Uses the Bot API directly via requests — no extra library required.

Commands handled via polling background thread:
  /status  — current positions + balance
  /pause   — pause trading
  /resume  — resume trading
"""

import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_BASE = "https://api.telegram.org/bot{token}/{method}"

SIDE_EMOJI = {"long": "🟢", "short": "🔴", "buy": "🟢", "sell": "🔴"}
WIN_EMOJI = "✅"
LOSS_EMOJI = "🔴"


class TelegramAlerter:
    def __init__(self, token: str, chat_id: str):
        self._token = token or ""
        self._chat_id = chat_id or ""
        self._enabled = bool(self._token and self._chat_id)
        self._last_update_id = 0
        self._stop_polling = threading.Event()
        self._poll_thread: Optional[threading.Thread] = None
        # These are injected by start_command_listener
        self._state = None
        self._agent_logger = None

    # ── Outbound alerts ──────────────────────────────────────────────────────

    def trade_opened(self, pair: str, side: str, entry: float, sl: Optional[float],
                     tp: Optional[float], leverage: int, confidence: int,
                     strategy: str, balance: float):
        emoji = SIDE_EMOJI.get(side.lower(), "⚡")
        direction = side.upper()
        sl_str = f"SL${sl:,.2f}" if sl else "no SL ⚠️"
        tp_str = f"TP${tp:,.2f}" if tp else "no TP"
        msg = (
            f"{emoji} OPENED {direction} {pair}\n"
            f"Entry: ${entry:,.4f} | {sl_str} | {tp_str}\n"
            f"Leverage: {leverage}x | Confidence: {confidence}% | {strategy}\n"
            f"Balance: ${balance:,.2f}"
        )
        self.send(msg)

    def trade_closed(self, pair: str, side: str, pnl_usdt: float, pnl_pct: float,
                     close_reason: str, total_balance: float, total_pnl_pct: float):
        if pnl_usdt >= 0:
            emoji = WIN_EMOJI
            tag = "WIN"
        else:
            emoji = LOSS_EMOJI
            tag = "LOSS"
        msg = (
            f"{emoji} CLOSED {pair} — {tag}\n"
            f"P&L: {pnl_usdt:+.2f} USDT ({pnl_pct:+.2f}%) | {close_reason}\n"
            f"Balance: ${total_balance:,.2f} | Total: {total_pnl_pct:+.1f}%"
        )
        self.send(msg)

    def circuit_breaker(self, reason: str, resume_at: str):
        msg = (
            f"⚠️ AGENT PAUSED\n"
            f"Reason: {reason}\n"
            f"Resumes: {resume_at}"
        )
        self.send(msg)

    def daily_summary(self, trades: int, wins: int, losses: int,
                      pnl_usdt: float, pnl_pct: float, balance: float):
        wr = round(wins / trades * 100, 1) if trades else 0
        emoji = "🟢" if pnl_usdt >= 0 else "🔴"
        msg = (
            f"{emoji} DAILY SUMMARY — {datetime.now(timezone.utc).strftime('%Y-%m-%d')}\n"
            f"Trades: {trades} | W:{wins} L:{losses} | WR:{wr}%\n"
            f"P&L: {pnl_usdt:+.2f} USDT ({pnl_pct:+.2f}%)\n"
            f"Balance: ${balance:,.2f}"
        )
        self.send(msg)

    def agent_started(self, mode: str, balance: float, pairs: int):
        msg = (
            f"🚀 TRADING AGENT STARTED\n"
            f"Mode: {mode} | Balance: ${balance:,.2f}\n"
            f"Watching {pairs} pairs"
        )
        self.send(msg)

    def send(self, text: str) -> bool:
        """Send a message. Returns True on success, False on failure."""
        if not self._enabled:
            return False
        url = _BASE.format(token=self._token, method="sendMessage")
        try:
            resp = requests.post(url, json={
                "chat_id": self._chat_id,
                "text": text,
                "parse_mode": "HTML",
            }, timeout=10)
            if not resp.ok:
                logger.warning(f"Telegram send failed: {resp.status_code} {resp.text[:100]}")
                return False
            return True
        except requests.RequestException as e:
            logger.warning(f"Telegram send error: {e}")
            return False

    # ── Inbound commands ─────────────────────────────────────────────────────

    def start_command_listener(self, state, agent_logger):
        """Start background thread that polls for /status /pause /resume."""
        if not self._enabled:
            return
        self._state = state
        self._agent_logger = agent_logger
        self._stop_polling.clear()
        self._poll_thread = threading.Thread(
            target=self._poll_loop, daemon=True, name="telegram-poll"
        )
        self._poll_thread.start()
        agent_logger.info("Telegram command listener started")

    def stop(self):
        self._stop_polling.set()

    def _poll_loop(self):
        while not self._stop_polling.is_set():
            try:
                self._process_updates()
            except Exception as e:
                logger.warning(f"Telegram poll error: {e}")
            time.sleep(5)  # poll every 5 seconds

    def _process_updates(self):
        url = _BASE.format(token=self._token, method="getUpdates")
        resp = requests.get(url, params={
            "offset": self._last_update_id + 1,
            "timeout": 4,
            "allowed_updates": ["message"],
        }, timeout=10)
        if not resp.ok:
            return
        data = resp.json()
        if not data.get("ok"):
            return
        for update in data.get("result", []):
            self._last_update_id = update["update_id"]
            msg = update.get("message", {})
            text = msg.get("text", "").strip().lower()
            chat_id = str(msg.get("chat", {}).get("id", ""))
            # Only accept commands from the configured chat
            if chat_id != self._chat_id:
                continue
            if text.startswith("/status"):
                self._handle_status()
            elif text.startswith("/pause"):
                self._handle_pause()
            elif text.startswith("/resume"):
                self._handle_resume()
            elif text.startswith("/help"):
                self._handle_help()

    def _handle_status(self):
        if not self._state:
            self.send("⚠️ State not available")
            return
        balance = self._state.paper_balance
        positions = self._state._data.get("open_positions", {})
        stock_pos = self._state._data.get("stock_positions", {})
        forex_pos = self._state._data.get("forex_positions", {})
        all_pos = {**positions, **stock_pos, **forex_pos}
        total_trades = self._state.total_trades
        wins = self._state.winning_trades
        wr = round(wins / total_trades * 100, 1) if total_trades else 0
        daily_pnl = self._state._data.get("daily_pnl_pct", 0)

        if all_pos:
            pos_lines = []
            for sym, pos in all_pos.items():
                side = pos.get("side", "?").upper()
                entry = pos.get("entry", 0)
                lev = pos.get("leverage", 1)
                conf = pos.get("confidence", 0)
                strategy = pos.get("strategy", "?")
                pos_lines.append(f"  {sym} {side} @${entry:,.4f} {lev}x [{strategy}] conf:{conf}%")
            pos_str = "\n".join(pos_lines)
        else:
            pos_str = "  No open positions"

        paused = self._state._data.get("paused_until", "")
        paused_str = ""
        if paused:
            try:
                pt = datetime.fromisoformat(paused)
                if pt > datetime.now(timezone.utc):
                    paused_str = f"\n⚠️ PAUSED until {pt.strftime('%H:%M UTC')}"
            except Exception:
                pass

        msg = (
            f"📊 STATUS\n"
            f"Balance: ${balance:,.2f} | Daily P&L: {daily_pnl:+.1f}%\n"
            f"Trades: {total_trades} | WR: {wr}%\n"
            f"\nPositions:\n{pos_str}"
            f"{paused_str}"
        )
        self.send(msg)

    def _handle_pause(self):
        if not self._state:
            self.send("⚠️ State not available")
            return
        from datetime import timedelta
        pause_until = datetime.now(timezone.utc) + timedelta(hours=24)
        self._state._data["paused_until"] = pause_until.isoformat()
        self._state.save()
        if self._agent_logger:
            self._agent_logger.warning("Agent PAUSED via Telegram /pause")
        self.send(f"⏸️ AGENT PAUSED\nWill resume at {pause_until.strftime('%Y-%m-%d %H:%M UTC')}\nUse /resume to unpause early")

    def _handle_resume(self):
        if not self._state:
            self.send("⚠️ State not available")
            return
        self._state._data["paused_until"] = datetime.now(timezone.utc).isoformat()
        self._state.save()
        if self._agent_logger:
            self._agent_logger.info("Agent RESUMED via Telegram /resume")
        self.send("▶️ AGENT RESUMED — trading is active")

    def _handle_help(self):
        self.send(
            "📖 COMMANDS\n"
            "/status — current positions + P&L\n"
            "/pause — pause trading for 24h\n"
            "/resume — resume trading immediately\n"
            "/help — show this message"
        )
