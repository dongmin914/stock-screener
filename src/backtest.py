"""Per-ticker historical win rate of the 6-condition entry checklist."""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.indicators import rsi, bollinger, ichimoku
from src.screener import score as compute_score


LOOKBACK_DAYS = 252  # ~1 trading year
HOLD_DAYS = 30       # measure outcome 30 days after entry
WIN_THRESHOLD = 0.05 # 5% gain = win
MIN_EVENTS = 3       # need at least 3 signals to report a win rate


def compute_win_rate(
    df: pd.DataFrame,
    rsi_threshold: float = 30,
    disparity_threshold: float = -10,
    ichimoku_mode: str = "B",
    entry_min_score: float = 7.5,
) -> tuple[float | None, int]:
    """Return (win_rate_pct, event_count). win_rate_pct is None if events < MIN_EVENTS.

    df: OHLCV DataFrame with at least 400 rows (need history for SMA200 + forward lookup)
    """
    if len(df) < 400:
        return None, 0

    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    vol = df["Volume"]

    # Precompute indicator series once (vectorized)
    sma200 = close.rolling(200).mean()
    sma50 = close.rolling(50).mean()
    rsi14 = rsi(close, 14)
    bb_upper, _, bb_lower = bollinger(close, 20, 2.0)
    tenkan, kijun, span_a, span_b = ichimoku(high, low)
    cloud_top = pd.concat([span_a, span_b], axis=1).max(axis=1)
    cloud_bot = pd.concat([span_a, span_b], axis=1).min(axis=1)
    vol_avg20 = vol.rolling(20).mean()

    # Iterate over lookback window — each day, build a features dict and score it
    start = max(len(df) - LOOKBACK_DAYS - HOLD_DAYS, 220)
    end = len(df) - HOLD_DAYS  # exclude days without enough forward data

    events = 0
    wins = 0

    for i in range(start, end):
        price = close.iloc[i]
        sma200_v = sma200.iloc[i]
        if pd.isna(price) or pd.isna(sma200_v) or sma200_v <= 0:
            continue

        rsi_now = rsi14.iloc[i]
        rsi_prev = rsi14.iloc[i - 1]
        rsi_min_10 = rsi14.iloc[max(0, i - 9):i + 1].min()
        if pd.isna(rsi_now) or pd.isna(rsi_prev) or pd.isna(rsi_min_10):
            continue

        # BB signal: touch lower in last 5 bars + recovered now
        bb_touch_lower_recent = bool((close.iloc[max(0, i - 4):i + 1] <= bb_lower.iloc[max(0, i - 4):i + 1]).any())
        bb_recovered = bool(price >= bb_lower.iloc[i])
        c4 = bb_touch_lower_recent and bb_recovered

        vol_avg = vol_avg20.iloc[i]
        if pd.isna(vol_avg) or vol_avg <= 0:
            continue
        vol_ratio = vol.iloc[i] / vol_avg
        bullish_candle = bool(close.iloc[i] > df["Open"].iloc[i])
        c5 = bool(vol_ratio >= 1.5 and bullish_candle)

        # Ichimoku: compute the 4 conditions for this day
        diff = tenkan - kijun
        cross_now_positive = bool(diff.iloc[max(0, i - 2):i + 1].gt(0).any())
        was_non_positive = bool(diff.iloc[max(0, i - 3):i].le(0).any())
        c6a = bool(cross_now_positive and was_non_positive and diff.iloc[i] > 0)

        ct = cloud_top.iloc[i]
        cb = cloud_bot.iloc[i]
        if pd.isna(ct) or pd.isna(cb):
            continue
        c6b = bool(price < cb)
        below_recent = bool((close.iloc[max(0, i - 4):i + 1] < cloud_bot.iloc[max(0, i - 4):i + 1]).any())
        c6c = bool(below_recent and price >= ct)
        c6d = bool(cb <= price <= ct)

        # Build features dict matching compute_features' output
        feats = {
            "price": float(price),
            "sma50": float(sma50.iloc[i]) if not pd.isna(sma50.iloc[i]) else 0.0,
            "sma200": float(sma200_v),
            "disparity_200": float((price / sma200_v - 1) * 100),
            "rsi14": float(rsi_now),
            "rsi14_prev": float(rsi_prev),
            "rsi14_min_10": float(rsi_min_10),
            "bb_upper": float(bb_upper.iloc[i]) if not pd.isna(bb_upper.iloc[i]) else 0.0,
            "bb_lower": float(bb_lower.iloc[i]) if not pd.isna(bb_lower.iloc[i]) else 0.0,
            "vol_ratio": float(vol_ratio),
            "tenkan": float(tenkan.iloc[i]) if not pd.isna(tenkan.iloc[i]) else 0.0,
            "kijun": float(kijun.iloc[i]) if not pd.isna(kijun.iloc[i]) else 0.0,
            "cloud_top": float(ct),
            "cloud_bot": float(cb),
            "c1_below_sma200": bool(price < sma200_v),
            "c4_bb_signal": c4,
            "c5_volume": c5,
            "c6a_tenkan_cross": c6a,
            "c6b_below_cloud": c6b,
            "c6c_cloud_breakout": c6c,
            "c6d_inside_cloud": c6d,
        }

        s = compute_score(feats, rsi_threshold, disparity_threshold, ichimoku_mode)
        if s["score"] >= entry_min_score:
            events += 1
            future_price = close.iloc[i + HOLD_DAYS]
            if pd.notna(future_price) and (future_price / price - 1) >= WIN_THRESHOLD:
                wins += 1

    if events < MIN_EVENTS:
        return None, events
    return round(100 * wins / events, 1), events
