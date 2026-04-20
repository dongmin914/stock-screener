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
    """Return (rate, is_fresh). is_fresh=False means fallback was used.

    Tries yf.download first (most reliable for forex), then fast_info, then history,
    before falling back to persisted meta.json or DEFAULT_RATE.
    """
    for fetch in (_fetch_via_download, _fetch_via_fast_info, _fetch_via_history):
        try:
            rate = fetch()
            if rate and rate > 0:
                _persist(rate)
                return rate, True
        except Exception:
            continue
    return _read_fallback(), False


def _fetch_via_download() -> float | None:
    df = yf.download("USDKRW=X", period="5d", progress=False, auto_adjust=True)
    if df is None or df.empty:
        return None
    close = df["Close"].dropna()
    return float(close.iloc[-1]) if len(close) else None


def _fetch_via_fast_info() -> float | None:
    fi = yf.Ticker("USDKRW=X").fast_info
    for key in ("last_price", "lastPrice", "regularMarketPrice"):
        val = fi.get(key) if hasattr(fi, "get") else getattr(fi, key, None)
        if val:
            return float(val)
    return None


def _fetch_via_history() -> float | None:
    hist = yf.Ticker("USDKRW=X").history(period="5d")
    if hist is None or hist.empty:
        return None
    return float(hist["Close"].iloc[-1])


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
