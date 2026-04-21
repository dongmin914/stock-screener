# CLAUDE.md

## 에이전트 역할 분담
- **설계/기획/아키텍처** → Opus
- **구현/코딩/반복 작업** → Sonnet 서브에이전트 위임
- Sonnet은 상위 Opus를 역호출하지 않음. 설계 변경 필요시 Opus에 보고만.

## Commands
```bash
pip install -r requirements.txt
python -m src.run         # 일일 배치 → data/results.csv + meta.json
streamlit run app.py      # 대시보드
python -m src.tickers     # 티커 스크래핑 점검
```
테스트/린터 없음. 배치가 통합 테스트 역할 (5~7분, `analyzed ≥50%` 미달 시 exit 1).

## Architecture

**2단계 파이프라인 — 임계값 분리 구조.**
배치는 raw features만 계산, 대시보드에서 사용자 슬라이더로 임계값 적용 → 재실행 없이 튜닝 가능.

## 모듈별 역할

| 파일 | 역할 |
|---|---|
| `indicators.py` | Raw 측정값(RSI, disparity) + 고정로직 booleans(BB/Volume/Ichimoku). NaN 시 `{}` 반환. Ichimoku span은 `.shift(26)` (TradingView 호환) |
| `screener.py` | 10점 gradient 스코어. `ENTRY_MIN=7.5`, `WATCH_MIN=5.0`. disparity/volume은 부분 점수 |
| `run.py` | `yf.download(group_by="ticker")`. `MIN_SUCCESS_RATIO=0.5` 미달 시 exit 1 (이전 CSV 보존). `ohlcv_cache` 유지 → backtest 활용 |
| `backtest.py` | 티커별 과거 1년 승률. 진입 조건 충족일 기준 30일 후 5% 상승 비율. `compute_win_rate(df)` → `(win_rate_pct, event_count)`. `MIN_EVENTS=3` 미만 시 None 반환 |
| `translate.py` | deep-translator + MD5[:12] 디스크 캐시. 연속 3회 실패 시 영어 폴백 |
| `fx.py` | `@st.cache_data(ttl=3600)`. meta.json 폴백, `DEFAULT_RATE=1380` |
| `sector_ko.py` | 섹터 11개 + 업종 ~126개 한글 매핑. 미매핑은 영어 폴스루 |
| `format.py` | `$3.4T (4,756조)` 병기 포맷 |
| `app.py` | 차트 상단 + 테이블 하단. 슬라이더 변경 시 stale 컬럼 drop 후 재스코어. 신호 카드(`_render_signal_card`) 차트 위에 표시 |

## CSV 컬럼 (results.csv)
- 기존 컬럼 외 추가: `win_rate` (float, None=데이터 부족), `win_events` (int, 과거 1년 신호 발생 횟수)

## 핵심 규칙

- **임계값 기반 boolean은 indicators가 아닌 screener에** 둘 것
- `summary`는 CSV에 넣지 않고 `summaries_ko.json`에 티커 키로 저장 (따옴표 이슈)
- `data/summaries_ko.json`은 현재 GitHub Actions 커밋 대상 아님 → 영속화 필요 시 추가

## 배포
- **대시보드**: Streamlit Community Cloud
- **배치**: `.github/workflows/daily-scan.yml`, cron `30 22 * * 1-5` (미국장 마감 30분 후)

## UI 컨벤션 (한국어)
- 티어 라벨: `본진입 후보 / 분할 매수 관심 / 조건 미충족`
- 사용자는 **구름대(cloud)만** 보고 Ichimoku 판단 → 기본 모드 **B (가격 구름대 아래)**
- Ichimoku 관련 제안 시 cloud 기반 조건 우선

## AI Analysis (Gemini)
- `src/analysis.py` — Gemini-powered per-ticker analysis, cached per-day per-ichimoku-mode in `data/analysis_cache.json`
- Requires `GEMINI_API_KEY` in Streamlit secrets or env var. Free tier: ~1000 req/day
- Session limit 30 requests to avoid abuse on shared URL
- Uses `gemini-2.5-flash` with Korean system prompt + streaming response