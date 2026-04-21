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
from src.analysis import analyze_stream, get_cached, get_api_key

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

BEGINNER_PRESETS = {
    "안정형": {"min_score": 7.5, "rsi_th": 25, "disp_th": -15, "ichimoku_mode": "B"},
    "균형형": {"min_score": 6.0, "rsi_th": 30, "disp_th": -10, "ichimoku_mode": "B"},
    "공격형": {"min_score": 4.5, "rsi_th": 35, "disp_th": -5,  "ichimoku_mode": "C"},
}

st.set_page_config(page_title="Stock Recommender", layout="wide")

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

st.title("📈 나의 매수 타이밍 추천")

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
    # Mode toggle — first widget in sidebar
    mode = st.radio(
        "👤 모드",
        ["쩨뿡이용", "기본"],
        index=0,
        horizontal=True,
        help="쩨뿡이용: 단순화된 Top 10 카드 / 기본: 전체 필터 + 테이블",
    )
    st.divider()

    if not is_fresh:
        st.caption(f"⚠️ 환율 폴백: ₩{usd_krw:,.0f}")
    else:
        st.caption(f"환율: ₩{usd_krw:,.0f}")

    if mode == "쩨뿡이용":
        # --- 쩨뿡이용 sidebar ---
        investor_type = st.radio(
            "🎯 투자 성향",
            ["안정형", "균형형", "공격형"],
            index=0,
        )
        preset = BEGINNER_PRESETS[investor_type]
        min_score = preset["min_score"]
        rsi_th = preset["rsi_th"]
        disp_th = preset["disp_th"]
        ichimoku_mode = preset["ichimoku_mode"]
        tf = "W"  # 주봉 fixed for 쩨뿡이용

        mcap_choice = st.selectbox("💰 규모", ["대형주만", "전체"])
        # 대형주만 → $10B equivalent (≈14조 KRW)
        if mcap_choice == "대형주만":
            mcap_min_usd = 14e12 / usd_krw
        else:
            mcap_min_usd = 0.0
        mcap_max_usd = 1e18  # no upper bound

        search = st.text_input("🔍 검색", placeholder="티커 / 회사명").strip()

        # Tier includes all non-pass; filtered by min_score below
        tiers = ["본진입 후보", "분할 매수 관심", "조건 미충족"]

        st.divider()
        st.link_button("🗺️ Finviz 섹터 히트맵", "https://finviz.com/map.ashx?t=sec", use_container_width=True)

    else:
        # --- 기본 sidebar (unchanged) ---
        st.header("🔍 필터")

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
            mcap_min_usd = mcap_min_jo * 1e12 / usd_krw
            mcap_max_usd = mcap_max_jo * 1e12 / usd_krw
        else:
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
            index=1,
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


# --- Shared: chart + company info ---
def _render_chart(selected_ticker: str, tf: str):
    """Render TradingView widget. Title and signal card are rendered by the caller."""
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
              "RSI@tv-basicstudies",
              "MASimple@tv-basicstudies"
            ],
            "studies_overrides": {{
              "Moving Average.length": 200,
              "Moving Average.plot.color": "#e91e63",
              "Moving Average.plot.linewidth": 2
            }},
            "container_id": "tv_chart"
          }});
        </script>
      </div>
    </body></html>
    """
    components.html(widget_html, height=chart_h + 5, scrolling=False)


def _render_company_info(selected_ticker: str, view: pd.DataFrame, beginner: bool = False):
    """Render company info expander. beginner=True renames expander + external link buttons."""
    summary_cache = load_summary_cache()
    sel_row = view[view["ticker"] == selected_ticker].iloc[0]

    expander_label = "📖 이 회사는 뭘 하나요?" if beginner else "🏢 회사 정보"
    with st.expander(expander_label, expanded=True):
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
        if beginner:
            lcol1.link_button("실시간 정보", f"https://finviz.com/quote.ashx?t={selected_ticker}", use_container_width=True)
            lcol2.link_button("매출/재무 구조", f"https://stockanalysis.com/stocks/{selected_ticker.lower()}/", use_container_width=True)
            lcol3.link_button("종합 정보", f"https://finance.yahoo.com/quote/{selected_ticker}", use_container_width=True)
        else:
            lcol1.link_button("Finviz", f"https://finviz.com/quote.ashx?t={selected_ticker}", use_container_width=True)
            lcol2.link_button("Stock Analysis", f"https://stockanalysis.com/stocks/{selected_ticker.lower()}/", use_container_width=True)
            lcol3.link_button("Yahoo Finance", f"https://finance.yahoo.com/quote/{selected_ticker}", use_container_width=True)


def _render_ai_analysis(selected_ticker: str, selected_name: str, ichimoku_mode: str, view: pd.DataFrame):
    """Render the AI analysis expander for the selected ticker."""
    summary_cache = load_summary_cache()

    with st.expander("🤖 AI 종목 분석 (Gemini)", expanded=False):
        if not get_api_key():
            st.warning(
                "⚠️ Gemini API 키가 설정되지 않았습니다.\n\n"
                "**설정 방법 (Streamlit Cloud)**:\n"
                "앱 페이지 우상단 ⋮ → Settings → Secrets → `GEMINI_API_KEY = \"...\"` 추가\n\n"
                "**로컬 설정**:\n"
                "`.streamlit/secrets.toml` 파일에 `GEMINI_API_KEY = \"...\"` 추가\n\n"
                "[API 키 발급 →](https://aistudio.google.com/)"
            )
        else:
            cached = get_cached(selected_ticker, ichimoku_mode)

            col_info, col_btn = st.columns([4, 1])
            col_info.caption(f"{selected_ticker} — {selected_name} / 일목 {ichimoku_mode} 기준")

            if cached:
                st.markdown(cached)
                if col_btn.button("🔄 재분석", key=f"reanalyze_{selected_ticker}"):
                    from src.analysis import _load_cache, _save_cache
                    c = _load_cache()
                    c.pop(selected_ticker, None)
                    _save_cache(c)
                    st.rerun()
            else:
                if "analysis_count" not in st.session_state:
                    st.session_state["analysis_count"] = 0
                SESSION_LIMIT = 30

                if st.session_state["analysis_count"] >= SESSION_LIMIT:
                    st.warning(f"세션 한도({SESSION_LIMIT}회) 초과. 페이지를 새로고침하면 초기화됩니다.")
                else:
                    if col_btn.button("🚀 분석 시작", key=f"analyze_{selected_ticker}", type="primary"):
                        summary_data = summary_cache.get(selected_ticker, {})
                        summary_ko = summary_data.get("ko") or summary_data.get("en")

                        sel_row = view[view["ticker"] == selected_ticker].iloc[0]

                        try:
                            with st.spinner("Gemini 분석 생성 중..."):
                                st.write_stream(
                                    analyze_stream(sel_row.to_dict(), ichimoku_mode, summary_ko)
                                )
                            st.session_state["analysis_count"] += 1
                            st.caption(f"세션 사용량: {st.session_state['analysis_count']}/{SESSION_LIMIT}")
                        except Exception as e:
                            st.error(f"분석 실패: {e}")
                    else:
                        st.caption("아래 버튼을 누르면 Gemini가 이 종목을 분석합니다. (같은 날 같은 종목은 캐시 재사용)")


def _trend(row) -> str:
    price = row.get("price")
    sma50 = row.get("sma50")
    sma200 = row.get("sma200")
    if any(pd.isna(v) for v in (price, sma50, sma200)):
        return "횡보"
    if price > sma200 and sma50 > sma200:
        return "상승"
    if price < sma200 and sma50 < sma200:
        return "하락"
    return "횡보"


def _signal(tier: str) -> tuple[str, str]:
    """Return (label, icon)."""
    return {
        "entry": ("매수", "🟢"),
        "watch": ("분할 매수", "🟡"),
        "pass": ("관망", "⚪"),
    }.get(tier, ("관망", "⚪"))


def _entry_target_stop(row) -> tuple[float | None, float | None, float | None]:
    price = row.get("price")
    bb_upper = row.get("bb_upper")
    if pd.isna(price) if price is not None else True:
        return None, None, None
    entry = float(price)
    target = max(float(bb_upper), entry * 1.10) if (bb_upper is not None and pd.notna(bb_upper)) else entry * 1.10
    stop = entry * 0.92
    return entry, target, stop


def _render_signal_card(row):
    """ChartPT-inspired signal summary card."""
    trend = _trend(row)
    signal_label, signal_icon = _signal(row.get("tier", "pass"))
    score_val = row.get("score", 0)
    win_rate = row.get("win_rate")
    win_events = row.get("win_events", 0)

    # Row 1: 추세 / 신호 / 점수 / 승률
    c1, c2, c3, c4 = st.columns(4)

    trend_icon = {"상승": "🟢", "하락": "🔴", "횡보": "⚪"}[trend]
    c1.metric("📈 추세", f"{trend_icon} {trend}")
    c2.metric("🎯 신호", f"{signal_icon} {signal_label}")
    c3.metric("⭐ 점수", f"{float(score_val):.1f}/10" if pd.notna(score_val) else "-")
    if pd.notna(win_rate) if win_rate is not None else False:
        c4.metric("🏆 승률", f"{win_rate:.0f}%", help=f"지난 1년 · 진입 신호 {int(win_events)}회")
    else:
        c4.metric("🏆 승률", "-", help="데이터 부족 (신호 3회 미만)")

    # Row 2: 진입가 / 목표가 / 손절가
    entry, target, stop = _entry_target_stop(row)
    e1, e2, e3 = st.columns(3)
    if entry:
        e1.metric("진입가", f"${entry:.2f}")
        e2.metric("목표가", f"${target:.2f}", f"+{((target / entry - 1) * 100):.1f}%")
        e3.metric("손절가", f"${stop:.2f}", f"-{((1 - stop / entry) * 100):.1f}%")

    st.markdown("---")


def _stars(score_val: float) -> str:
    if score_val >= 8.0:
        return "⭐⭐⭐⭐⭐"
    if score_val >= 6.5:
        return "⭐⭐⭐⭐"
    if score_val >= 5.0:
        return "⭐⭐⭐"
    return ""


def _build_reasons(row, ichimoku_mode: str) -> list[str]:
    reasons = []
    if row.get("c1_below_sma200"):
        reasons.append("장기 추세선 아래 저평가 구간")
    if row.get("c2_disparity", 0) > 0:
        disp = abs(row.get("disparity_200", 0))
        reasons.append(f"200일선 대비 {disp:.1f}% 할인된 가격")
    if row.get("c3_rsi_bounce"):
        reasons.append("과매도 이후 반등 초기 신호")
    if row.get("c4_bb_signal"):
        reasons.append("변동성 밴드 하단 터치 후 회복")
    if row.get("c5_volume"):
        reasons.append("거래량 급증 + 양봉 (매수세 확인)")
    if row.get("c6_ichimoku"):
        ichimoku_text = {
            "A": "단기 골든크로스 신호",
            "B": "추세 전환 대기 구간 (저점 매수존)",
            "C": "하락 추세 종료 + 상승 전환 신호",
            "D": "추세 전환 진행 중",
        }
        reasons.append(ichimoku_text.get(ichimoku_mode, ""))
    return [r for r in reasons if r]


def render_beginner(view: pd.DataFrame, usd_krw: float, ichimoku_mode: str, tf: str):
    """쩨뿡이용 mode: cards + dropdown + chart + company info."""
    st.markdown("## 🎯 오늘의 추천 종목 TOP 10")

    if len(view) == 0:
        st.info("필터 조건에 맞는 종목이 없어요. 사이드바에서 투자 성향을 변경하거나 규모 필터를 완화하세요.")
        return

    top10 = view.head(10)

    for i, (_, row) in enumerate(top10.iterrows(), start=1):
        with st.container(border=True):
            top_l, top_r = st.columns([4, 1])
            top_l.markdown(f"### {TIER_ICON[row['tier']]} [{i}] {row['name']} ({row['ticker']})")
            top_r.markdown(
                f"<div style='text-align:right;font-size:1.1rem'>{_stars(row['score'])}</div>",
                unsafe_allow_html=True,
            )

            meta_parts = []
            if pd.notna(row.get("sector")):
                meta_parts.append(translate_sector(row["sector"]))
            if pd.notna(row.get("industry")):
                meta_parts.append(translate_industry(row["industry"]))
            if pd.notna(row.get("market_cap")):
                meta_parts.append(format_market_cap(row["market_cap"], usd_krw))
            if meta_parts:
                st.caption(" · ".join(meta_parts))

            reasons = _build_reasons(row, ichimoku_mode)
            if reasons:
                st.markdown("**💡 이 종목이 추천된 이유:**")
                for r in reasons:
                    st.markdown(f"- ✓ {r}")

    st.markdown("---")

    options = top10["ticker"].tolist()
    label_map = {t: f"{t} — {n}" for t, n in zip(top10["ticker"], top10["name"])}
    selected_ticker = st.selectbox(
        "📊 차트로 볼 종목",
        options,
        format_func=lambda t: label_map[t],
        key="beginner_chart_select",
    )
    selected_name = label_map[selected_ticker].split(" — ", 1)[1]
    sel_row = view[view["ticker"] == selected_ticker].iloc[0].to_dict()

    st.markdown(f"### 📊 {selected_ticker} — {selected_name}")
    _render_signal_card(sel_row)
    _render_chart(selected_ticker, tf)
    _render_company_info(selected_ticker, view, beginner=True)
    _render_ai_analysis(selected_ticker, selected_name, ichimoku_mode, view)


def render_advanced(view: pd.DataFrame, usd_krw: float, ichimoku_mode: str, tf: str, rsi_th: int, disp_th: int):
    """기본 mode: table + chart + company info + checklist expander."""
    if "market_cap" in view.columns:
        view["시총"] = view["market_cap"].apply(lambda v: format_market_cap(v, usd_krw))
    for key, short in COND_SHORT.items():
        view[short] = view[key].map({True: "✅", False: "·"})
    view["티어"] = view["tier"].map(lambda t: f"{TIER_ICON.get(t, '·')}")

    # Determine selected ticker
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

    st.markdown("### 🎯 추천 종목 리스트 (행 클릭 → 아래 차트 갱신)")

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

    if selected_ticker:
        sel_row = view[view["ticker"] == selected_ticker].iloc[0].to_dict()
        st.markdown(f"### 📊 {selected_ticker} — {selected_name}")
        _render_signal_card(sel_row)
        _render_chart(selected_ticker, tf)
        _render_company_info(selected_ticker, view, beginner=False)
        _render_ai_analysis(selected_ticker, selected_name, ichimoku_mode, view)
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


# --- Main content dispatch ---
if mode == "쩨뿡이용":
    render_beginner(view, usd_krw, ichimoku_mode, tf)
else:
    render_advanced(view, usd_krw, ichimoku_mode, tf, rsi_th, disp_th)
