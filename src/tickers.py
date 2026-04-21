"""Fetch S&P 500, NASDAQ-100, KOSPI 200 and KOSDAQ 150 tickers with company names."""
from __future__ import annotations

import sys
from io import StringIO

import pandas as pd
import requests

SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
NDX_URL = "https://en.wikipedia.org/wiki/Nasdaq-100"
UA = {"User-Agent": "Mozilla/5.0 (stock-screener research)"}


def _read_tables(url: str) -> list[pd.DataFrame]:
    resp = requests.get(url, headers=UA, timeout=30)
    resp.raise_for_status()
    return pd.read_html(StringIO(resp.text))


def _normalize(df: pd.DataFrame, sym_col: str, name_col: str) -> pd.DataFrame:
    out = df[[sym_col, name_col]].copy()
    out.columns = ["ticker", "name"]
    out["ticker"] = out["ticker"].astype(str).str.replace(".", "-", regex=False).str.strip()
    out["name"] = out["name"].astype(str).str.strip()
    return out


def _sp500() -> pd.DataFrame:
    return _normalize(_read_tables(SP500_URL)[0], "Symbol", "Security")


def _ndx() -> pd.DataFrame:
    for t in _read_tables(NDX_URL):
        if "Ticker" in t.columns and "Company" in t.columns:
            return _normalize(t, "Ticker", "Company")
        if "Symbol" in t.columns and "Company" in t.columns:
            return _normalize(t, "Symbol", "Company")
    raise RuntimeError("Could not locate NASDAQ-100 ticker table")


def _top_kr_by_mcap(market: str, suffix: str, top_n: int) -> pd.DataFrame:
    """Top-N Korean tickers by market cap for KOSPI or KOSDAQ.

    Uses FinanceDataReader (scrapes KRX public pages) — no credentials.
    Returns ticker, name, and market_cap_krw + shares_native so run.py can
    compute market_cap without calling yfinance.info (which gets 401s at scale).
    """
    import FinanceDataReader as fdr
    df = fdr.StockListing(market)
    cap_col = next((c for c in ("Marcap", "MarketCap", "시가총액") if c in df.columns), None)
    shares_col = next((c for c in ("Stocks", "shares") if c in df.columns), None)
    if cap_col is None:
        raise RuntimeError(f"No market cap column in FDR {market} listing: {list(df.columns)}")
    top = df.dropna(subset=[cap_col]).nlargest(top_n, cap_col)
    out = pd.DataFrame({
        "ticker": top["Code"].astype(str).str.zfill(6) + f".{suffix}",
        "name": top["Name"].astype(str),
        "market_cap_krw": top[cap_col].astype(float),
        "shares_native": top[shares_col].astype(float) if shares_col else None,
    })
    return out.reset_index(drop=True)


def _kospi200() -> pd.DataFrame:
    """Top 200 KOSPI stocks by market cap (≈ KOSPI 200 index)."""
    return _top_kr_by_mcap("KOSPI", "KS", 200)


def _kosdaq150() -> pd.DataFrame:
    """Top 150 KOSDAQ stocks by market cap (≈ KOSDAQ 150 index)."""
    return _top_kr_by_mcap("KOSDAQ", "KQ", 150)


def get_ticker_frame() -> pd.DataFrame:
    frames = [_sp500(), _ndx()]
    try:
        frames.append(_kospi200())
    except Exception as e:
        print(f"  ! KOSPI 200 failed: {e}", file=sys.stderr)
    try:
        frames.append(_kosdaq150())
    except Exception as e:
        print(f"  ! KOSDAQ 150 failed: {e}", file=sys.stderr)
    df = pd.concat(frames, ignore_index=True)
    # Allow standard US tickers (1-6 chars) and Korean format (6 digits + .KS/.KQ)
    df = df[df["ticker"].str.match(r"^([A-Z0-9\-]{1,6}|\d{6}\.(KS|KQ))$")]
    df = df.drop_duplicates(subset=["ticker"]).sort_values("ticker").reset_index(drop=True)
    return df


def get_tickers() -> list[str]:
    return get_ticker_frame()["ticker"].tolist()


if __name__ == "__main__":
    df = get_ticker_frame()
    print(f"Total tickers: {len(df)}")
    print(df.head(5).to_string(index=False))
