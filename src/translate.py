"""Translate English company summaries to Korean, with MD5-keyed disk cache."""
from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CACHE_FILE = DATA_DIR / "summaries_ko.json"

logger = logging.getLogger(__name__)


def load_cache() -> dict:
    """Read data/summaries_ko.json → {ticker: {"hash": ..., "ko": ..., "translated_at": ...}}."""
    try:
        if CACHE_FILE.exists():
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def save_cache(cache: dict) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")


def translate_summaries(info_dict: dict[str, dict]) -> dict[str, str]:
    """Translate English summaries to Korean.

    Returns {ticker: korean_or_english_summary}.
    Uses disk cache keyed by MD5 hash; calls GoogleTranslator only for new/changed summaries.
    Falls back to English after 3 consecutive translation failures.
    """
    from deep_translator import GoogleTranslator  # lazy import — not available in batch-less deploys

    cache = load_cache()
    results: dict[str, str] = {}
    new_count = 0
    hit_count = 0
    consecutive_failures = 0
    use_english_fallback = False
    # Incremental persistence: Ctrl+C or network drop mid-batch must not lose work.
    PROGRESS_EVERY = 10
    SAVE_EVERY = 50
    translator = GoogleTranslator(source="en", target="ko")

    items = list(info_dict.items())
    total_to_try = sum(1 for _, info in items if info and info.get("summary"))
    processed = 0

    for ticker, info in items:
        summary = info.get("summary") if info else None
        if not summary:
            continue

        processed += 1
        text_hash = hashlib.md5(summary.encode("utf-8")).hexdigest()[:12]
        cached = cache.get(ticker, {})

        if cached.get("hash") == text_hash and cached.get("ko"):
            # Cache hit — no API call needed
            results[ticker] = cached["ko"]
            hit_count += 1
        elif use_english_fallback:
            # Store English in cache so dashboard shows *something* and next run can retry Korean
            cache[ticker] = {"hash": text_hash, "ko": None, "en": summary, "translated_at": None}
            results[ticker] = summary
        else:
            try:
                ko = translator.translate(summary)
                cache[ticker] = {
                    "hash": text_hash,
                    "ko": ko,
                    "en": summary,
                    "translated_at": datetime.now(timezone.utc).date().isoformat(),
                }
                results[ticker] = ko
                new_count += 1
                consecutive_failures = 0
                time.sleep(0.15)  # gentle pacing to avoid Google rate-limit circuit break
            except Exception as exc:
                logger.warning("번역 실패 %s: %s", ticker, exc)
                consecutive_failures += 1
                # Save English as fallback so dashboard isn't blank; retry Korean on next run
                cache[ticker] = {"hash": text_hash, "ko": None, "en": summary, "translated_at": None}
                results[ticker] = summary
                if consecutive_failures >= 3:
                    print("  ! 번역 연속 3회 실패 — 나머지는 영어로 폴백합니다.")
                    use_english_fallback = True

        if processed % PROGRESS_EVERY == 0:
            print(f"  번역 진행: {processed}/{total_to_try} (신규 {new_count}, 캐시 {hit_count})")
        if processed % SAVE_EVERY == 0:
            save_cache(cache)

    save_cache(cache)
    total = new_count + hit_count
    logger.info("번역: %d/%d 신규 (%d 캐시 히트)", new_count, total, hit_count)
    print(f"번역: {new_count}/{total} 신규 ({hit_count} 캐시 히트)")
    return results
