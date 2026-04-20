"""Display formatting helpers for market cap and other dashboard values."""
from __future__ import annotations

import pandas as pd


def format_market_cap(usd: float, usd_krw: float) -> str:
    if usd is None or usd <= 0 or pd.isna(usd):
        return "-"
    # USD side
    if usd >= 1e12:
        usd_str = f"${usd / 1e12:.1f}T"
    elif usd >= 1e9:
        usd_str = f"${usd / 1e9:.0f}B"
    else:
        usd_str = f"${usd / 1e6:.0f}M"
    # KRW side
    krw = usd * usd_krw
    if krw >= 10 * 1e12:  # 10조 이상 → no decimal
        krw_str = f"{krw / 1e12:,.0f}조"
    elif krw >= 1e12:  # 1~10조 → 1 decimal
        krw_str = f"{krw / 1e12:.1f}조"
    else:
        krw_str = f"{krw / 1e8:,.0f}억"
    return f"{usd_str} ({krw_str})"
