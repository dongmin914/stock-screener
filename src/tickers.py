"""Fetch S&P 500 and NASDAQ-100 tickers with company names."""
from __future__ import annotations

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


def get_ticker_frame() -> pd.DataFrame:
    df = pd.concat([_sp500(), _ndx()], ignore_index=True)
    df = df[df["ticker"].str.isascii() & df["ticker"].str.len().between(1, 6)]
    df = df.drop_duplicates(subset=["ticker"]).sort_values("ticker").reset_index(drop=True)
    return df


def get_tickers() -> list[str]:
    return get_ticker_frame()["ticker"].tolist()


if __name__ == "__main__":
    df = get_ticker_frame()
    print(f"Total tickers: {len(df)}")
    print(df.head(5).to_string(index=False))
