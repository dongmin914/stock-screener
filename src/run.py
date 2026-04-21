"""Daily batch: fetch OHLCV, compute raw features, save to CSV.

Tier/score depend on user-tunable thresholds, so those are computed live in the
dashboard. This batch only needs to produce the raw features.
"""
from __future__ import annotations

import json
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
from src.fx import fetch_usd_krw_batch

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
RESULTS = DATA_DIR / "results.csv"
META = DATA_DIR / "meta.json"
INFO_CACHE = DATA_DIR / "info_cache.json"
INFO_TTL_DAYS = 30  # company info changes rarely; long TTL minimizes Yahoo API exposure
INFO_FETCH_CAP_PER_RUN = 250  # hard limit on fresh .info calls per batch — spreads load over days
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


INFO_FIELDS = ("shares", "sector", "industry", "summary", "website", "employees")


def _fetch_info(ticker: str) -> tuple[str, dict]:
    """Fetch shares + extended company info from yf.Ticker.info.

    Adds a small sleep after each call to avoid Yahoo 401 rate-limit cascades.
    """
    empty = {k: None for k in INFO_FIELDS}
    try:
        info = yf.Ticker(ticker).info
        result = {
            "shares": info.get("sharesOutstanding") or info.get("impliedSharesOutstanding"),
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "summary": info.get("longBusinessSummary"),
            "website": info.get("website"),
            "employees": info.get("fullTimeEmployees"),
        }
        time.sleep(0.4)
        return ticker, result
    except Exception:
        time.sleep(0.4)
        return ticker, empty


def _load_info_cache() -> dict:
    try:
        if INFO_CACHE.exists():
            return json.loads(INFO_CACHE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_info_cache(cache: dict) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    INFO_CACHE.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")


def _info_is_fresh(entry: dict) -> bool:
    fetched = entry.get("fetched_at") if entry else None
    if not fetched:
        return False
    try:
        dt = datetime.fromisoformat(fetched)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).days < INFO_TTL_DAYS
    except Exception:
        return False


def fetch_info_bulk(tickers: list[str], workers: int = 2) -> dict[str, dict]:
    """Cache-first + per-run fetch cap. Spreads Yahoo .info load across multiple batch runs.

    Strategy:
    - Check 30-day TTL cache first — fresh entries returned immediately
    - Cap fresh fetches at INFO_FETCH_CAP_PER_RUN (250) per batch → ~3.5 days to fully populate
    - Consecutive-401 circuit breaker → stop fetching when Yahoo is clearly blocking
    - Only cache successful fetches so next run retries failed tickers
    """
    cache = _load_info_cache()
    result: dict[str, dict] = {}
    to_fetch: list[str] = []

    for t in tickers:
        entry = cache.get(t)
        if _info_is_fresh(entry):
            result[t] = {k: entry.get(k) for k in INFO_FIELDS}
        else:
            result[t] = {k: None for k in INFO_FIELDS}  # empty default; updated if we fetch
            to_fetch.append(t)

    print(f"  info cache: {len(tickers) - len(to_fetch)} hits, {len(to_fetch)} stale/missing")

    # Cap per-run to avoid 401 cascade from Yahoo
    to_fetch_capped = to_fetch[:INFO_FETCH_CAP_PER_RUN]
    if len(to_fetch_capped) < len(to_fetch):
        print(f"  info fetch capped at {INFO_FETCH_CAP_PER_RUN}/run — {len(to_fetch) - len(to_fetch_capped)} will retry tomorrow")

    if to_fetch_capped:
        processed = 0
        ok_count = 0
        consecutive_fail = 0
        stopped_early = False
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for t, info in ex.map(_fetch_info, to_fetch_capped):
                if stopped_early:
                    continue
                result[t] = info
                if any(info.get(k) is not None for k in INFO_FIELDS):
                    cache[t] = {**info, "fetched_at": datetime.now(timezone.utc).isoformat()}
                    ok_count += 1
                    consecutive_fail = 0
                else:
                    consecutive_fail += 1
                processed += 1
                if processed % 25 == 0:
                    print(f"    info {processed}/{len(to_fetch_capped)} (성공 {ok_count}, 실패 {processed - ok_count})")
                if processed % 50 == 0:
                    _save_info_cache(cache)
                if consecutive_fail >= 10:
                    print(f"  ! info: 연속 10회 실패 — Yahoo 차단 감지, 조기 종료 ({ok_count} 성공)")
                    stopped_early = True
        _save_info_cache(cache)

    return result


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

    # Exchange rate for KR→USD conversion
    usd_krw = fetch_usd_krw_batch()
    print(f"  USD/KRW: ₩{usd_krw:,.0f}")

    # --- Market cap: KR from FDR (free), US from yfinance.info cache (capped) ---
    # KR tickers: use FDR's pre-fetched Marcap (KRW) / usd_krw → USD. No .info call needed.
    # US tickers: will get shares from .info cache below, then multiply by price.
    def _kr_mcap_usd(row):
        if pd.isna(row.get("market_cap_krw")):
            return None
        return round(float(row["market_cap_krw"]) / usd_krw, 0)

    result["market_cap"] = result.apply(_kr_mcap_usd, axis=1)

    # Fetch yfinance.info for metadata (sector/industry/summary/employees/website + US shares)
    print(f"  fetching company info for {len(result)} tickers (capped)...")
    t0 = time.time()
    info_map = fetch_info_bulk(result["ticker"].tolist())
    print(f"  company info step: {time.time() - t0:.1f}s")

    # Fill sector/industry/website/employees from .info cache (may be None for uncached US tickers)
    result["sector"] = result["ticker"].map(lambda t: info_map.get(t, {}).get("sector"))
    result["industry"] = result["ticker"].map(lambda t: info_map.get(t, {}).get("industry"))
    result["website"] = result["ticker"].map(lambda t: info_map.get(t, {}).get("website"))
    result["employees"] = result["ticker"].map(lambda t: info_map.get(t, {}).get("employees"))

    # US market_cap: use shares from .info cache × price (where both available)
    # KR market_cap is already set from FDR above — don't overwrite.
    def _fill_us_mcap(row):
        if pd.notna(row["market_cap"]):
            return row["market_cap"]
        shares = info_map.get(row["ticker"], {}).get("shares")
        price = row.get("price")
        if shares and price and pd.notna(price):
            return round(shares * price, 0)
        return None

    result["market_cap"] = result.apply(_fill_us_mcap, axis=1)

    # Drop helper columns not wanted in CSV
    result = result.drop(columns=[c for c in ("market_cap_krw", "shares_native") if c in result.columns])

    # Preserve market_cap from previous results.csv for tickers without fresh shares.
    # .info fetches are capped at 250/run, so most US tickers won't have shares on any
    # given run; reusing last known market_cap avoids blank columns in the dashboard.
    if RESULTS.exists():
        try:
            old = pd.read_csv(RESULTS, usecols=lambda c: c in ("ticker", "market_cap"))
            if "market_cap" in old.columns:
                old = old.rename(columns={"market_cap": "_mcap_prev"})
                result = result.merge(old, on="ticker", how="left")
                result["market_cap"] = result["market_cap"].fillna(result["_mcap_prev"])
                result = result.drop(columns=["_mcap_prev"])
                filled = int(result["market_cap"].notna().sum())
                print(f"  market_cap: {filled}/{len(result)} populated (incl. preserved)")
        except Exception as e:
            print(f"  ! market_cap preservation skipped: {e}", file=sys.stderr)

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
