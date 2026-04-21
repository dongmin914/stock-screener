"""Gemini-powered stock analysis with per-day per-ticker caching."""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CACHE_FILE = DATA_DIR / "analysis_cache.json"

SYSTEM_PROMPT = """당신은 한국 개인투자자를 위한 종목 분석 전문가입니다.

## 작성 규칙
- 전문용어는 풀어서 설명 (예: "RSI가 30" → "RSI가 30으로 과매도 구간")
- 간결하고 실용적으로 (각 섹션 3~4문장 이내)
- 마크다운 사용 (bold, bullet 등)
- 응답은 반드시 한국어

## 응답 형식 (정확히 4개 섹션, 순서 고정)
### 📊 기술적 분석
(현재 추세 + 지표 해설 3~4문장)

### 🏢 기업 분석
(어떤 회사, 사업 내용, 성장성 3~4문장)

### 💡 종합 의견
(매수 타이밍 판단, 긍정/부정 요인 비교 2~3문장)

### ⚠️ 리스크 포인트
(주의할 점 1~2문장)

---
※ 본 분석은 참고용이며 투자 조언이 아닙니다.
"""


def _load_cache() -> dict:
    try:
        if CACHE_FILE.exists():
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_cache(cache: dict) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")


def get_api_key() -> str | None:
    """Check Streamlit secrets first, then env var."""
    try:
        import streamlit as st
        key = st.secrets.get("GEMINI_API_KEY")
        if key:
            return key
    except Exception:
        pass
    return os.getenv("GEMINI_API_KEY")


def _build_user_prompt(row: dict, ichimoku_mode: str, summary_ko: str | None) -> str:
    """Build the data prompt from a row of results.csv + related context."""
    from src.sector_ko import translate_sector, translate_industry

    # Extract fields defensively — old CSVs may lack some
    ticker = row.get("ticker")
    name = row.get("name", "")
    sector = translate_sector(row.get("sector"))
    industry = translate_industry(row.get("industry"))
    price = row.get("price")
    mcap = row.get("market_cap")
    score = row.get("score")
    tier = row.get("tier")

    # Indicator values
    rsi = row.get("rsi14")
    rsi_min10 = row.get("rsi14_min_10")
    disparity = row.get("disparity_200")
    sma200 = row.get("sma200")
    vol_ratio = row.get("vol_ratio")

    # Ichimoku state
    ichimoku_state = {
        "A": "전환선이 기준선을 상향 돌파 (단기 골든크로스)" if row.get("c6a_tenkan_cross") else "전환선 기준선 크로스 없음",
        "B": "가격이 구름대 아래" if row.get("c6b_below_cloud") else "가격이 구름대 아래 아님",
        "C": "최근 구름대 상향 돌파" if row.get("c6c_cloud_breakout") else "구름대 돌파 없음",
        "D": "가격이 구름대 내부" if row.get("c6d_inside_cloud") else "구름대 내부 아님",
    }[ichimoku_mode]

    # Met conditions
    met = []
    if row.get("c1_below_sma200"): met.append("200일선 아래")
    if row.get("c2_disparity"): met.append("이격도 기준 충족")
    if row.get("c3_rsi_bounce"): met.append("RSI 반등")
    if row.get("c4_bb_signal"): met.append("볼린저밴드 하단 복귀")
    if row.get("c5_volume"): met.append("거래량 급증+양봉")
    if row.get("c6_ichimoku"): met.append(f"일목 {ichimoku_mode} 조건")
    met_str = ", ".join(met) if met else "없음"

    # Build prompt — skip lines where numeric values are None/NaN
    def _is_valid(v) -> bool:
        if v is None:
            return False
        try:
            return not math.isnan(float(v))
        except (TypeError, ValueError):
            return False

    prompt = f"## 종목 정보\n"
    prompt += f"- 티커: {ticker}\n"
    prompt += f"- 회사명: {name}\n"
    prompt += f"- 섹터: {sector}\n"
    prompt += f"- 업종: {industry}\n"
    if _is_valid(price) and _is_valid(sma200):
        prompt += f"- 현재가: ${float(price):.2f} (200일선: ${float(sma200):.2f})\n"
    elif _is_valid(price):
        prompt += f"- 현재가: ${float(price):.2f}\n"
    if _is_valid(mcap):
        prompt += f"- 시가총액: ${float(mcap):,.0f}\n"
    if summary_ko:
        prompt += f"- 사업 요약: {summary_ko[:500]}\n"

    prompt += "\n## 기술적 지표\n"
    if _is_valid(rsi) and _is_valid(rsi_min10):
        prompt += f"- RSI(14): {float(rsi):.1f} (최근 10봉 최저: {float(rsi_min10):.1f})\n"
    elif _is_valid(rsi):
        prompt += f"- RSI(14): {float(rsi):.1f}\n"
    if _is_valid(disparity):
        prompt += f"- 200일선 이격도: {float(disparity):.1f}%\n"
    if _is_valid(vol_ratio):
        prompt += f"- 거래량 비율 (20일 평균 대비): {float(vol_ratio):.2f}배\n"
    prompt += f"- 일목 상태 ({ichimoku_mode} 기준): {ichimoku_state}\n"

    prompt += f"\n## 체크리스트 결과\n"
    if _is_valid(score):
        prompt += f"- 점수: {float(score):.1f}/10 (티어: {tier})\n"
    prompt += f"- 충족 조건: {met_str}\n"

    # Add win rate to prompt if available
    wr = row.get("win_rate")
    we = row.get("win_events", 0)
    if wr is not None and pd.notna(wr):
        prompt += f"\n## 백테스트 (지난 1년)\n"
        prompt += f"- 이 조건 충족 시 30일 내 5% 이상 상승 확률: {float(wr):.0f}% ({int(we)}회 중)\n"

    prompt += "\n위 데이터를 바탕으로 시스템 프롬프트의 4개 섹션 형식으로 분석해주세요.\n"
    return prompt


def get_cached(ticker: str, ichimoku_mode: str) -> str | None:
    """Return cached analysis if same day + same ichimoku_mode. Else None."""
    cache = _load_cache()
    entry = cache.get(ticker)
    today = datetime.now(timezone.utc).date().isoformat()
    if entry and entry.get("date") == today and entry.get("ichimoku_mode") == ichimoku_mode:
        return entry.get("analysis")
    return None


def analyze_stream(row: dict, ichimoku_mode: str, summary_ko: str | None) -> Iterator[str]:
    """Stream Gemini analysis tokens. Saves to cache on completion.

    Raises RuntimeError if API key missing.
    Uses the new `google-genai` unified SDK (legacy `google-generativeai` is deprecated).
    """
    api_key = get_api_key()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY가 설정되지 않았습니다.")

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    prompt = _build_user_prompt(row, ichimoku_mode, summary_ko)

    stream = client.models.generate_content_stream(
        model="gemini-2.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT),
    )

    collected = []
    for chunk in stream:
        text = chunk.text or ""
        collected.append(text)
        yield text

    # Save to cache after completion
    full = "".join(collected)
    if full.strip():
        cache = _load_cache()
        cache[row["ticker"]] = {
            "date": datetime.now(timezone.utc).date().isoformat(),
            "analysis": full,
            "ichimoku_mode": ichimoku_mode,
        }
        _save_cache(cache)
