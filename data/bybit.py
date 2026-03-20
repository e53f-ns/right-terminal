import ccxt
import pandas as pd
from typing import Optional


class BybitClient:
    def __init__(self, api_key: str, secret: str, testnet: bool = False):
        params = {
            "apiKey": api_key,
            "secret": secret,
            "enableRateLimit": True,
        }
        if testnet:
            params["urls"] = {"api": {"public": "https://api-testnet.bybit.com", "private": "https://api-testnet.bybit.com"}}
        self._exchange = ccxt.bybit(params)

    def get_candles(self, pair: str, timeframe: str = "15m", limit: int = 200) -> pd.DataFrame:
        raw = self._exchange.fetch_ohlcv(pair, timeframe=timeframe, limit=limit)
        df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df = df.assign(timestamp=pd.to_datetime(df["timestamp"], unit="ms", utc=True))
        return df

    def get_orderbook_summary(self, pair: str, depth: int = 20) -> dict:
        ob = self._exchange.fetch_order_book(pair, limit=depth)
        bids = ob["bids"]
        asks = ob["asks"]
        bid_wall = max(bids, key=lambda x: x[1]) if bids else [0, 0]
        ask_wall = max(asks, key=lambda x: x[1]) if asks else [0, 0]
        total_bid_vol = sum(b[1] for b in bids)
        total_ask_vol = sum(a[1] for a in asks)
        return {
            "best_bid": bids[0][0] if bids else None,
            "best_ask": asks[0][0] if asks else None,
            "bid_wall": {"price": bid_wall[0], "size": bid_wall[1]},
            "ask_wall": {"price": ask_wall[0], "size": ask_wall[1]},
            "bid_ask_ratio": round(total_bid_vol / total_ask_vol, 3) if total_ask_vol else 0,
        }

    def get_funding_rate(self, pair: str) -> float:
        result = self._exchange.fetch_funding_rate(pair)
        return result.get("fundingRate", 0.0)

    def get_open_interest(self, pair: str) -> Optional[float]:
        try:
            result = self._exchange.fetch_open_interest(pair)
            return result.get("openInterestValue")
        except Exception:
            return None

    def get_balance(self) -> dict:
        balance = self._exchange.fetch_balance()
        return {
            "USDT": balance.get("USDT", {}).get("free", 0.0),
            "total": balance.get("USDT", {}).get("total", 0.0),
        }

    def place_order(self, pair: str, side: str, amount: float,
                    stop_loss: float, take_profit: float) -> dict:
        order = self._exchange.create_order(
            symbol=pair,
            type="market",
            side=side,
            amount=amount,
            params={"stopLoss": {"triggerPrice": stop_loss},
                    "takeProfit": {"triggerPrice": take_profit}},
        )
        return order

    def cancel_order(self, order_id: str, pair: str) -> dict:
        return self._exchange.cancel_order(order_id, pair)
