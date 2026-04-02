import streamlit as st
import pandas as pd
from sqlalchemy import create_engine, text
from datetime import datetime
import pymysql
import plotly.express as px

# 기존 수집 로직 임포트
from collect import analyze_and_save, DB_HOST, DB_USER, DB_PASS, DB_NAME

# --- [1. 기본 설정 및 반응형 CSS] ---
st.set_page_config(page_title="Trend Undercurrent", page_icon="🌊", layout="wide")

st.markdown("""
<style>
.main { background-color: #F8F9FA; }
div[data-testid="stForm"] { border: 2px solid #E9ECEF; border-radius: 15px; padding: 20px; background-color: white; box-shadow: 0 4px 6px rgba(0,0,0,0.02); }

/* 대시보드 컨테이너: 기본 가로 배치 */
.dashboard-container { 
    display: flex; 
    flex-direction: row; 
    gap: 15px; 
    margin-bottom: 25px; 
    align-items: stretch; 
    width: 100%;
}

.ranking-board, .insight-board { 
    flex: 1; 
    height: 320px; 
    border-radius: 15px; 
    padding: 25px; 
    box-shadow: 0 4px 15px rgba(0,0,0,0.03); 
    box-sizing: border-box; 
    min-width: 0;
}

/* 좌측 랭킹보드 스타일 */
.ranking-board { background-color: white; border: 1px solid #E9ECEF; overflow: hidden; position: relative; }
.ranking-title { font-size: 1.2rem; font-weight: 800; color: #2B3452; margin-bottom: 10px; border-bottom: 2px solid #F1F3F5; padding-bottom: 10px; }

/* 우측 인사이트보드 스타일 */
.insight-board { background-color: #2B3452; color: white; display: flex; flex-direction: column; justify-content: flex-start; }
.insight-title { font-size: 1.2rem; font-weight: 800; color: #FFD43B; margin-bottom: 10px; border-bottom: 2px solid rgba(255,255,255,0.1); padding-bottom: 10px; }
.insight-text { font-size: 1.1rem; line-height: 1.6; margin-bottom: 10px; }
.highlight-text { font-weight: 900; color: #38D9A9; font-size: 1.3rem; }

/* 롤링 애니메이션 설정 */
.ticker-viewport { height: 210px; overflow: hidden; position: relative; }
.ticker-track { animation: smoothSlide 10s ease-in-out infinite; }
.ticker-track:hover { animation-play-state: paused; } 
.ticker-page { height: 210px; display: flex; flex-direction: column; }

.ranking-item { 
    height: 42px; display: flex; align-items: center; justify-content: space-between; 
    border-bottom: 1px dashed #F1F3F5; font-size: 1.1rem; box-sizing: border-box;
}
.ranking-rank { font-weight: 900; color: #FF6B6B; width: 30px; }
.ranking-kw { font-weight: 600; color: #212529; flex-grow: 1; }
.ranking-score { font-size: 0.9rem; color: #868E96; font-weight: 600; }

@keyframes smoothSlide {
    0%, 40% { transform: translateY(0); }             
    45%, 50% { transform: translateY(-210px); }       
    50%, 90% { transform: translateY(-210px); }       
    95%, 100% { transform: translateY(-420px); }      
}

/* -----------------------------------------------------------
   [최종 수정] 모바일 가로 2단 + 제목 강조 + 데이터 슬림화
----------------------------------------------------------- */
@media (max-width: 768px) {
    /* 1. 메인 제목 크기 키우기 */
    h1 { 
        font-size: 1.6rem !important; 
        white-space: nowrap !important; 
        letter-spacing: -1.5px !important; 
        margin-bottom: 15px !important;
    }
    
    /* 2. 가로 2단 배치 강제 유지 */
    .dashboard-container { 
        flex-direction: row !important; 
        gap: 8px !important; 
    }

    /* 3. 박스 내부 여백 및 높이 조정 */
    .ranking-board, .insight-board { 
        padding: 12px !important; 
        height: 280px !important; 
    }

    /* 4. 제목 및 텍스트 폰트 다이어트 */
    .ranking-title, .insight-title { 
        font-size: 0.8rem !important; 
        padding-bottom: 8px !important;
        margin-bottom: 8px !important;
    }

    .ranking-item { font-size: 0.75rem !important; height: 35px !important; }
    .ranking-rank { width: 18px !important; }

    .insight-text { font-size: 0.7rem !important; line-height: 1.2 !important; margin-bottom: 5px !important; }
    .highlight-text { font-size: 0.9rem !important; }

    /* 5. 롤링 높이 재조정 (35px * 5개 = 175px) */
    .ticker-viewport { height: 175px !important; }
    .ticker-page { height: 175px !important; }
    @keyframes smoothSlide { 
        0%, 40% { transform: translateY(0); } 
        45%, 50% { transform: translateY(-175px); } 
        55%, 90% { transform: translateY(-175px); } 
        95%, 100% { transform: translateY(-350px); } 
    }
}

.stButton button, .stFormSubmitButton button { border-radius: 10px !important; font-weight: 600 !important; background-color: #2B3452; color: white; transition: 0.3s; }
</style>
""", unsafe_allow_html=True)

engine = create_engine(f"mysql+pymysql://{DB_USER}:{DB_PASS}@{DB_HOST}/{DB_NAME}")

if 'searched_keywords' not in st.session_state:
    st.session_state.searched_keywords = set()

# --- [2. 핵심 데이터 함수] ---
def get_client_ip():
    """접속한 사용자의 IP 주소 추출"""
    try:
        from streamlit import context
        # Streamlit 최신 버전의 헤더 접근 방식
        headers = context.headers
        # 로드밸런서나 프록시를 거쳤을 경우 원래 IP 추출, 없으면 Remote-Addr
        ip = headers.get("X-Forwarded-For", headers.get("Remote-Addr", "unknown"))
        return ip.split(",")[0].strip() if ip else "unknown"
    except:
        return "unknown_ip"

def increment_user_search(keyword):
    """DB와 IP를 활용한 어뷰징(새로고침) 완벽 차단 로직"""
    today = datetime.now().strftime('%Y-%m-%d')
    client_ip = get_client_ip()

    with engine.connect() as conn:
        # 1. 방어벽: 이 IP가 오늘 이 키워드를 이미 클릭했는지 DB에서 확인
        check_sql = text("SELECT 1 FROM user_search_logs WHERE date = :today AND keyword = :keyword AND client_ip = :ip")
        is_already_clicked = conn.execute(check_sql, {"today": today, "keyword": keyword, "ip": client_ip}).fetchone()

        if not is_already_clicked:
            # 2. 첫 클릭이라면: IP 로그 테이블에 기록 남기기 (도장 쾅!)
            insert_sql = text("INSERT INTO user_search_logs (date, keyword, client_ip) VALUES (:today, :keyword, :ip)")
            conn.execute(insert_sql, {"today": today, "keyword": keyword, "ip": client_ip})

            # 3. 실제 카운트 1 증가
            update_sql = text("UPDATE trend_history SET user_search_count = COALESCE(user_search_count, 0) + 1 WHERE keyword = :keyword AND date = :today")
            conn.execute(update_sql, {"keyword": keyword, "today": today})
            conn.commit()
            
            # 세션에도 저장 (빠른 UI 반응을 위해)
            st.session_state.searched_keywords.add(f"{keyword}_{today}")
            return True
            
    # 이미 클릭한 IP라면 False 반환 (카운트 안 올라감)
    return False
    
def get_today_data():
    today = datetime.now().strftime('%Y-%m-%d')
    query = f"SELECT * FROM trend_history WHERE date = '{today}'"
    df = pd.read_sql(query, engine)
    if not df.empty:
        df['display_score'] = df.apply(lambda r: round(min(100.0, r['final_score'] + (r.get('user_search_count', 0) * 0.5)), 2), axis=1)
        df['search_combined'] = df.apply(lambda r: f"{r['total_search']:.1f} ({'🔺' if r['search_dod'] > 0 else '🔻' if r['search_dod'] < 0 else '➖'} {abs(r['search_dod']):.1f}%)", axis=1)
        def update_trend_type(row):
            user_clicks = row.get('user_search_count', 0)
            original = row['trend_type']
            if user_clicks >= 30: return "⚡ 플랫폼 핵심 키워드"
            elif user_clicks >= 10:
                return "🔥 유저 픽 (역주행)" if "조정기" in original or "관측" in original else "🚀 플랫폼 핫트렌드"
            return original
        df['trend_type'] = df.apply(update_trend_type, axis=1)
        df = df.sort_values('display_score', ascending=False).reset_index(drop=True)
    return df


def execute_search(kw):
    today = datetime.now().strftime('%Y-%m-%d')
    with engine.connect() as conn:
        result = pd.read_sql("SELECT * FROM trend_history WHERE keyword = %s AND date = %s", conn, params=(kw, today))
    
    if not result.empty:
        if increment_user_search(kw):
            st.session_state.search_status = "success"
            st.session_state.search_msg = f"🔥 '{kw}' 유저 관심도가 반영되어 점수가 올랐습니다!"
        else:
            st.session_state.search_status = "info"
            st.session_state.search_msg = f"💡 '{kw}'는 오늘 이미 검색하셨습니다. (최신 결과 갱신)"
    else:
        try:
            analyze_and_save([kw], "Manual_Search")
            increment_user_search(kw)
            st.session_state.search_status = "success"
            st.session_state.search_msg = f"✨ '{kw}' 신규 발굴 및 랭킹 등록 완료!"
        except Exception as e:
            st.session_state.search_status = "error"
            st.session_state.search_msg = f"❌ 분석 실패: {e}"
    st.session_state.searched_kw = kw

# --- [3. UI: 헤더 및 검색] ---
st.title("🌊 Trend-Undercurrent")
st.markdown("**NAVER, Google 검색량** / **YouTube, INSTAGRAM 언급량** / **유저 참여도**를 융합한 AI 기반 실시간 트렌드 레이더")
st.markdown("**※ 현재 개발 진행 중 : 언급량 수집 X ※**")

with st.form("search_form", clear_on_submit=False):
    col1, col2 = st.columns([4, 1])
    with col1: user_kw = st.text_input("새로운 키워드를 발굴해보세요", placeholder="예: 두쫀쿠, 버터떡, 봄동", label_visibility="collapsed")
    # ✅ 수정됨: use_container_width=True -> width="stretch"
    with col2: search_btn = st.form_submit_button("트렌드 분석", width="stretch")

if search_btn and user_kw.strip():
    kw = user_kw.strip()
    with st.spinner(f"🚀 AI 분석 중..."):
        execute_search(kw)
    st.rerun()

if 'searched_kw' in st.session_state:
    kw = st.session_state.searched_kw
    status = st.session_state.search_status
    msg = st.session_state.search_msg
    if status == "success": st.success(msg)
    elif status == "info": st.info(msg)
    elif status == "error": st.error(msg)
    
    if status in ["success", "info"]:
        df_all = get_today_data()
        searched_df = df_all[df_all['keyword'] == kw]
        if not searched_df.empty:
            st.dataframe(
                searched_df[['trend_type', 'keyword', 'display_score', 'search_combined', 'user_search_count']],
                column_config={
                    "trend_type": st.column_config.TextColumn("트렌드 속성", width="medium"),
                    "keyword": st.column_config.TextColumn("키워드", width="medium"),
                    "display_score": st.column_config.ProgressColumn("종합 점수", min_value=0, max_value=100, format="%.1f"),
                    "search_combined": st.column_config.TextColumn("검색량(전일대비)"),
                    "user_search_count": st.column_config.NumberColumn("유저 클릭수", format="🔥 %d회")
                }, 
                # ✅ 수정됨: use_container_width=True -> width="stretch"
                width="stretch", hide_index=True
            )

st.divider()

# --- [3. 상세 분석 팝업 (Dialog)] ---
def get_keyword_history(kw):
    query = text("SELECT date, final_score FROM trend_history WHERE keyword = :kw ORDER BY date ASC")
    with engine.connect() as conn:
        return pd.read_sql(query, conn, params={"kw": kw})
        
@st.dialog("📊 트렌드 심층 리포트", width="large")
def show_trend_detail(kw, df_today):
    row = df_today[df_today['keyword'] == kw].iloc[0]
    
    # 1. 연관어 태그 섹션
    st.write("🏷️ **AI 추출 연관 키워드**")
    related = row.get('related_keywords', "")
    if related:
        tags_html = "".join([f"<span style='background:#F1F3F5; color:#2B3452; padding:6px 14px; border-radius:20px; margin-right:8px; font-size:0.9rem; font-weight:700; display:inline-block; margin-bottom:10px; border:1px solid #dee2e6;'>#{w.strip()}</span>" for w in related.split(',')])
        st.markdown(tags_html, unsafe_allow_html=True)
    else: 
        st.info("연관 키워드 데이터가 없습니다.")
        
    # --- 🚀 [여기가 추가된 부분: AI 분석 리포트] ---
    st.markdown("---")
    st.write("🤖 **AI 트렌드 원인 분석**")
    
    # DB에서 analysis_txt 데이터 가져오기 (없으면 기본 메시지)
    analysis_text = row.get('analysis_txt', "분석 진행 중 또는 데이터가 없습니다.")
    
    # 가독성을 위해 st.info 블록 안에 표시
    st.info(analysis_text)
    # ---------------------------------------------
    
    st.markdown("---")
    st.subheader(f"📈 '{kw}' 시계열 지수 변화")
    history = get_keyword_history(kw)
    if not history.empty:
        fig = px.line(history, x='date', y='final_score', markers=True, line_shape='spline', color_discrete_sequence=['#FF6B6B'])
        fig.update_layout(height=280, margin=dict(l=0, r=0, t=10, b=0), xaxis_title="날짜", yaxis_title="지수")
        # ✅ 수정됨: use_container_width=True
        st.plotly_chart(fig, use_container_width=True)
    else: 
        st.info("시계열 데이터를 수집 중입니다.")
    
    
# --- [4. UI: 메인 대시보드] ---
try:
    df = get_today_data()
    def format_views(val):
      try:
          val = int(val)
          if val == 0:
              return "-"
          return f"{val:,}" # 천 단위 쉼표 추가
      except:
          return "-"
    df['view_dod_formatted'] = df['view_dod'].apply(format_views)
    
    if df.empty:
        st.info("📊 데이터 수집 중...")
    else:
        total_kws = len(df)
        user_p = df[df['user_search_count'] > 0]
        if not user_p.empty:
            most_c = user_p.iloc[0]['keyword']
        else:
            # 유저 클릭 데이터가 없으면 전체 점수(display_score) 1위 키워드 사용
            most_c = df.iloc[0]['keyword']
        high_g = df.sort_values('search_dod', ascending=False).iloc[0]

        def get_rank_html(start, end):
            sub = df.iloc[start:end]
            html_res = "<div class='ticker-page'>"
            for i, row in sub.iterrows():
                html_res += f"<div class='ranking-item'><span class='ranking-rank'>{i+1}</span><span class='ranking-kw'>{row['keyword']}</span><span class='ranking-score'>{row['display_score']}점</span></div>"
            html_res += "</div>"
            return html_res

        page1_html = get_rank_html(0, 5)
        page2_html = get_rank_html(5, 10) if len(df) > 5 else page1_html
        ticker_html = page1_html + page2_html + page1_html

        # [대칭 구조 적용] 제목 - 밑줄 - 내용 순서 통일
        dashboard_html = f"""
<div class="dashboard-container">
    <div class="ranking-board">
        <div class="ranking-title">🔥 실시간 급상승</div>
        <div class="ticker-viewport">
            <div class="ticker-track">{ticker_html}</div>
        </div>
    </div>
    <div class="insight-board">
        <div class="insight-title">💡 투데이 인사이트</div>
        <div style="flex-grow: 1; display: flex; flex-direction: column; justify-content: flex-start; padding-top: 5px;">
            <div class='insight-text'>오늘 수집된 총 <span class='highlight-text'>{total_kws}개</span>의 키워드 중,</div>
            <div class='insight-text'>유저들이 가장 주목한 키워드는 <span class='highlight-text'>'{most_c}'</span> 입니다.</div>
            <div class='insight-text' style='margin-top:auto; font-size:0.95rem; color:#ADB5BD; border-top: 1px solid rgba(255,255,255,0.1); padding-top: 15px;'>
                🚀 검색 가속도 최고 종목:<br>
                <b style='color: white; font-size: 1.1rem;'>{high_g['keyword']} (+{high_g['search_dod']}%)</b>
            </div>
        </div>
    </div>
</div>
"""
        st.markdown(dashboard_html, unsafe_allow_html=True)

        # --- [5. 하단 표 섹션] ---
        tab1, tab2 = st.tabs(["🌐 전체 랭킹", "🔥 유저 픽 (검색 발생)"])
        with tab1:
            event = st.dataframe(
                df[['trend_type', 'keyword', 'display_score', 'search_combined', 'view_dod_formatted', 'user_search_count']],
                column_config={
                    "trend_type": st.column_config.TextColumn("트렌드 속성", width="medium"),
                    "keyword": st.column_config.TextColumn("키워드", width="medium"),
                    "display_score": st.column_config.ProgressColumn("종합 점수", min_value=0, max_value=100, format="%.1f"),
                    "search_combined": st.column_config.TextColumn("검색량(전일대비)"),
                    "view_dod_formatted": st.column_config.TextColumn("언급량", help="유튜브, 인스타 기반 언급량 데이터"),
                    "user_search_count": st.column_config.NumberColumn("유저 클릭수", format="🔥 %d회")
                }, 
                # ✅ 수정됨: use_container_width=True -> width="stretch"
                width="stretch", hide_index=True, on_select="rerun", selection_mode="single-row"
            )
            if event.selection and len(event.selection.rows) > 0:
                show_trend_detail(df.iloc[event.selection.rows[0]]['keyword'], df)
        with tab2:
            df_u = df[df['user_search_count'] > 0]
            if df_u.empty: st.write("검색 발생 키워드가 없습니다.")
            else:
                event2 = st.dataframe(
                    df_u[['trend_type', 'keyword', 'display_score', 'search_combined', 'user_search_count']],
                    column_config={
                        "trend_type": st.column_config.TextColumn("트렌드 속성", width="medium"),
                        "keyword": st.column_config.TextColumn("키워드", width="medium"),
                        "display_score": st.column_config.ProgressColumn("종합 점수", min_value=0, max_value=100, format="%.1f"),
                        "search_combined": st.column_config.TextColumn("검색량(전일대비)"),
                        "user_search_count": st.column_config.NumberColumn("유저 클릭수", format="🔥 %d회")
                    }, 
                    # ✅ 수정됨: use_container_width=True -> width="stretch"
                    width="stretch", hide_index=True, on_select="rerun", selection_mode="single-row"
                )
                if event2.selection and len(event2.selection.rows) > 0:
                    show_trend_detail(df_u.iloc[event2.selection.rows[0]]['keyword'], df)

except Exception as e:
    st.error(f"시스템 오류: {e}")
