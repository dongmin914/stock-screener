"""Apply the entry checklist with configurable thresholds and gradient (0-10) scoring."""
from __future__ import annotations

TIER_KO = {"entry": "본진입 후보", "watch": "분할 매수 관심", "pass": "조건 미충족"}

ICHIMOKU_MODES = {
    "A": ("전환>기준 돌파", "c6a_tenkan_cross"),
    "B": ("가격 구름대 아래", "c6b_below_cloud"),
    "C": ("구름대 상향 돌파", "c6c_cloud_breakout"),
    "D": ("가격 구름대 내부", "c6d_inside_cloud"),
}

# Max points per condition — sum = 10.0
WEIGHTS = {
    "c1_below_sma200": 1.0,
    "c2_disparity": 2.5,
    "c3_rsi_bounce": 1.5,
    "c4_bb_signal": 1.5,
    "c5_volume": 1.5,
    "c6_ichimoku": 2.0,
}

LABEL = {
    "c1_below_sma200": "200일선 아래",
    "c2_disparity": "이격도",
    "c3_rsi_bounce": "RSI 반등",
    "c4_bb_signal": "볼밴 복귀",
    "c5_volume": "거래량+양봉",
    "c6_ichimoku": "일목",
}

ENTRY_MIN = 7.5
WATCH_MIN = 5.0


def _c2_disparity(features: dict, threshold: float) -> float:
    disp = features.get("disparity_200", 0)
    if threshold >= 0 or disp >= 0:
        return 0.0
    ratio = min(abs(disp) / abs(threshold), 1.0)
    return round(ratio * WEIGHTS["c2_disparity"], 3)


def _c3_rsi(features: dict, threshold: float) -> float:
    min_rsi = features.get("rsi14_min_10", 100)
    now = features.get("rsi14", 100)
    prev = features.get("rsi14_prev", 0)
    if min_rsi <= threshold and now > threshold and now > prev:
        return WEIGHTS["c3_rsi_bounce"]
    return 0.0


def _c5_volume(features: dict) -> float:
    # c5_volume (from indicators) = True only when vol_ratio >= 1.5 AND bullish candle.
    # Give half credit when volume is elevated (>=1.0x avg) but doesn't meet full criteria.
    if features.get("c5_volume"):
        return WEIGHTS["c5_volume"]
    ratio = features.get("vol_ratio") or 0
    if ratio >= 1.0:
        return WEIGHTS["c5_volume"] * 0.5
    return 0.0


def evaluate(features: dict, rsi_threshold: float = 30, disparity_threshold: float = -10, ichimoku_mode: str = "A") -> dict:
    """Return per-condition point awards (floats)."""
    if not features:
        return {k: 0.0 for k in WEIGHTS}
    ichimoku_key = ICHIMOKU_MODES[ichimoku_mode][1]
    return {
        "c1_below_sma200": WEIGHTS["c1_below_sma200"] if features.get("c1_below_sma200") else 0.0,
        "c2_disparity": _c2_disparity(features, disparity_threshold),
        "c3_rsi_bounce": _c3_rsi(features, rsi_threshold),
        "c4_bb_signal": WEIGHTS["c4_bb_signal"] if features.get("c4_bb_signal") else 0.0,
        "c5_volume": _c5_volume(features),
        "c6_ichimoku": WEIGHTS["c6_ichimoku"] if features.get(ichimoku_key) else 0.0,
    }


def score(features: dict, rsi_threshold: float = 30, disparity_threshold: float = -10, ichimoku_mode: str = "A") -> dict:
    if not features:
        return {"score": 0.0, "conditions_met": [], "tier": "pass", **{k: 0.0 for k in WEIGHTS}}
    points = evaluate(features, rsi_threshold, disparity_threshold, ichimoku_mode)
    total = round(sum(points.values()), 1)
    met = [LABEL[k] for k, v in points.items() if v > 0]

    if total >= ENTRY_MIN:
        tier = "entry"
    elif total >= WATCH_MIN:
        tier = "watch"
    else:
        tier = "pass"

    # Booleans for display (any partial credit counts as "touched")
    bools = {k: v > 0 for k, v in points.items()}
    return {"score": total, "conditions_met": met, "tier": tier, **bools}
