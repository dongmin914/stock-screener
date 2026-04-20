"""Exchange rate helper: USD/KRW via yfinance with meta.json fallback."""
from __future__ import annotations

import json
from pathlib import Path

import streamlit as st
import yfinance as yf

META = Path(__file__).resolve().parent.parent / "data" / "meta.json"
DEFAULT_RATE = 1380.0  # ultimate fallback if meta has no rate


@st.cache_data(ttl=3600)
def get_usd_krw() -> tuple[float, bool]:
    """Return (rate, is_fresh). is_fresh=False means fallback was used."""
    try:
        rate = float(yf.Ticker("USDKRW=X").fast_info["last_price"])
        if rate > 0:
            _persist(rate)
            return rate, True
    except Exception:
        pass
    return _read_fallback(), False


def _persist(rate: float):
    try:
        meta = json.loads(META.read_text(encoding="utf-8")) if META.exists() else {}
        meta["usd_krw"] = rate
        META.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _read_fallback() -> float:
    try:
        if META.exists():
            meta = json.loads(META.read_text(encoding="utf-8"))
            return float(meta.get("usd_krw", DEFAULT_RATE))
    except Exception:
        pass
    return DEFAULT_RATE
