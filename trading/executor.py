from trading.paper_engine import PaperEngine
from analysis.claude_brain import TradeSignal


class Executor:
    def __init__(self, paper_trading: bool, paper_engine: PaperEngine = None, bybit_client=None):
        self._paper = paper_trading
        self._paper_engine = paper_engine
        self._bybit = bybit_client

    def execute(self, signal: TradeSignal, size_usdt: float) -> dict:
        if self._paper:
            return self._paper_engine.open_position(
                pair=signal.pair,
                side="buy" if signal.decision == "BUY" else "sell",
                size_usdt=size_usdt,
                entry=signal.entry,
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit,
                confidence=signal.confidence,
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

    def check_exits(self, pair: str, current_price: float) -> dict:
        if self._paper:
            return self._paper_engine.check_stop_take(pair, current_price) or {}
        return {}
