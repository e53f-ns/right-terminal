import ccxt
import pandas as pd
from typing import Optional


class BybitClient:
    @staticmethod
    def to_perp(spot_pair: str) -> str:
        """Convert spot pair (BTC/USDT) to linear perpetual (BTC/USDT:USDT)."""
        base, quote = spot_pair.split("/")
        return f"{base}/{quote}:{quote}"

    def __init__(self, api_key: str, secret: str, testnet: bool = False):
        self._exchange = ccxt.bybit({
            "apiKey": api_key,
            "secret": secret,
            "enableRateLimit": True,
        })
        if testnet:
            self._exchange.set_sandbox_mode(True)

    def get_candles(self, pair: str, timeframe: str = "15m", limit: int = 200) -> pd.DataFrame:
        raw = self._exchange.fetch_ohlcv(pair, timeframe=timeframe, limit=limit)
        df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df = df.assign(timestamp=pd.to_datetime(df["timestamp"], unit="ms", utc=True))
        return df

    def get_orderbook_summary(self, pair: str, depth: int = 50) -> dict:
        ob = self._exchange.fetch_order_book(pair, limit=depth)
        bids = ob["bids"]   # [[price, size], ...]  descending
        asks = ob["asks"]   # [[price, size], ...]  ascending
        bid_wall = max(bids, key=lambda x: x[1]) if bids else [0, 0]
        ask_wall = max(asks, key=lambda x: x[1]) if asks else [0, 0]
        total_bid_vol = sum(b[1] for b in bids)
        total_ask_vol = sum(a[1] for a in asks)

        # Top-3 walls on each side (sorted by size descending)
        top_bid_walls = sorted(bids, key=lambda x: x[1], reverse=True)[:3]
        top_ask_walls = sorted(asks, key=lambda x: x[1], reverse=True)[:3]

        # Thin zones: price gaps between consecutive levels > 0.1% of mid price
        mid = (bids[0][0] + asks[0][0]) / 2 if bids and asks else 0
        thin_threshold = mid * 0.001
        thin_bid_zones, thin_ask_zones = [], []
        for i in range(len(bids) - 1):
            gap = bids[i][0] - bids[i + 1][0]
            if gap > thin_threshold:
                thin_bid_zones.append({"from": bids[i + 1][0], "to": bids[i][0], "gap": round(gap, 2)})
        for i in range(len(asks) - 1):
            gap = asks[i + 1][0] - asks[i][0]
            if gap > thin_threshold:
                thin_ask_zones.append({"from": asks[i][0], "to": asks[i + 1][0], "gap": round(gap, 2)})

        return {
            "best_bid": bids[0][0] if bids else None,
            "best_ask": asks[0][0] if asks else None,
            "bid_wall": {"price": bid_wall[0], "size": bid_wall[1]},
            "ask_wall": {"price": ask_wall[0], "size": ask_wall[1]},
            "top_bid_walls": [{"price": p, "size": s} for p, s in top_bid_walls],
            "top_ask_walls": [{"price": p, "size": s} for p, s in top_ask_walls],
            "thin_bid_zones": thin_bid_zones[:3],
            "thin_ask_zones": thin_ask_zones[:3],
            "bid_ask_ratio": round(total_bid_vol / total_ask_vol, 3) if total_ask_vol else 0,
            "raw_bids": bids,
            "raw_asks": asks,
        }

    def get_funding_rate(self, pair: str) -> float:
        perp = self.to_perp(pair) if ":" not in pair else pair
        result = self._exchange.fetch_funding_rate(perp)
        return result.get("fundingRate", 0.0)

    def get_open_interest(self, pair: str) -> Optional[float]:
        perp = self.to_perp(pair) if ":" not in pair else pair
        try:
            result = self._exchange.fetch_open_interest(perp)
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
