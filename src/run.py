"""Daily batch: fetch OHLCV, compute raw features, save to CSV.

Tier/score depend on user-tunable thresholds, so those are computed live in the
dashboard. This batch only needs to produce the raw features.
"""
from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

from src.indicators import compute_features
from src.tickers import get_ticker_frame
from src.translate import translate_summaries

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
RESULTS = DATA_DIR / "results.csv"
META = DATA_DIR / "meta.json"
BATCH_SIZE = 50
# Fail the job (exit 1 → GitHub Actions sends email) if fewer than this ratio
# of tickers produced features. Guards against silent yfinance outages.
MIN_SUCCESS_RATIO = 0.5


def _flatten(df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    if df.columns.nlevels > 1:
        if ticker in df.columns.get_level_values(0):
            df = df[ticker]
        else:
            df.columns = df.columns.get_level_values(0)
    return df


def analyze(ticker: str, raw: pd.DataFrame) -> dict | None:
    df = _flatten(raw, ticker).dropna()
    if len(df) < 220:
        return None
    feats = compute_features(df)
    if not feats:
        return None
    return {"ticker": ticker, **{k: round(v, 3) if isinstance(v, float) else v for k, v in feats.items()}}


def _fetch_info(ticker: str) -> tuple[str, dict]:
    """Fetch shares + extended company info from yf.Ticker.info."""
    empty = {"shares": None, "sector": None, "industry": None, "summary": None, "website": None, "employees": None}
    try:
        info = yf.Ticker(ticker).info
        return ticker, {
            "shares": info.get("sharesOutstanding") or info.get("impliedSharesOutstanding"),
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "summary": info.get("longBusinessSummary"),
            "website": info.get("website"),
            "employees": info.get("fullTimeEmployees"),
        }
    except Exception:
        return ticker, empty


def fetch_info_bulk(tickers: list[str], workers: int = 20) -> dict[str, dict]:
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return dict(ex.map(_fetch_info, tickers))


def run(tickers_df: pd.DataFrame | None = None, period: str = "2y") -> pd.DataFrame:
    if tickers_df is None:
        tickers_df = get_ticker_frame()
    tickers = tickers_df["ticker"].tolist()
    print(f"[{datetime.now(timezone.utc).isoformat()}] screening {len(tickers)} tickers")

    rows: list[dict] = []
    failures: list[str] = []
    for i in range(0, len(tickers), BATCH_SIZE):
        batch = tickers[i : i + BATCH_SIZE]
        data = yf.download(batch, period=period, progress=False, auto_adjust=True, group_by="ticker", threads=True)
        for t in batch:
            try:
                row = analyze(t, data)
                if row:
                    rows.append(row)
                else:
                    failures.append(t)
            except Exception as exc:
                failures.append(t)
                print(f"  ! {t}: {exc}", file=sys.stderr)
        print(f"  batch {i // BATCH_SIZE + 1}: {i + len(batch)}/{len(tickers)}")
        time.sleep(1)

    success_ratio = len(rows) / len(tickers) if tickers else 0
    print(f"  analyzed: {len(rows)}/{len(tickers)} ({success_ratio:.1%}), skipped/failed: {len(failures)}")
    if success_ratio < MIN_SUCCESS_RATIO:
        print(
            f"[ERROR] success ratio {success_ratio:.1%} below {MIN_SUCCESS_RATIO:.0%} — "
            f"aborting to preserve prior results.csv",
            file=sys.stderr,
        )
        sys.exit(1)

    result = pd.DataFrame(rows)

    result = result.merge(tickers_df, on="ticker", how="left")

    print(f"  fetching company info for {len(result)} tickers...")
    t0 = time.time()
    info_map = fetch_info_bulk(result["ticker"].tolist())
    result["shares"] = result["ticker"].map(lambda t: info_map.get(t, {}).get("shares"))
    result["market_cap"] = (result["shares"] * result["price"]).round(0)
    result["sector"] = result["ticker"].map(lambda t: info_map.get(t, {}).get("sector"))
    result["industry"] = result["ticker"].map(lambda t: info_map.get(t, {}).get("industry"))
    result["website"] = result["ticker"].map(lambda t: info_map.get(t, {}).get("website"))
    result["employees"] = result["ticker"].map(lambda t: info_map.get(t, {}).get("employees"))
    print(f"  company info fetched in {time.time() - t0:.1f}s")

    # Translate summaries and store in data/summaries_ko.json (not in CSV)
    print("  translating summaries...")
    translate_summaries(info_map)

    cols = ["ticker", "name", "market_cap", "sector", "industry", "website", "employees"] + [
        c for c in result.columns
        if c not in ("ticker", "name", "market_cap", "sector", "industry", "website", "employees")
    ]
    result = result[cols].sort_values("disparity_200").reset_index(drop=True)

    DATA_DIR.mkdir(exist_ok=True)
    result.to_csv(RESULTS, index=False, encoding="utf-8")

    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ticker_count": len(tickers),
        "result_count": len(result),
    }
    META.write_text(pd.Series(meta).to_json(indent=2), encoding="utf-8")
    print(f"saved {RESULTS} ({len(result)} rows) | meta: {meta}")
    return result


if __name__ == "__main__":
    run()
