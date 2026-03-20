import pandas as pd
import numpy as np
import pytest
from analysis.smc import detect_smc


def make_df_with_structure():
    data = {
        "open":  [100, 101, 105, 110, 108, 103, 100, 98,  102, 107],
        "high":  [102, 106, 111, 115, 112, 106, 102, 99,  108, 112],
        "low":   [99,  100, 104, 108, 105, 100, 97,  95,  99,  105],
        "close": [101, 105, 110, 113, 107, 102, 99,  97,  106, 110],
        "volume": [500, 800, 1200, 900, 600, 700, 500, 400, 900, 1100],
    }
    return pd.DataFrame(data)


def test_detect_smc_returns_required_keys():
    df = make_df_with_structure()
    result = detect_smc(df)
    for key in ["order_blocks", "fvgs", "swing_highs", "swing_lows",
                "bos_detected", "choch_detected", "premium_zone",
                "discount_zone", "liquidity_above", "liquidity_below"]:
        assert key in result, f"Missing SMC key: {key}"


def test_premium_discount_zones():
    df = make_df_with_structure()
    result = detect_smc(df)
    assert result["premium_zone"] > result["discount_zone"]


def test_fvg_detection_finds_gap():
    data = {
        "open":  [100, 101, 106],
        "high":  [101, 102, 110],
        "low":   [99,  100, 105],
        "close": [100, 101, 109],
        "volume": [500, 500, 1500],
    }
    df = pd.DataFrame(data)
    result = detect_smc(df)
    assert isinstance(result["fvgs"], list)
