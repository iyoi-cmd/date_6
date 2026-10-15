import sqlite3

import pandas as pd
import plotly.express as px
import streamlit as st

st.set_page_config(page_title="신규 권역 성장 기회 대시보드", layout="wide")
st.title("신규 권역 성장 기회 대시보드")                          # ✏️ 6주차 메인 제목
st.caption("가입월 · 가입 채널 · 플랫폼별 | 2025.09 ~ 2026.07 가입자")   # ✏️ 6주차 부제

ACCENT, GRAY = "#1F3A5F", "#BDBDBD"
px.defaults.color_discrete_map = {
    "INSTA_첫주문쿠폰": ACCENT, "organic": "#8C8C8C", "NAVER_검색광고": "#A6A6A6",
    "GOOGLE_앱설치": GRAY, "친구초대": "#DADADA", "Android": "#8C8C8C", "iOS": GRAY,
}
px.defaults.color_discrete_sequence = [GRAY]

# ------------------------------------------------------------
# 0. 필요한 데이터만 추출 — 쓸 테이블과 컬럼만 메모리 DB에 올리고, 정제 테이블을 처음 한 번만 만듦
# ------------------------------------------------------------
DATA_DIR = "data"                          # 원본 CSV를 넣어 둔 폴더
NEEDED = {                                 # ✏️ 이번 대시보드에 필요한 테이블과 컬럼 (쿼리에 쓰는 것만)
    "app_events": ["event_datetime", "customer_id", "session_id", "event_name",
                   "platform", "traffic_source", "campaign_name"],
    "orders": ["customer_id", "order_datetime"],
}

@st.cache_resource
def get_db():                              # 필요한 컬럼만 메모리 DB에 올리고, 정제 테이블 events·odr을 생성(처음 한 번)
    con = sqlite3.connect(":memory:", check_same_thread=False)
    for name, cols in NEEDED.items():
        # dtype=str: 고객 ID의 앞자리 0(0000002)이 숫자로 바뀌지 않게 글자로 읽음
        pd.read_csv(f"{DATA_DIR}/{name}.csv", usecols=cols, dtype=str).to_sql(name, con, index=False)
    # ✏️ 5주차 제외 규칙: 테스트 계정(T로 시작) 제외 · 이벤트 100건 이상인 비정상 세션 제외
    con.execute("""
        CREATE TABLE events AS
        SELECT *
          FROM app_events
         WHERE customer_id NOT LIKE 'T%'
           AND session_id NOT IN (SELECT session_id FROM app_events
                                  GROUP BY session_id HAVING COUNT(*) >= 100)
    """)
    con.execute("CREATE TABLE odr AS SELECT * FROM orders WHERE customer_id NOT LIKE 'T%'")
    # 고객별로 묶는 집계가 빨라지도록 customer_id에 인덱스를 붙임
    con.execute("CREATE INDEX idx_events_customer ON events(customer_id)")
    con.execute("CREATE INDEX idx_odr_customer ON odr(customer_id)")
    return con

# ------------------------------------------------------------
# 1. 데이터 준비 — 정제된 테이블을 SQL로 집계 (비율이 아니라 분자 · 분모 건수만)
# ------------------------------------------------------------
@st.cache_data
def query(sql):                            # SQL을 실행해 표로 돌려줌
    return pd.read_sql(sql, get_db())

# 고객별 가입일 · 가입 채널 · 플랫폼 (가입 이벤트 기준) - 두 쿼리 모두 사용
SQL_SIGNUP = """
su AS (
    SELECT customer_id,
           DATE(MIN(event_datetime))                     AS 가입일,
           MIN(COALESCE(campaign_name, traffic_source)) AS 가입채널,
           MIN(platform)                                AS 플랫폼
      FROM events
     WHERE event_name = 'sign_up'
     GROUP BY customer_id
)
"""

# ✏️ 가입월 × 가입 채널 × 플랫폼별 가입 후 30일 퍼널 (예제 Q1 · Q3)
SQL_SIGNUP_FUNNEL = f"""
WITH {SQL_SIGNUP},
ev AS (                                    -- 가입 후 30일 안의 행동
    SELECT DISTINCT e.customer_id, e.event_name
      FROM events e
      JOIN su ON e.customer_id = su.customer_id
     WHERE julianday(DATE(e.event_datetime)) - julianday(su.가입일) BETWEEN 0 AND 30
),
fo AS (                                    -- 고객별 첫 주문일
    SELECT customer_id, DATE(MIN(order_datetime)) AS 첫주문일
      FROM odr
     GROUP BY customer_id
)
SELECT strftime('%Y-%m', su.가입일) AS 가입월,
       su.가입채널,
       su.플랫폼,
       COUNT(DISTINCT su.customer_id)                                                       AS 가입자,
       COUNT(DISTINCT CASE WHEN ev.event_name = 'view_restaurant' THEN su.customer_id END)    AS 식당조회,
       COUNT(DISTINCT CASE WHEN ev.event_name = 'add_to_cart'     THEN su.customer_id END)    AS 장바구니,
       COUNT(DISTINCT CASE WHEN ev.event_name = 'begin_checkout'  THEN su.customer_id END)    AS 결제시작,
       COUNT(DISTINCT CASE WHEN julianday(fo.첫주문일) - julianday(su.가입일) <= 30
                            THEN su.customer_id END)                                        AS 첫주문30일
  FROM su
  LEFT JOIN ev ON su.customer_id = ev.customer_id
  LEFT JOIN fo ON su.customer_id = fo.customer_id
 WHERE su.가입일 <= '2026-07-31'            -- 30일을 다 볼 수 있는 가입자만
 GROUP BY 1, 2, 3
 ORDER BY 1, 2, 3
"""

# ✏️ 가입월 × 가입 채널 × 플랫폼별 30일 재주문 (예제 Q2, 분모가 첫 주문 고객이라 표를 따로 만듦)
SQL_REORDER = f"""
WITH {SQL_SIGNUP},
seq AS (
    SELECT customer_id, order_datetime,
           ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY order_datetime) AS 순번,
           LEAD(order_datetime) OVER (PARTITION BY customer_id ORDER BY order_datetime) AS 다음주문
      FROM odr
)
SELECT strftime('%Y-%m', su.가입일) AS 가입월,
       su.가입채널,
       su.플랫폼,
       COUNT(*)                                                                             AS 첫주문고객,
       SUM(CASE WHEN julianday(DATE(seq.다음주문)) - julianday(DATE(seq.order_datetime)) <= 30
                THEN 1 ELSE 0 END)                                                          AS 재주문30일
  FROM seq
  JOIN su ON seq.customer_id = su.customer_id
 WHERE seq.순번 = 1
   AND seq.order_datetime < '2026-08-01'    -- 30일을 다 볼 수 있는 첫 주문만
 GROUP BY 1, 2, 3
 ORDER BY 1, 2, 3
"""

@st.cache_data
def load_data():
    return query(SQL_SIGNUP_FUNNEL), query(SQL_REORDER)

df, rd = load_data()                       # df: 가입 후 30일 퍼널 / rd: 30일 재주문

# 📌 AI 해설 사전 데이터 결합
AI_NOTES = {
    "전체": "전체 가입자 2,774명의 30일 첫 주문 전환율은 69.9%이며 가장 병목이 심한 단계는 식당조회 to 장바구니 단계(84.1%)이다. INSTA_첫주문쿠폰 채널이 도입된 2026-01 가입월부터 전체 첫 주문 전환율이 70%대 중반에서 60%대로 하락하는 양상을 보인다. 유입 유저의 질적 변화나 프로모션 구조 변동이 전체 전환율 저하에 영향을 미친 것으로 보인다.",
    "Android": "Android 플랫폼 가입자 1,502명의 30일 첫 주문 전환율은 69.2%로 iOS(70.7%) 및 전체 평균(69.9%)보다 소폭 낮다. 식당조회 to 장바구니 전환율이 83.8%로 가장 낮은 수치를 기록하며 전체 퍼널의 주요 이탈 지점 역할을 하고 있다. Android 앱 환경에서의 탐색 편의성이나 UI 문제 가능성에 대해 확인이 필요하다.",
    "iOS": "iOS 플랫폼 가입자 1,272명의 30일 첫 주문 전환율은 70.7%로 Android(69.2%)보다 높으며 30일 재주문율 역시 68.2%로 상대적으로 우수하다. 가장 낮은 전환율을 보인 단계는 식당조회 to 장바구니(84.5%)로 전체 수준과 유사하다. 상대적으로 결제 의사가 높은 사용자층이 유입되고 있는 것으로 보인다.",
    "organic": "organic 채널 가입자 847명의 30일 첫 주문 전환율은 75.2%, 30일 재주문율은 73.5%로 전체 평균을 크게 상회한다. 가장 낮은 단계인 식당조회 to 장바구니 전환율 역시 88.6%로 높게 형성되어 있다. 자발적 유입 유저의 높은 관여도가 지속적인 구매 및 재주문으로 이어지는 것으로 보인다.",
    "INSTA_첫주문쿠폰": "INSTA_첫주문쿠폰 가입자 739명의 30일 첫 주문 전환율은 55.6%, 30일 재주문율은 44.5%로 전체 및 타 채널 대비 현저히 낮다. 식당조회 to 장바구니 전환율이 72.4%로 가장 큰 병목을 보이며, 2026-01 가입월에 처음 등장한 이후 전체 전환율 감소를 견인했다. 단발성 혜택만을 노린 체리피커 유저의 높은 유입 비율이 원인인지 확인이 필요하다.",
    "NAVER_검색광고": "NAVER_검색광고 가입자 517명의 30일 첫 주문 전환율은 76.4%로 유료 광고 채널 중 가장 높은 성과를 보인다. 가장 낮은 단계인 식당조회 to 장바구니 전환율은 87.7%이며 30일 재주문율도 72.6%로 견조하다. 목적형 검색을 통해 유입된 고객의 높은 구매 의도가 반영된 결과로 보인다.",
    "GOOGLE_앱설치": "GOOGLE_앱설치 가입자 398명의 30일 첫 주문 전환율은 73.6%이며 30일 재주문율은 72.7% 수준이다. 다른 대상들과 달리 가장 낮은 전환율 단계가 장바구니 to 결제시작(88.0%)으로 나타나는 점이 특징적이다. 해당 채널 유입 유저의 결제 진입 단계 허들을 낮추는 개선안이 유효할 것으로 보인다.",
    "친구초대": "친구초대 가입자는 273명으로 집단 크기가 300명 미만으로 다소 작다. 30일 첫 주문 전환율은 74.4%이며, 30일 재주문율은 77.3%로 8개 대상 중 가장 높은 재구매 충성도를 보인다. 기존 고객 추천을 통한 유입 특성상 상호 신뢰 기반의 높은 브랜드 충성도가 작용한 것으로 보인다."
}
AI_SOURCE = "🤖 Gemini가 미리 만든 해설 · 전체 기간 기준"

# ------------------------------------------------------------
# 2. 사이드바 필터 — 두 표에 같은 조건을 적용
# ------------------------------------------------------------
SMALL = 100                                # ✏️ 이보다 가입자가 적은 집단은 "작은 집단"

if "reset" not in st.session_state:
    st.session_state.reset = 0

def reset_filters():                       # 번호가 바뀌면 위젯이 처음 상태로 새로 만들어짐
    st.session_state.reset += 1

n = st.session_state.reset
months = sorted(df["가입월"].unique())
channels = sorted(df["가입채널"].unique())
platforms = sorted(df["플랫폼"].unique())

st.sidebar.header("필터")
start, end = st.sidebar.select_slider("가입월", options=months,
                                    value=(months[0], months[-1]), key=f"기간_{n}")
sel_channels = st.sidebar.multiselect("가입 채널", channels, default=channels, key=f"채널_{n}")
sel_platforms = st.sidebar.multiselect("플랫폼", platforms, default=platforms, key=f"플랫폼_{n}")
with st.sidebar.expander("고급 설정"):
    base_start, base_end = st.select_slider(
        "평소 범위 기준 기간", options=months, value=(months[0], months[3]), key=f"기준_{n}",   # ✏️ 문제가 없던 기간
        help="문제가 없던 기간을 고르세요. 이 기간의 월별 값 범위를 '평소 범위'로 씁니다.")
    hide_small = st.checkbox(f"가입자 {SMALL}명 미만 집단 제외 (집단 비교 · 드릴다운)", key=f"작은집단_{n}")
st.sidebar.button("필터 초기화", on_click=reset_filters)

# 집단 조건(가입 채널 · 플랫폼)만 적용한 데이터 g · rg, 기간까지 적용한 데이터 f · rf
g = df[df["가입채널"].isin(sel_channels) & df["플랫폼"].isin(sel_platforms)]
f = g[(g["가입월"] >= start) & (g["가입월"] <= end)]
rg = rd[rd["가입채널"].isin(sel_channels) & rd["플랫폼"].isin(sel_platforms)]
rf = rg[(rg["가입월"] >= start) & (rg["가입월"] <= end)]

def label(selected, all_values):           # 조건 줄에 쓸 말: 모두 고르면 "전체"
    if len(selected) == len(all_values):
        return "전체"
    return ", ".join(selected) if selected else "선택 없음"

period = "전체" if (start, end) == (months[0], months[-1]) else f"{start} ~ {end}"
st.caption(f"🔎 보고 있는 조건: 가입월 {period} · 가입 채널 {label(sel_channels, channels)} · "
           f"플랫폼 {label(sel_platforms, platforms)}")

if f.empty:
    st.warning("선택한 조건에 해당하는 데이터가 없습니다. 필터를 바꿔 주세요.")
    st.stop()

def rate(a, b):
    return a / b * 100 if b else 0

# ------------------------------------------------------------
# 3. 화면 — 탭 3개
# ------------------------------------------------------------
tab_overview, tab_explore, tab_action = st.tabs(["📊 개요", "🔍 탐색", "✅ 액션"])

with tab_overview:
    st.caption("이 탭에서 볼 수 있는 것: 핵심 숫자와 그 숫자를 읽는 법, 6주차 리포트의 차트 3장 (필터 적용)")

    # 3-1. KPI 카드 — ✏️ 5주차 지표 3개
    t = f[["가입자", "식당조회", "장바구니", "첫주문30일"]].sum()
    tr = rf[["첫주문고객", "재주문30일"]].sum()
    c1, c2, c3 = st.columns(3)
    c1.metric("30일 첫 주문 전환율", f"{rate(t['첫주문30일'], t['가입자']):.1f}%")
    c2.metric("식당 조회 → 장바구니 전환율", f"{rate(t['장바구니'], t['식당조회']):.1f}%")
    c3.metric("30일 재주문율", f"{rate(tr['재주문30일'], tr['첫주문고객']):.1f}%")

    # --- 지금 숫자 읽기 상자 추가 ---
    with st.container(border=True):
        st.markdown("**📏 지금 숫자 읽기**")

        f_months = sorted(f["가입월"].unique())
        
        # 1) 30일 첫 주문 전환율 메시지 생성
        msg_order = ""
        if len(f_months) < 3:
            msg_order = "비교하려면 선택 기간을 3개월 이상 골라 주세요"
        else:
            last_3_months = f_months[-3:]
            f_last3 = f[f["가입월"].isin(last_3_months)]
            sum_last3 = f_last3[["가입자", "식당조회", "장바구니", "결제시작", "첫주문30일"]].sum()
            current_rate_order = rate(sum_last3["첫주문30일"], sum_last3["가입자"])
            start_month_label = last_3_months[0]

            g_base = g[(g["가입월"] >= base_start) & (g["가입월"] <= base_end)]
            g_base_months = sorted(g_base["가입월"].unique())
            
            if len(g_base_months) < 3:
                msg_order = "기준 기간을 이 집단의 데이터가 있는 시기로 옮겨 주세요"
            else:
                rates_list = []
                for m in g_base_months:
                    m_df = g_base[g_base["가입월"] == m][["가입자", "첫주문30일"]].sum()
                    b_denom = m_df["가입자"]
                    if b_denom > 0:
                        rates_list.append(rate(m_df["첫주문30일"], b_denom))
                
                if not rates_list:
                    msg_order = "기준 기간에 계산 가능한 데이터가 없습니다."
                else:
                    min_r = min(rates_list)
                    max_r = max(rates_list)
                    if current_rate_order < min_r:
                        diff_val = min_r - current_rate_order
                        msg_order = f"{start_month_label} 가입월부터 3개월 30일 첫 주문 전환율은 {current_rate_order:.1f}%로, 평소 범위({min_r:.1f}–{max_r:.1f}%)보다 {diff_val:.1f}%p 낮습니다"
                    elif current_rate_order > max_r:
                        diff_val = current_rate_order - max_r
                        msg_order = f"{start_month_label} 가입월부터 3개월 30일 첫 주문 전환율은 {current_rate_order:.1f}%로, 평소 범위({min_r:.1f}–{max_r:.1f}%)보다 {diff_val:.1f}%p 높습니다"
                    else:
                        msg_order = f"{start_month_label} 가입월부터 3개월 30일 첫 주문 전환율은 {current_rate_order:.1f}%로, 평소 범위({min_r:.1f}–{max_r:.1f}%) 내에 있습니다"

        # 2) 식당 조회 → 장바구니 전환율 메시지 생성
        msg_cart = ""
        if len(f_months) < 3:
            msg_cart = "비교하려면 선택 기간을 3개월 이상 골라 주세요"
        else:
            last_3_months = f_months[-3:]
            f_last3 = f[f["가입월"].isin(last_3_months)]
            sum_last3 = f_last3[["가입자", "식당조회", "장바구니", "결제시작", "첫주문30일"]].sum()
            current_rate_cart = rate(sum_last3["장바구니"], sum_last3["식당조회"])
            start_month_label = last_3_months[0]

            g_base = g[(g["가입월"] >= base_start) & (g["가입월"] <= base_end)]
            g_base_months = sorted(g_base["가입월"].unique())
            
            if len(g_base_months) < 3:
                msg_cart = "기준 기간을 이 집단의 데이터가 있는 시기로 옮겨 주세요"
            else:
                rates_list_cart = []
                for m in g_base_months:
                    m_df = g_base[g_base["가입월"] == m][["식당조회", "장바구니"]].sum()
                    b_denom = m_df["식당조회"]
                    if b_denom > 0:
                        rates_list_cart.append(rate(m_df["장바구니"], b_denom))
                
                if not rates_list_cart:
                    msg_cart = "기준 기간에 계산 가능한 데이터가 없습니다."
                else:
                    min_r_c = min(rates_list_cart)
                    max_r_c = max(rates_list_cart)
                    if current_rate_cart < min_r_c:
                        diff_val = min_r_c - current_rate_cart
                        msg_cart = f"{start_month_label} 가입월부터 3개월 식당 조회 → 장바구니 전환율은 {current_rate_cart:.1f}%로, 평소 범위({min_r_c:.1f}–{max_r_c:.1f}%)보다 {diff_val:.1f}%p 낮습니다"
                    elif current_rate_cart > max_r_c:
                        diff_val = current_rate_cart - max_r_c
                        msg_cart = f"{start_month_label} 가입월부터 3개월 식당 조회 → 장바구니 전환율은 {current_rate_cart:.1f}%로, 평소 범위({min_r_c:.1f}–{max_r_c:.1f}%)보다 {diff_val:.1f}%p 높습니다"
                    else:
                        msg_cart = f"{start_month_label} 가입월부터 3개월 식당 조회 → 장바구니 전환율은 {current_rate_cart:.1f}%로, 평소 범위({min_r_c:.1f}–{max_r_c:.1f}%) 내에 있습니다"

        st.markdown(f"- {msg_order}")
        st.markdown(f"- {msg_cart}")
        
        st.caption(f"📏 자동 계산 · 평소 범위 기준 기간 {base_start} ~ {base_end} (월별 값)")
        
        with st.expander("🤖 AI 해설 · 전체", expanded=False):
            st.write(AI_NOTES.get("전체", "아직 해설이 없습니다."))
            st.caption(AI_SOURCE)

with tab_overview:
    st.divider()

    # ✏️ 3-2. 차트 - 6주차 차트 ① 문제 · ② 원인 · ③ 제안
    left, right = st.columns(2)

    # ① 문제: 가입월별 30일 첫 주문 전환율
    m = f.groupby("가입월")[["가입자", "첫주문30일"]].sum().reset_index()
    m["첫주문전환율"] = (m["첫주문30일"] / m["가입자"] * 100).round(1)
    fig1 = px.line(m, x="가입월", y="첫주문전환율", markers=True, hover_data=["가입자"],
                   labels={"가입월": "가입월", "첫주문전환율": "30일 첫 주문 전환율(%)", "가입자": "가입자(명)"},
                   title="① 가입월별 30일 첫 주문 전환율")
    fig1.update_layout(xaxis_title="")
    fig1.update_xaxes(type="category")       # 가입월(2025-09)을 날짜가 아니라 글자 그대로 표시
    left.plotly_chart(fig1, width="stretch")

    # ② 원인: 가입 채널별 식당 조회 → 장바구니 전환율
    ch = f.groupby("가입채널")[["식당조회", "장바구니"]].sum().reset_index()
    ch["장바구니전환율"] = (ch["장바구니"] / ch["식당조회"] * 100).round(1)
    ch = ch.sort_values("장바구니전환율")
    fig2 = px.bar(ch, x="장바구니전환율", y="가입채널", orientation="h", text_auto=".1f",
                  hover_data=["식당조회"],
                  labels={"장바구니전환율": "식당 조회 → 장바구니 전환율(%)", "가입채널": "가입 채널",
                          "식당조회": "식당 조회 가입자(명)"},
                  title="② 가입 채널별 식당 조회 → 장바구니 전환율")
    fig2.update_traces(marker_color=[ACCENT if c == "INSTA_첫주문쿠폰" else GRAY for c in ch["가입채널"]])
    fig2.update_xaxes(visible=False)
    fig2.update_layout(yaxis_title="")
    right.plotly_chart(fig2, width="stretch")

    # ③ 제안: 가입 채널별 30일 재주문율
    rc = rf.groupby("가입채널")[["첫주문고객", "재주문30일"]].sum().reset_index()
    rc["재주문율"] = (rc["재주문30일"] / rc["첫주문고객"] * 100).round(1)
    rc = rc.sort_values("재주문율")
    fig3 = px.bar(rc, x="재주문율", y="가입채널", orientation="h", text_auto=".1f",
                  hover_data=["첫주문고객"],
                  labels={"재주문율": "30일 재주문율(%)", "가입채널": "가입 채널", "첫주문고객": "첫 주문 고객(명)"},
                  title="③ 가입 채널별 30일 재주문율")
    fig3.update_xaxes(range=[0, rc["재주문율"].max() * 1.2])
    fig3.update_layout(yaxis_title="")
    st.plotly_chart(fig3, width="stretch")

    # AI 해설을 만들 때 Gemini에 올릴 표 (필터와 관계없이 전체)
    with st.expander("📥 AI 해설용 데이터 내려받기"):
        st.download_button("가입 후 30일 퍼널 (가입월 × 가입 채널 × 플랫폼)",
                           df.to_csv(index=False).encode("utf-8-sig"), "가입월_채널_플랫폼_퍼널.csv")
        st.download_button("30일 재주문 (가입월 × 가입 채널 × 플랫폼)",
                           rd.to_csv(index=False).encode("utf-8-sig"), "가입월_채널_플랫폼_재주문.csv")

with tab_explore:
    st.caption("이 탭에서 볼 수 있는 것: 집단 비교와 드릴다운 — 어디서, 언제부터인가")
    
    st.subheader("집단 비교")
    group_by = st.radio("비교 기준", ["가입채널", "플랫폼"], horizontal=True, key="explore_group_by")
    
    # f 데이터에서 집단별 퍼널 5단계 합계 계산
    agg_df = f.groupby(group_by)[["가입자", "식당조회", "장바구니", "결제시작", "첫주문30일"]].sum().reset_index()
    
    # 작은 집단 제외 처리
    excluded_groups = []
    if hide_small:
        small_groups = agg_df[agg_df["가입자"] < SMALL][group_by].tolist()
        excluded_groups = small_groups
        agg_df = agg_df[agg_df["가입자"] >= SMALL]
        if excluded_groups:
            st.caption(f"제외한 집단: {', '.join(excluded_groups)} (가입자 {SMALL}명 미만)")
            
    # 남은 집단 중 가입자가 SMALL명 미만인 집단이 있는지 확인 (hide_small이 False이거나 남아있는 경우 안내)
    all_f_agg = f.groupby(group_by)[["가입자", "식당조회", "장바구니", "결제시작", "첫주문30일"]].sum().reset_index()
    small_remaining = all_f_agg[all_f_agg["가입자"] < SMALL][group_by].tolist()
    if small_remaining:
        st.caption(f"⚠️ 안내: 가입자가 {SMALL}명 미만인 집단({', '.join(small_remaining)})이 포함되어 있어 값이 크게 흔들릴 수 있습니다.")
        
    if agg_df.empty:
        st.warning("조건에 해당하는 데이터가 없습니다.")
    else:
        # 퍼널 차트를 위해 데이터를 long format으로 변환 (누적 전환율 계산)
        funnel_steps = [
            ("가입자", "가입자"),
            ("식당조회", "식당조회"),
            ("장바구니", "장바구니"),
            ("결제시작", "결제시작"),
            ("첫주문30일", "첫주문30일")
        ]
        
        long_list = []
        for _, row in agg_df.iterrows():
            g_name = row[group_by]
            base_val = row["가입자"]
            for step_key, step_label in funnel_steps:
                val = row[step_key]
                rate_val = (val / base_val * 100) if base_val > 0 else 0
                long_list.append({
                    group_by: g_name,
                    "단계": step_label,
                    "누적전환율": round(rate_val, 1),
                    "사람수": val,
                    "기준사람수": base_val
                })
        long_df = pd.DataFrame(long_list)
        
        # 단계 전환율(직전 단계 대비 %) 계산을 위한 데이터 준비
        step_transition_list = []
        for _, row in agg_df.iterrows():
            g_name = row[group_by]
            s_su = row["가입자"]
            s_view = row["식당조회"]
            s_cart = row["장바구니"]
            s_chk = row["결제시작"]
            s_ord = row["첫주문30일"]
            
            transitions = [
                ("가입 → 식당조회", s_view, s_su),
                ("식당조회 → 장바구니", s_cart, s_view),
                ("장바구니 → 결제시작", s_chk, s_cart),
                ("결제시작 → 첫주문30일", s_ord, s_chk)
            ]
            
            for t_label, cur_val, prev_val in transitions:
                t_rate = (cur_val / prev_val * 100) if prev_val > 0 else 0
                step_transition_list.append({
                    group_by: g_name,
                    "단계전환": t_label,
                    "단계전환율": round(t_rate, 1),
                    "직전단계사람수": prev_val,
                    "현재사람수": cur_val
                })
        trans_df = pd.DataFrame(step_transition_list)
        
        col_left, col_right = st.columns(2)
        
        with col_left:
            fig_funnel = px.funnel(
                long_df,
                x="누적전환율",
                y="단계",
                color=group_by,
                text="누적전환율",
                hover_data={"누적전환율": True, "사람수": True, "기준사람수": True, "단계": False},
                labels={"누적전환율": "누적 전환율(%)", "단계": "퍼널 단계", group_by: "집단", "사람수": "사람 수(명)"},
                title="집단별 누적 전환율 퍼널"
            )
            col_left.plotly_chart(fig_funnel, width="stretch")
            
        with col_right:
            fig_bar = px.bar(
                trans_df,
                x="단계전환",
                y="단계전환율",
                color=group_by,
                barmode="group",
                text="단계전환율",
                hover_data={"단계전환율": True, "직전단계사람수": True, "현재사람수": True},
                labels={"단계전환율": "단계 전환율(%)", "단계전환": "퍼널 단계 전환", group_by: "집단", "직전단계사람수": "직전 단계 사람 수(명)"},
                title="집단별 단계 전환율 비교"
            )
            fig_bar.update_traces(texttemplate='%{text:.1f}%', textposition='outside')
            fig_bar.update_layout(yaxis_title="단계 전환율(%)", xaxis_title="")
            col_right.plotly_chart(fig_bar, width="stretch")

    st.divider()
    st.subheader("드릴다운: 언제부터, 무엇 때문일까")

    available_groups = agg_df[group_by].tolist()
    if not available_groups:
        available_groups = all_f_agg[group_by].tolist()

    col_s1, col_s2 = st.columns(2)
    with col_s1:
        sel_group = st.selectbox("자세히 볼 집단", available_groups, key="drill_group")
    with col_s2:
        step_options = [
            "가입 → 30일 첫 주문",
            "가입 → 식당조회",
            "식당조회 → 장바구니",
            "장바구니 → 결제시작",
            "결제시작 → 30일 첫 주문"
        ]
        sel_step = st.selectbox("단계", step_options, key="drill_step")

    other_axis = "플랫폼" if group_by == "가입채널" else "가입채널"

    f_drill = f[f[group_by] == sel_group]
    m_drill = f_drill.groupby(["가입월", other_axis])[["가입자", "식당조회", "장바구니", "결제시작", "첫주문30일"]].sum().reset_index()

    rate_col_name = "전환율(%)"
    calc_rows = []
    
    for _, row in m_drill.iterrows():
        su = row["가입자"]
        view = row["식당조회"]
        cart = row["장바구니"]
        chk = row["결제시작"]
        ord30 = row["첫주문30일"]
        
        val = None
        if sel_step == "가입 → 30일 첫 주문":
            if su > 0:
                val = round(ord30 / su * 100, 1)
        elif sel_step == "가입 → 식당조회":
            if su > 0:
                val = round(view / su * 100, 1)
        elif sel_step == "식당조회 → 장바구니":
            if view > 0:
                val = round(cart / view * 100, 1)
        elif sel_step == "장바구니 → 결제시작":
            if cart > 0:
                val = round(chk / cart * 100, 1)
        elif sel_step == "결제시작 → 30일 첫 주문":
            if chk > 0:
                val = round(ord30 / chk * 100, 1)
                
        if val is not None:
            new_row = row.to_dict()
            new_row[rate_col_name] = val
            calc_rows.append(new_row)
            
    drill_chart_df = pd.DataFrame(calc_rows)

    fig_drill1 = px.line(
        drill_chart_df,
        x="가입월",
        y=rate_col_name,
        color=other_axis,
        markers=True,
        labels={"가입월": "가입월", rate_col_name: f"{sel_step} 전환율(%)", other_axis: other_axis},
        title=f"① {sel_group}의 가입월별 {sel_step} 전환율 — {other_axis}별로 쪼개 보기"
    )
    fig_drill1.update_layout(xaxis_title="")
    fig_drill1.update_xaxes(type="category")
    st.plotly_chart(fig_drill1, width="stretch")
    st.caption("모든 갈래가 함께 움직였다면, 그 축은 원인이 아닙니다.")

    f_total_group = f.groupby(["가입월", group_by])["가입자"].sum().reset_index()
    f_total_group["집단구분"] = f_total_group[group_by].apply(lambda x: sel_group if x == sel_group else "그 외")
    stack_df = f_total_group.groupby(["가입월", "집단구분"])["가입자"].sum().reset_index()
    
    color_map = {sel_group: "#1f4e78", "그 외": "#d9d9d9"}
    
    fig_drill2 = px.bar(
        stack_df,
        x="가입월",
        y="가입자",
        color="집단구분",
        barmode="stack",
        color_discrete_map=color_map,
        labels={"가입월": "가입월", "가입자": "가입자 수(명)", "집단구분": "집단"},
        title=f"② 가입월별 가입자 구성 ({sel_group} vs 그 외)"
    )
    fig_drill2.update_layout(xaxis_title="")
    fig_drill2.update_xaxes(type="category")
    st.plotly_chart(fig_drill2, width="stretch")
    st.caption("전체 전환율이 움직인 시점에 이 집단의 비중이 함께 바뀌었다면 구성 변화가 원인 후보입니다. 함께 움직였다고 원인이 확정되지는 않습니다.")

    with st.expander(f"🤖 AI 해설 · {sel_group}", expanded=False):
        note_text = AI_NOTES.get(sel_group, "이 집단의 해설은 아직 없습니다.")
        st.write(note_text)
        st.caption(AI_SOURCE)

with tab_action:
    st.caption("측정 KPI의 현재 값은 선택한 기간의 마지막 3개월로 계산합니다")
    
    f_action = df[(df["가입월"] >= start) & (df["가입월"] <= end)]
    rd_action = rd[(rd["가입월"] >= start) & (rd["가입월"] <= end)]
    
    def get_last_3_months_data(data_df):
        if data_df.empty:
            return pd.DataFrame()
        months = sorted(data_df["가입월"].unique())
        if len(months) < 3:
            last_months = months
        else:
            last_months = months[-3:]
        return data_df[data_df["가입월"].isin(last_months)]

    f_act_last3 = get_last_3_months_data(f_action)
    rd_act_last3 = get_last_3_months_data(rd_action)

    col1, col2 = st.columns(2)
    
    with col1:
        with st.container(border=True):
            st.markdown("**카드 1 · (제목)**")
            st.markdown("- 문제: …")
            st.markdown("- 근거: …")
            st.markdown("- 성격: …")
            st.markdown("- 액션: …")
            st.markdown("- 우선순위: …")
            
            if not f_act_last3.empty:
                t_act = f_act_last3[["가입자", "식당조회", "장바구니", "결제시작", "첫주문30일"]].sum()
                val_c1 = rate(t_act['첫주문30일'], t_act['가입자'])
                col1.metric("카드 1 측정 KPI", f"{val_c1:.1f}%")
            else:
                col1.metric("카드 1 측정 KPI", "데이터 없음")
            st.caption("대시보드에서: (내가 쓴 내용)")
            
            # 액션 탭 카드 1 내 AI 해설 추가
            with st.expander("🤖 AI 해설 · INSTA_첫주문쿠폰", expanded=False):
                st.write(AI_NOTES.get("INSTA_첫주문쿠폰", "아직 해설이 없습니다."))
                st.caption(AI_SOURCE)
            
    with col2:
        with st.container(border=True):
            st.markdown("**카드 2 · (제목)**")
            st.markdown("- 문제: …")
            st.markdown("- 근거: …")
            st.markdown("- 성격: …")
            st.markdown("- 액션: …")
            st.markdown("- 우선순위: …")
            
            if not rd_act_last3.empty:
                tr_act = rd_act_last3[["첫주문고객", "재주문30일"]].sum()
                val_c2 = rate(tr_act['재주문30일'], tr_act['첫주문고객'])
                col2.metric("카드 2 측정 KPI", f"{val_c2:.1f}%")
            else:
                col2.metric("카드 2 측정 KPI", "데이터 없음")
            st.caption("대시보드에서: (내가 쓴 내용)")
            
            # 액션 탭 카드 2 내 AI 해설 추가 (예: 전체 또는 주요 제안 채널 기준)
            with st.expander("🤖 AI 해설 · 재주문 및 채널 요약", expanded=False):
                st.write(AI_NOTES.get("organic", "아직 해설이 없습니다."))
                st.caption(AI_SOURCE)