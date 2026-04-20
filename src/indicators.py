"""Technical indicators for the entry checklist.

Exposes RAW measurements where thresholds are user-tunable (RSI, disparity),
and pre-computed booleans for conditions that don't need tuning (BB/volume/ichimoku).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def rsi(close: pd.Series, length: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / length, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def bollinger(close: pd.Series, length: int = 20, std: float = 2.0):
    mid = close.rolling(length).mean()
    sd = close.rolling(length).std(ddof=0)
    return mid + std * sd, mid, mid - std * sd


def ichimoku(high: pd.Series, low: pd.Series):
    """Return (tenkan, kijun, span_a, span_b).

    span_a/span_b are shifted 26 bars forward to match TradingView's display —
    the cloud visible "today" on the chart equals values computed 26 bars ago.
    """
    tenkan = (high.rolling(9).max() + low.rolling(9).min()) / 2
    kijun = (high.rolling(26).max() + low.rolling(26).min()) / 2
    span_a = ((tenkan + kijun) / 2).shift(26)
    span_b = ((high.rolling(52).max() + low.rolling(52).min()) / 2).shift(26)
    return tenkan, kijun, span_a, span_b


def compute_features(df: pd.DataFrame) -> dict:
    if len(df) < 220:
        return {}

    close, high, low, volume = df["Close"], df["High"], df["Low"], df["Volume"]

    sma200 = close.rolling(200).mean()
    sma50 = close.rolling(50).mean()
    rsi14 = rsi(close, 14)
    bb_upper, _, bb_lower = bollinger(close, 20, 2.0)
    tenkan, kijun, span_a, span_b = ichimoku(high, low)
    cloud_top = pd.concat([span_a, span_b], axis=1).max(axis=1)
    cloud_bot = pd.concat([span_a, span_b], axis=1).min(axis=1)
    vol_avg20 = volume.rolling(20).mean()

    last = -1
    price = float(close.iloc[last])
    sma200_v = float(sma200.iloc[last])
    if pd.isna(price) or pd.isna(sma200_v) or sma200_v <= 0:
        return {}
    disparity_200 = (price / sma200_v - 1) * 100

    # Raw RSI measurements — threshold applied in screener
    rsi_now = float(rsi14.iloc[last])
    rsi_prev = float(rsi14.iloc[last - 1])
    rsi_min_10 = float(rsi14.iloc[-10:].min())
    if any(pd.isna(v) for v in (rsi_now, rsi_prev, rsi_min_10)):
        return {}

    # Fixed-logic conditions
    bb_touch_lower_recent = bool((close.iloc[-5:] <= bb_lower.iloc[-5:]).any())
    bb_recovered = bool(close.iloc[last] >= bb_lower.iloc[last])
    c4_bb_signal = bb_touch_lower_recent and bb_recovered

    vol_avg = float(vol_avg20.iloc[last])
    if pd.isna(vol_avg) or vol_avg <= 0:
        return {}
    vol_ratio = float(volume.iloc[last]) / vol_avg
    bullish_candle = bool(close.iloc[last] > df["Open"].iloc[last])
    c5_volume = bool(vol_ratio >= 1.5 and bullish_candle)

    # Option A: tenkan > kijun golden cross within last 3 bars
    diff = tenkan - kijun
    cross_now_positive = bool(diff.iloc[-3:].gt(0).any())
    was_non_positive = bool(diff.iloc[-4:-1].le(0).any())
    c6a_tenkan_cross = bool(cross_now_positive and was_non_positive and diff.iloc[last] > 0)

    cloud_top_now = float(cloud_top.iloc[last])
    cloud_bot_now = float(cloud_bot.iloc[last])
    if pd.isna(cloud_top_now) or pd.isna(cloud_bot_now):
        return {}

    # Option B: price below cloud (weak zone — deep-discount buy zone per user's style)
    c6b_below_cloud = bool(price < cloud_bot_now)

    # Option C: bullish breakout — was below cloud recently, now at/above cloud top
    below_recent = bool((close.iloc[-5:] < cloud_bot.iloc[-5:]).any())
    c6c_cloud_breakout = bool(below_recent and close.iloc[last] >= cloud_top_now)

    # Option D: price currently inside the cloud (trend-change zone)
    c6d_inside_cloud = bool(cloud_bot_now <= price <= cloud_top_now)

    return {
        "price": price,
        "sma50": float(sma50.iloc[last]),
        "sma200": sma200_v,
        "disparity_200": float(disparity_200),
        "rsi14": rsi_now,
        "rsi14_prev": rsi_prev,
        "rsi14_min_10": rsi_min_10,
        "bb_upper": float(bb_upper.iloc[last]),
        "bb_lower": float(bb_lower.iloc[last]),
        "vol_ratio": vol_ratio,
        "tenkan": float(tenkan.iloc[last]),
        "kijun": float(kijun.iloc[last]),
        "cloud_top": cloud_top_now,
        "cloud_bot": cloud_bot_now,
        "c1_below_sma200": bool(price < sma200_v),
        "c4_bb_signal": c4_bb_signal,
        "c5_volume": c5_volume,
        "c6a_tenkan_cross": c6a_tenkan_cross,
        "c6b_below_cloud": c6b_below_cloud,
        "c6c_cloud_breakout": c6c_cloud_breakout,
        "c6d_inside_cloud": c6d_inside_cloud,
    }
