from trading.paper_engine import PaperEngine
from analysis.claude_brain import TradeSignal


class Executor:
    def __init__(self, paper_trading: bool, paper_engine: PaperEngine = None, bybit_client=None):
        self._paper = paper_trading
        self._paper_engine = paper_engine
        self._bybit = bybit_client

    def execute(self, signal: TradeSignal, size_usdt: float,
                leverage: int = 1, strategy: str = "SCALP") -> dict:
        # Hard pre-trade validation — never open a position without protection
        if signal.confidence < 65:
            return {
                "status": "rejected",
                "reason": f"confidence {signal.confidence}% below minimum 65%",
            }
        if not signal.stop_loss:
            return {
                "status": "rejected",
                "reason": "missing stop_loss — trade rejected",
            }
        if not signal.take_profit:
            return {
                "status": "rejected",
                "reason": "missing take_profit — trade rejected",
            }

        try:
            if self._paper:
                return self._paper_engine.open_position(
                    pair=signal.pair,
                    side="buy" if signal.decision == "BUY" else "sell",
                    size_usdt=size_usdt,
                    entry=signal.entry,
                    stop_loss=signal.stop_loss,
                    take_profit=signal.take_profit,
                    confidence=signal.confidence,
                    leverage=leverage,
                    strategy=strategy,
                )
            else:
                amount = size_usdt / signal.entry
                return self._bybit.place_order(
                    pair=signal.pair,
                    side="buy" if signal.decision == "BUY" else "sell",
                    amount=amount,
                    stop_loss=signal.stop_loss,
                    take_profit=signal.take_profit,
                )
        except ValueError as e:
            return {"status": "rejected", "reason": str(e)}

    def check_exits(self, pair: str, current_price: float) -> dict:
        if self._paper:
            return self._paper_engine.check_stop_take(pair, current_price) or {}
        return {}

    def check_margin_health(self) -> str:
        """Returns pair to close if margin health is bad, else None."""
        if self._paper and self._paper_engine:
            return self._paper_engine.check_margin_health()
        return None
