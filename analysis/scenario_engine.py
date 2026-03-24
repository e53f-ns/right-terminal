"""
MiroFish Scenario Simulation Engine

Forward-looking macro scenario predictions that feed into
Claude's trade decision making. Queries MiroFish API for
predicted market impact of macro events, sentiment shifts,
and technical breakouts.

Results are cached for 30 minutes to avoid hammering the API.
"""

import json
import time
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

logger = logging.getLogger("agent.scenario")

MIROFISH_URL = "https://mirofish.homes/"
CACHE_TTL_SECONDS = 1800  # 30 minutes


@dataclass
class ScenarioPrediction:
    direction: str       # BULLISH / BEARISH / NEUTRAL
    confidence: int      # 0-100
    risk_factors: str    # key risks in one line
    timeframe: str       # e.g. "24h", "48h"
    scenario: str        # the scenario that was simulated
    raw_response: str    # full API response text
    timestamp: float     # when this was fetched


class ScenarioEngine:
    def __init__(self):
        self._cache: dict[str, ScenarioPrediction] = {}
        self._last_daily_run: Optional[str] = None  # YYYY-MM-DD

    def should_run(self, fear_greed: int, fg_24h_change: float,
                   geopolitical_risk: int = 0, hour_utc: int = 0) -> bool:
        """Determine if MiroFish should be queried this cycle."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # Once per day general market scenario (at 8 UTC)
        if hour_utc == 8 and self._last_daily_run != today:
            return True

        # Dramatic F&G shift
        if abs(fg_24h_change) > 10:
            return True

        # Extreme fear or greed
        if fear_greed < 15 or fear_greed > 85:
            return True

        # High geopolitical risk
        if geopolitical_risk > 70:
            return True

        return False

    def get_scenario(self, scenario_key: str) -> Optional[ScenarioPrediction]:
        """Get cached scenario if still valid."""
        cached = self._cache.get(scenario_key)
        if cached and (time.time() - cached.timestamp) < CACHE_TTL_SECONDS:
            return cached
        return None

    def simulate(self, scenario_text: str, scenario_key: str) -> ScenarioPrediction:
        """Query MiroFish API with a scenario. Returns prediction."""
        # Check cache first
        cached = self.get_scenario(scenario_key)
        if cached:
            logger.info(f"SCENARIO cache hit: {scenario_key}")
            return cached

        logger.info(f"SCENARIO query: {scenario_text[:80]}...")

        try:
            payload = json.dumps({"scenario": scenario_text}).encode("utf-8")
            req = Request(
                MIROFISH_URL,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(req, timeout=15) as resp:
                raw = resp.read().decode("utf-8")

            prediction = self._parse_response(raw, scenario_text)
            self._cache[scenario_key] = prediction
            logger.info(
                f"SCENARIO result: {prediction.direction} "
                f"{prediction.confidence}% [{prediction.timeframe}]"
            )
            return prediction

        except (URLError, HTTPError, TimeoutError) as e:
            logger.warning(f"MiroFish API error: {e}")
            return self._neutral_fallback(scenario_text, str(e))
        except Exception as e:
            logger.warning(f"MiroFish unexpected error: {e}")
            return self._neutral_fallback(scenario_text, str(e))

    def run_macro_scenarios(self, fear_greed: int, btc_price: float,
                            fg_24h_change: float = 0) -> list[ScenarioPrediction]:
        """Run relevant macro scenarios based on current conditions."""
        scenarios = []
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # General daily market outlook
        if self._last_daily_run != today:
            pred = self.simulate(
                f"Given current crypto Fear & Greed index at {fear_greed} "
                f"and BTC at ${btc_price:,.0f}, what is the most likely "
                f"market direction for crypto in the next 24-48 hours?",
                scenario_key=f"daily_outlook_{today}",
            )
            scenarios.append(pred)
            self._last_daily_run = today

        # Extreme fear scenario
        if fear_greed < 15:
            pred = self.simulate(
                f"If Fear & Greed drops below 10 (current: {fear_greed}), "
                f"what historically happens to crypto markets in the next 48 hours? "
                f"BTC is at ${btc_price:,.0f}.",
                scenario_key=f"extreme_fear_{fear_greed}",
            )
            scenarios.append(pred)

        # Extreme greed scenario
        elif fear_greed > 85:
            pred = self.simulate(
                f"If Fear & Greed is above 85 (current: {fear_greed}), "
                f"what is the risk of a crypto market correction? "
                f"BTC is at ${btc_price:,.0f}.",
                scenario_key=f"extreme_greed_{fear_greed}",
            )
            scenarios.append(pred)

        # F&G dramatic shift
        if abs(fg_24h_change) > 10:
            direction = "dropped" if fg_24h_change < 0 else "surged"
            pred = self.simulate(
                f"Fear & Greed {direction} by {abs(fg_24h_change):.0f} points "
                f"in 24 hours (now {fear_greed}). How will crypto react "
                f"in the next 24-48 hours? BTC at ${btc_price:,.0f}.",
                scenario_key=f"fg_shift_{int(fg_24h_change)}",
            )
            scenarios.append(pred)

        # BTC breakout scenarios
        round_levels = [50000, 60000, 70000, 75000, 80000, 90000, 100000]
        for level in round_levels:
            if abs(btc_price - level) / level < 0.02:  # within 2% of level
                above = "above" if btc_price > level else "below"
                pred = self.simulate(
                    f"If BTC breaks {'above' if btc_price < level else 'further above'} "
                    f"${level:,} with high volume (currently ${btc_price:,.0f}), "
                    f"what is the likely next 48h price action?",
                    scenario_key=f"btc_level_{level}",
                )
                scenarios.append(pred)
                break

        return scenarios

    def get_compact_summary(self) -> str:
        """One-line summary of active scenarios for Claude prompt."""
        now = time.time()
        active = [
            p for p in self._cache.values()
            if (now - p.timestamp) < CACHE_TTL_SECONDS
        ]
        if not active:
            return "none"

        parts = []
        for p in active[:3]:  # max 3 scenarios in prompt
            parts.append(f"{p.direction}({p.confidence}%,{p.timeframe})")
        return " | ".join(parts)

    def _parse_response(self, raw: str, scenario_text: str) -> ScenarioPrediction:
        """Parse MiroFish response into structured prediction."""
        now = time.time()

        # Try JSON response
        try:
            data = json.loads(raw)
            return ScenarioPrediction(
                direction=self._extract_direction(
                    data.get("prediction", data.get("direction", ""))
                ),
                confidence=int(data.get("confidence", 50)),
                risk_factors=data.get("risk_factors", data.get("risks", "unknown")),
                timeframe=data.get("timeframe", "24-48h"),
                scenario=scenario_text,
                raw_response=raw[:500],
                timestamp=now,
            )
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

        # Parse free-text response
        text_lower = raw.lower()
        direction = self._extract_direction(raw)

        # Extract confidence from text
        confidence = 50  # default
        import re
        conf_match = re.search(r'(\d{1,3})\s*%', raw)
        if conf_match:
            val = int(conf_match.group(1))
            if 0 <= val <= 100:
                confidence = val

        # Extract timeframe
        timeframe = "24-48h"
        tf_match = re.search(r'(\d+)\s*-?\s*(\d+)?\s*(hour|day|h|d)', text_lower)
        if tf_match:
            timeframe = tf_match.group(0)

        # Extract risk factors (first sentence mentioning risk)
        risk_factors = "see full response"
        for sentence in raw.split('.'):
            if any(w in sentence.lower() for w in ['risk', 'danger', 'warning', 'caution']):
                risk_factors = sentence.strip()[:120]
                break

        return ScenarioPrediction(
            direction=direction,
            confidence=confidence,
            risk_factors=risk_factors,
            timeframe=timeframe,
            scenario=scenario_text,
            raw_response=raw[:500],
            timestamp=now,
        )

    @staticmethod
    def _extract_direction(text: str) -> str:
        """Extract BULLISH/BEARISH/NEUTRAL from text."""
        t = text.upper()
        bull_words = ["BULLISH", "UPWARD", "RALLY", "RECOVERY", "BOUNCE", "HIGHER"]
        bear_words = ["BEARISH", "DOWNWARD", "CRASH", "CORRECTION", "DROP", "LOWER"]
        bull_score = sum(1 for w in bull_words if w in t)
        bear_score = sum(1 for w in bear_words if w in t)
        if bull_score > bear_score:
            return "BULLISH"
        elif bear_score > bull_score:
            return "BEARISH"
        return "NEUTRAL"

    @staticmethod
    def _neutral_fallback(scenario_text: str, error: str) -> ScenarioPrediction:
        return ScenarioPrediction(
            direction="NEUTRAL",
            confidence=0,
            risk_factors=f"API unavailable: {error[:80]}",
            timeframe="N/A",
            scenario=scenario_text,
            raw_response=error,
            timestamp=time.time(),
        )
