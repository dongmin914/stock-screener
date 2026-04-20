"""Streamlit dashboard — two-column layout: chart (left) + clickable table (right)."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from src.screener import ICHIMOKU_MODES, TIER_KO, score
from src.fx import get_usd_krw
from src.format import format_market_cap
from src.sector_ko import translate_sector, translate_industry

DATA = Path(__file__).parent / "data"
RESULTS = DATA / "results.csv"
META = DATA / "meta.json"
SUMMARIES = DATA / "summaries_ko.json"

TIER_ICON = {"entry": "🟢", "watch": "🟡", "pass": "⚪"}
COND_SHORT = {
    "c1_below_sma200": "200MA↓",
    "c2_disparity": "이격",
    "c3_rsi_bounce": "RSI반등",
    "c4_bb_signal": "볼밴",
    "c5_volume": "거래량",
    "c6_ichimoku": "일목",
}

st.set_page_config(page_title="Stock Screener", layout="wide")

# Tighten vertical spacing so chart + table fit without scroll on typical laptops.
st.markdown(
    """
    <style>
      .block-container { padding-top: 1.5rem; padding-bottom: 1rem; }
      [data-testid="stMetricValue"] { font-size: 1.4rem; }
      [data-testid="stVerticalBlock"] { gap: 0.2rem !important; }
      [data-testid="stVerticalBlock"] > div { gap: 0.2rem !important; }
      [data-testid="stElementContainer"] { margin-bottom: 0 !important; }
      iframe { display: block; margin: 0 !important; }
      h3 { margin-top: 0.3rem !important; margin-bottom: 0.2rem !important; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("📈 나의 매수 타이밍 스크리너")

if not RESULTS.exists():
    st.warning("아직 데이터가 없어요. 터미널에서 `python -m src.run` 먼저 실행하세요.")
    st.stop()

df = pd.read_csv(RESULTS)
meta = json.loads(META.read_text(encoding="utf-8")) if META.exists() else {}


@st.cache_data(ttl=300)
def load_summary_cache() -> dict:
    """Read data/summaries_ko.json → {ticker: {"hash": ..., "ko": ...}}."""
    try:
        if SUMMARIES.exists():
            return json.loads(SUMMARIES.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


# Fetch exchange rate once at top; used for sidebar filters and table formatting
usd_krw, is_fresh = get_usd_krw()

# --- Sidebar ---
with st.sidebar:
    st.header("🔍 필터")

    if not is_fresh:
        st.caption(f"⚠️ 환율 폴백: ₩{usd_krw:,.0f}")
    else:
        st.caption(f"환율: ₩{usd_krw:,.0f}")

    tier_options = ["본진입 후보", "분할 매수 관심", "조건 미충족"]
    tiers = st.multiselect("티어", tier_options, default=["본진입 후보", "분할 매수 관심"])

    # 1조 = 1e12 KRW; boundaries stored in 조 units, compared against market_cap (USD)
    mcap_preset = st.selectbox(
        "시가총액",
        ["전체", "대형주 (140조+)", "중대형주 (14조+)", "중형주 (2.8조+)", "소형주만 (2.8조 미만)", "직접 지정"],
    )
    if mcap_preset == "직접 지정":
        mcap_min_jo = st.number_input("최소 시총 (조)", min_value=0.0, value=0.0, step=1.0)
        mcap_max_jo = st.number_input("최대 시총 (조)", min_value=0.0, value=10000.0, step=1.0)
        # Convert 조 → USD
        mcap_min_usd = mcap_min_jo * 1e12 / usd_krw
        mcap_max_usd = mcap_max_jo * 1e12 / usd_krw
    else:
        # Preset KRW boundaries (조 units) → convert to USD live
        _presets_jo = {
            "전체": (0, 1e9),
            "대형주 (140조+)": (140, 1e9),
            "중대형주 (14조+)": (14, 1e9),
            "중형주 (2.8조+)": (2.8, 1e9),
            "소형주만 (2.8조 미만)": (0, 2.8),
        }
        min_jo, max_jo = _presets_jo[mcap_preset]
        mcap_min_usd = min_jo * 1e12 / usd_krw
        mcap_max_usd = max_jo * 1e12 / usd_krw

    search = st.text_input("티커 / 회사명 검색").strip()

    st.divider()
    st.header("⚙️ 기준값 조정")
    min_score = st.slider("최소 점수", 0.0, 10.0, 5.0, 0.5, help="10점 만점 · 본진입 ≥7.5, 분할 매수 ≥5.0")
    rsi_th = st.slider("RSI 과매도 기준", 10, 50, 30, help="이 값 이하에 도달 후 반등하면 조건 충족")
    disp_th = st.slider("이격도 기준 (%)", -40, 0, -10, help="200일선 대비 이 % 이하면 조건 충족")

    ichimoku_labels = {
        "A": "A · 전환>기준 돌파 (단기 골든크로스)",
        "B": "B · 가격 구름대 아래 (저점 매수존)",
        "C": "C · 구름대 상향 돌파 (추세 전환 초기)",
        "D": "D · 가격 구름대 내부 (추세 전환 진행)",
    }
    ichimoku_mode = st.radio(
        "일목 조건",
        list(ichimoku_labels.keys()),
        index=1,  # default to B (cloud-only style)
        format_func=lambda k: ichimoku_labels[k],
    )

    st.divider()
    st.header("📐 차트 설정")
    tf_label = st.radio("시간프레임", ["주봉", "일봉", "월봉"], horizontal=True)
    interval_map = {"주봉": "W", "일봉": "D", "월봉": "M"}
    tf = interval_map[tf_label]

    st.link_button("🗺️ Finviz 섹터 히트맵", "https://finviz.com/map.ashx?t=sec", use_container_width=True)

# --- Live re-score ---
def _compute(row):
    s = score(row.to_dict(), rsi_th, disp_th, ichimoku_mode)
    return pd.Series({
        "score": s["score"],
        "tier": s["tier"],
        "c2_disparity": s["c2_disparity"],
        "c3_rsi_bounce": s["c3_rsi_bounce"],
        "c6_ichimoku": s["c6_ichimoku"],
    })

new_cols = df.apply(_compute, axis=1)
df = df.drop(columns=[c for c in new_cols.columns if c in df.columns])
df = pd.concat([df, new_cols], axis=1)
df["tier_ko"] = df["tier"].map(TIER_KO)

# --- Metrics row ---
n_entry = int((df["tier"] == "entry").sum())
n_watch = int((df["tier"] == "watch").sum())
c1, c2, c3, c4 = st.columns(4)
c1.metric("🟢 본진입", n_entry, help="체크리스트 5개 이상")
c2.metric("🟡 분할 매수 관심", n_watch, help="체크리스트 3~4개")
c3.metric("전체 스캔", meta.get("result_count", len(df)))
c4.metric("업데이트 (UTC)", (meta.get("generated_at") or "")[:16])

# --- Filter ---
view = df[df["tier_ko"].isin(tiers) & (df["score"] >= min_score)].copy()
if "market_cap" in view.columns:
    mcap_usd = view["market_cap"].fillna(0)
    view = view[(mcap_usd >= mcap_min_usd) & (mcap_usd <= mcap_max_usd)]
if search:
    q = search.upper()
    view = view[view["ticker"].str.upper().str.contains(q) | view["name"].str.upper().str.contains(q)]
view = view.sort_values(["score", "disparity_200"], ascending=[False, True]).reset_index(drop=True)

if "market_cap" in view.columns:
    view["시총"] = view["market_cap"].apply(lambda v: format_market_cap(v, usd_krw))
for key, short in COND_SHORT.items():
    view[short] = view[key].map({True: "✅", False: "·"})
view["티어"] = view["tier"].map(lambda t: f"{TIER_ICON.get(t, '·')}")

# Determine selected ticker from previous-run session state (table renders first now,
# but its selection from the prior rerun still drives chart below).
if len(view) == 0:
    selected_ticker = None
    selected_name = None
else:
    sel_state = st.session_state.get("result_table")
    sel_rows = sel_state.selection.rows if sel_state and getattr(sel_state, "selection", None) else []
    idx = sel_rows[0] if sel_rows else 0
    idx = min(idx, len(view) - 1)
    selected_ticker = view.iloc[idx]["ticker"]
    selected_name = view.iloc[idx]["name"]

# --- Table on top (full width) ---
st.markdown("### 📋 스크리닝 결과 (행 클릭 → 아래 차트 갱신)")

if len(view) > 0:
    compact_cols = ["ticker", "name"]
    if "시총" in view.columns:
        compact_cols.append("시총")
    compact_cols += ["티어", "score"] + list(COND_SHORT.values()) + ["disparity_200", "rsi14"]

    st.dataframe(
        view[compact_cols].rename(columns={
            "ticker": "티커",
            "name": "회사명",
            "score": "점수",
            "disparity_200": "이격%",
            "rsi14": "RSI",
        }),
        use_container_width=True,
        height=400,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key="result_table",
    )

# --- Chart below table (full width) ---
if selected_ticker:
    st.markdown(f"### 📊 {selected_ticker} — {selected_name}")
    chart_h = 650
    widget_html = f"""
    <!DOCTYPE html>
    <html><head><style>
      html, body {{ margin:0; padding:0; height:100%; width:100%; overflow:hidden; }}
      .tradingview-widget-container {{ height:{chart_h}px; width:100%; }}
      #tv_chart {{ height:{chart_h}px; width:100%; }}
    </style></head><body>
      <div class="tradingview-widget-container">
        <div id="tv_chart"></div>
        <script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script>
        <script type="text/javascript">
          new TradingView.widget({{
            "width": "100%",
            "height": {chart_h},
            "symbol": "{selected_ticker}",
            "interval": "{tf}",
            "timezone": "Etc/UTC",
            "theme": "light",
            "style": "1",
            "locale": "en",
            "toolbar_bg": "#f1f3f6",
            "enable_publishing": false,
            "allow_symbol_change": true,
            "hide_side_toolbar": false,
            "withdateranges": true,
            "studies": [
              "BB@tv-basicstudies",
              "IchimokuCloud@tv-basicstudies",
              "RSI@tv-basicstudies"
            ],
            "container_id": "tv_chart"
          }});
        </script>
      </div>
    </body></html>
    """
    components.html(widget_html, height=chart_h + 5, scrolling=False)

    # --- Company info expander (below chart) ---
    summary_cache = load_summary_cache()
    sel_row = view[view["ticker"] == selected_ticker].iloc[0]

    with st.expander("🏢 회사 정보", expanded=True):
        col1, col2 = st.columns([1, 1])
        col1.markdown(f"**섹터:** {translate_sector(sel_row.get('sector'))}")
        col1.markdown(f"**업종:** {translate_industry(sel_row.get('industry'))}")
        employees = sel_row.get("employees")
        if pd.notna(employees) if employees is not None else False:
            col2.markdown(f"**직원:** {int(employees):,}명")
        website = sel_row.get("website")
        if pd.notna(website) if website is not None else False:
            if website:
                col2.markdown(f"**웹사이트:** [{website}]({website})")

        cached_entry = summary_cache.get(selected_ticker, {})
        ko = cached_entry.get("ko")
        en = cached_entry.get("en")
        if ko:
            st.markdown(ko)
        elif en:
            st.caption("⚠️ 한국어 번역 대기 중 — 다음 배치에서 재시도됩니다")
            st.markdown(en)

        st.markdown("**🔗 외부 링크**")
        lcol1, lcol2, lcol3 = st.columns(3)
        lcol1.link_button("Finviz", f"https://finviz.com/quote.ashx?t={selected_ticker}", use_container_width=True)
        lcol2.link_button("Stock Analysis", f"https://stockanalysis.com/stocks/{selected_ticker.lower()}/", use_container_width=True)
        lcol3.link_button("Yahoo Finance", f"https://finance.yahoo.com/quote/{selected_ticker}", use_container_width=True)

else:
    st.info("필터 조건에 맞는 종목이 없어요. 사이드바에서 기준을 완화하세요.")

with st.expander("체크리스트 설명 / 점수 기준 (10점 만점)"):
    st.markdown(
        f"""
        현재 기준: **RSI ≤ {rsi_th}**, **이격도 ≤ {disp_th}%**, **일목 {ichimoku_mode}**

        | 조건 | 배점 | 채점 |
        |------|------|------|
        | 200MA↓ | 1.0 | 200일선 아래면 1.0 |
        | 이격 | 2.5 | `min(\|이격\|/\|기준\|, 1.0) × 2.5` — 기준 도달 시 만점 |
        | RSI반등 | 1.5 | 최근 10봉 내 RSI≤기준 경험 후 현재 반등 중 |
        | 볼밴 | 1.5 | 최근 5봉 내 하단 이탈, 현재 복귀 |
        | 거래량 | 1.5 | ≥1.5배 + 양봉 = 만점 / ≥1.0배 = 절반 |
        | 일목 ({ichimoku_mode}) | 2.0 | {ICHIMOKU_MODES[ichimoku_mode][0]} |

        - 🟢 **본진입 후보**: **7.5점 이상**
        - 🟡 **분할 매수 관심**: **5.0 이상 7.5 미만**
        - ⚪ **조건 미충족**: 5.0 미만

        **일목 옵션**:
        - A · 전환>기준 돌파 (최근 3봉 내 단기 골든크로스)
        - B · 가격 < min(스팬1, 스팬2) — 구름대 아래 약세 구간
        - C · 최근 5봉 내 구름대 아래였다가 현재 상단 돌파
        - D · min(스팬) ≤ 가격 ≤ max(스팬) — 구름대 내부 (추세전환 진행)
        """
    )
