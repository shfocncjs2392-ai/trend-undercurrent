import pandas as pd
import requests
from pytrends.request import TrendReq
import json
import time
import math
from datetime import datetime, timedelta, timezone

import re
from collections import Counter

# Selenium 관련 임포트
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
import warnings
import html # HTML 엔티티 디코딩용 추가

from konlpy.tag import Mecab 

import os
from dotenv import load_dotenv

# .env 파일 로드
load_dotenv()

# --- [Groq LLM 초기화 (Mecab 대체)] ---
from groq import Groq

GROQ_API_KEY = os.getenv("GROQ_API_KEY")

try:
    groq_client = Groq(api_key=GROQ_API_KEY)
    print("✅ Groq(LLM) 로드 성공! 이제 AI가 연관어를 추출합니다.")
except Exception as e:
    print(f"⚠️ Groq 로딩 실패: {e}")
    groq_client = None

try:
    # Amazon Linux 환경에 맞춰 설치한 사전 경로 명시
    mecab = Mecab(dicpath='/usr/local/lib/mecab/dic/mecab-ko-dic')
    print("✅ Mecab 로드 성공!")
except Exception as e:
    print(f"⚠️ Mecab 로딩 실패 (설치 및 경로 확인 필요): {e}")
    mecab = None

warnings.simplefilter(action='ignore', category=FutureWarning) # 경고 끄기
pd.set_option('future.no_silent_downcasting', True) # 권장 설정 적용

# --- [1] 설정 및 초기화 ---
NAVER_CLIENT_ID     = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")
YOUTUBE_API_KEY     = os.getenv("YOUTUBE_API_KEY")

pytrends = TrendReq(hl='ko', tz=360)

import pymysql
from sqlalchemy import create_engine

# --- [RDS 연결 정보 설정] ---
DB_HOST = os.getenv("DB_HOST")
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")
DB_NAME = os.getenv("DB_NAME")

def get_db_connection():
    """MySQL(RDS) 연결 객체 반환"""
    return pymysql.connect(
        host=DB_HOST, 
        user=DB_USER, 
        password=DB_PASS, 
        database=DB_NAME, 
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor
    )

def init_db():
    """최초 실행 시 DB와 테이블 생성"""
    # 1. DB 존재 확인 및 생성 (초기 접속은 database 지정 없이)
    conn = pymysql.connect(host=DB_HOST, user=DB_USER, password=DB_PASS, charset='utf8mb4')
    c = conn.cursor()
    c.execute(f"CREATE DATABASE IF NOT EXISTS {DB_NAME}")
    conn.commit()
    conn.close()

    # 2. 테이블 생성
    conn = get_db_connection()
    c = conn.cursor()
    # 주의: MySQL은 TEXT 대신 VARCHAR(255)를 권장하고, REAL 대신 FLOAT를 사용함
    c.execute('''CREATE TABLE IF NOT EXISTS trend_history (
                date DATETIME, 
                keyword VARCHAR(255), 
                category VARCHAR(255), 
                n_search FLOAT, 
                g_search FLOAT, 
                total_search FLOAT, 
                search_dod FLOAT, 
                yt_videos INT, 
                video_dod FLOAT, 
                yt_views INT, 
                view_dod FLOAT, 
                final_score FLOAT, 
                trend_type VARCHAR(255),
                user_search_count INT DEFAULT 0,                
                related_keywords TEXT,
                analysis_txt TEXT,
                PRIMARY KEY (date, keyword) 
            )''')
    conn.commit()
    conn.close()
    
def init_log_table():
    """어뷰징 방지용 IP 로그 테이블 강제 생성 (앱 실행 시 최초 1회)"""
    conn = pymysql.connect(host=DB_HOST, user=DB_USER, password=DB_PASS, charset='utf8mb4')
    c = conn.cursor()
    c.execute(f"CREATE DATABASE IF NOT EXISTS {DB_NAME}")
    conn.commit()
    conn.close()

    # 2. 테이블 생성
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS user_search_logs (
                date DATE,
                keyword VARCHAR(255),
                client_ip VARCHAR(255),
                PRIMARY KEY (date, keyword, client_ip)
            )''')
    conn.commit()
    conn.close()


def get_realtime_seeds_selenium(category_id="50000006"):
    """Selenium을 사용하여 전날/전전날 데이터를 순차적으로 시도하여 20개 추출"""
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument('--remote-debugging-port=9222')
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    
    # 시도할 날짜 리스트 (전날, 전전날)
    target_dates = [
        (datetime.now() - timedelta(days=1)).strftime('%Y%m%d'),
        (datetime.now() - timedelta(days=2)).strftime('%Y%m%d')
    ]
    
    keywords = []
    
    try:
        for date in target_dates:
            print(f"로그: {date} 날짜 데이터 수집 시도 중... (카테고리:{category_id})")
            url = f"https://snxbest.naver.com/keyword/best?categoryId={category_id}&sortType=KEYWORD_POPULAR&periodType=DAILY&ageType=ALL&syncDate={date}"
            
            driver.get(url)
            time.sleep(5) # 동적 로딩 대기
            
            # 아이템 컨테이너 찾기
            items = driver.find_elements(By.XPATH, "//li[contains(@class, 'item')] | //div[contains(@class, 'item')]")
            
            current_attempt_keywords = []
            for item in items:
                try:
                    # 상대 경로로 키워드 추출
                    keyword = item.find_element(By.XPATH, ".//span[1]/strong[2]").text.strip()
                    if keyword:
                        current_attempt_keywords.append(keyword)
                    if len(current_attempt_keywords) >= 20: break
                except:
                    continue
            
            # 해당 날짜에 데이터가 있으면 루프 종료, 없으면 다음 날짜(전전날) 시도
            if current_attempt_keywords:
                print(f"성공: {date} 날짜에서 {len(current_attempt_keywords)}개의 키워드를 확보했습니다.")
                keywords = current_attempt_keywords
                break
            else:
                print(f"알림: {date} 데이터가 아직 업데이트되지 않았습니다. 다음 날짜로 재시도합니다.")

        # 최종 결과 반환
        if keywords:
            return keywords
            
    except Exception as e:
        print(f"수집 에-러: {e}")
    finally:
        driver.quit()
        
    print("경고: 모든 시도가 실패하여 폴백 키워드를 반환합니다.")
    return ["두바이초콜릿", "요아정", "밤티라미수", "저당요거트", "단백질쉐이크"]


# --- [2] 플랫폼별 데이터 수집 모듈 ---

def get_naver_search_trend_advanced(keywords):
    """네이버 데이터랩 API를 통해 최근 7일 상세 추이를 가져와 실시간 증감률 계산"""
    if not keywords: return {}
    url = "https://openapi.naver.com/v1/datalab/search"
    headers = {"X-Naver-Client-Id": NAVER_CLIENT_ID, "X-Naver-Client-Secret": NAVER_CLIENT_SECRET, "Content-Type": "application/json"}
    
    results = {}
    for i in range(0, len(keywords), 5):
        chunk = keywords[i:i+5]
        body = {
            "startDate": (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d'),
            "endDate": datetime.now().strftime('%Y-%m-%d'),
            "timeUnit": "date",
            "keywordGroups": [{"groupName": k, "keywords": [k]} for k in chunk]
        }
        res = requests.post(url, headers=headers, data=json.dumps(body))
        if res.status_code == 200:
            for item in res.json()['results']:
                data_list = item.get('data', [])
                if len(data_list) >= 2:
                    # API 자체 데이터 기반 실시간 증감(오늘 vs 어제) 계산
                    latest_val = data_list[-1]['ratio']
                    prev_val = data_list[-2]['ratio']
                    instant_dod = calculate_dod(latest_val, prev_val)
                    avg_val = sum(d['ratio'] for d in data_list) / len(data_list)
                    results[item['title']] = {"avg": avg_val, "instant_dod": instant_dod}
                elif data_list:
                    results[item['title']] = {"avg": data_list[0]['ratio'], "instant_dod": 0.0}
    return results

def get_google_trend_score(keywords):
    """구글 트렌드 관심도 수집"""
    print(f"로그: 구글 트렌드 수집 중... (대상: {len(keywords)}개)")
    g_results = {}
    for k in keywords:
        try:
            pytrends.build_payload([k], timeframe='now 7-d', geo='KR')
            df = pytrends.interest_over_time()
            if not df.empty:
                g_results[k] = round(df[k].mean(), 2)
            else:
                g_results[k] = 0
        except:
            g_results[k] = 0
        time.sleep(1)
    return g_results

def get_youtube_shorts_advanced(keywords):
    """최근 7일 생산량 + 영상 간 조회수 편차를 분석해 실시간 활성도 계산"""
    youtube_results = {}
    search_url = "https://www.googleapis.com/youtube/v3/search"
    video_url = "https://www.googleapis.com/youtube/v3/videos"
    
    # 최근 7일 / 최근 48시간(급상승 확인용) 날짜 설정
    last_week = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat("T").replace("+00:00", "Z")
    
    for k in keywords:
        try:
            search_params = {
                "part": "snippet", "q": f"{k} shorts", "type": "video",
                "videoDuration": "short", "publishedAfter": last_week,
                "order": "viewCount", "maxResults": 10, "key": YOUTUBE_API_KEY
            }
            res_search = requests.get(search_url, params=search_params)
            
            if res_search.status_code == 200:
                data = res_search.json()
                total_vids = data.get("pageInfo", {}).get("totalResults", 0)
                items = data.get("items", [])
                
                avg_views = 0
                instant_view_dod = 0.0 # DB 없을 때의 임시 조회수 증감
                
                if items:
                    v_ids = [i['id']['videoId'] for i in items]
                    v_res = requests.get(video_url, params={"part": "statistics,snippet", "id": ",".join(v_ids), "key": YOUTUBE_API_KEY})
                    if v_res.status_code == 200:
                        v_items = v_res.json().get("items", [])
                        views = [int(v['statistics'].get('viewCount', 0)) for v in v_items]
                        avg_views = sum(views) / len(views)
                        
                        # [자체 계산 로직] 최신 영상(1-2위)과 그 외 영상의 조회수 차이를 가속도로 간주
                        if len(views) >= 3:
                            instant_view_dod = calculate_dod(views[0], (sum(views[1:5])/4))
                
                youtube_results[k] = {"volume": total_vids, "views": int(avg_views), "instant_dod": instant_view_dod}
            else:
                youtube_results[k] = {"volume": 0, "views": 0, "instant_dod": 0.0}
        except:
            youtube_results[k] = {"volume": 0, "views": 0, "instant_dod": 0.0}
    return youtube_results

def get_naver_news_titles(keyword):
    """네이버 뉴스에서 검색 결과 제목 20개를 가져옴"""
    url = "https://openapi.naver.com/v1/search/news.json"
    headers = {
        "X-Naver-Client-Id": NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET
    }
    params = {"query": keyword, "display": 20, "sort": "sim"}
    
    try:
        response = requests.get(url, headers=headers, params=params)
        if response.status_code == 200:
            items = response.json().get('items', [])
            # 1. HTML 엔티티 디코딩 (&quot; -> ") 후 태그 제거
            titles = [re.sub(r'<[^>]+>', '', html.unescape(item['title'])) for item in items]
            return titles
    except Exception as e:
        print(f"❌ 네이버 뉴스 수집 실패 ({keyword}): {e}")
    return []
def manual_extract_related_keywords(keyword, titles):
    """
    [통계 기반 자동 복합명사 추출 알고리즘]
    하드코딩 없이 문맥 내 동시 출현 빈도(Co-occurrence)를 분석하여 연관어를 추출합니다.
    """
    if not titles or not mecab: return ""

    # 1. 초강력 불용어 (이건 NLP 모델에서도 필수적으로 거르는 노이즈 리스트)
    stopwords = {
        keyword, '뉴스', '오늘', '출시', '공개', '결국', '이유', '포토', '종합', '단독', '사진', '개최',
                '이것', '저것', '그것', '무엇', '누구', '어디', '여기', '저기', '음식', '사람', '최고', '최근', 
                '요즘', '우리', '진짜', '정말', '비교', '추천', '특징', '정리', '방법', '사용', '생각', '시간', 
                '정도', '경우', '관련', '확인', '모습', '시작', '영상', '내일', '어제', '이번', '주말', '올해', 
                '내년', '대비', '주의', '진행', '예정', '준비', '완료', '모두', '전체', '일부', '자체', '때문',
                '사실', '이름', '마음', '사이', '최초', '역대', '등장', '증가', '감소', '하락', '상승', '돌파',
                '메뉴', '세트', '점유', '비율', '기준', '연속', '기록', '달성', '시장', '업계', '기업', '브랜드',
                '제품', '상품', '판매', '매출', '수익', '영업', '이익', '투자', '주가', '주식', '종목', '특징주',
                '지원', '혜택', '이벤트', '할인', '행사', '프로모션', '고객', '소비자', '이용자', '유저', '대표',
                '사장', '회장', '직원', '관계자', '전문가', '분석', '평가', '전망', '예상', '기대', '우려', '논란',
                '화제', '인기', '유행', '트렌드', '이슈', '핵심', '주목', '관심', '집중', '확대', '축소', '강화'
                }

    # 2. 동적 키워드 병합 (정규식 활용)
    # 검색어가 '요거트'일 때 기사에 '요 거트'로 나오는 등 띄어쓰기 오류를 하드코딩 없이 자동 보정
    import re
    kw_no_space = keyword.replace(" ", "")
    kw_chars = list(kw_no_space)
    if len(kw_chars) > 1:
        # 생성되는 정규식 예: r'요\s*거\s*트'
        dynamic_pattern = re.compile(r'\s*'.join(kw_chars))
        titles = [dynamic_pattern.sub(kw_no_space, t) for t in titles]

    unigram_counts = Counter()
    bigram_counts = Counter()

    for title in titles:
        pos_tags = mecab.pos(title)
        current_nouns = []

        # 문장 내 명사 추출
        for word, tag in pos_tags:
            # 일반명사, 고유명사, 영단어 추출
            if tag in ['NNG', 'NNP', 'SL']: 
                # 1글자짜리 무의미한 한글 명사 1차 필터링
                if len(word) >= 2 or tag == 'SL': 
                    current_nouns.append(word)
            else:
                # 조사가 나오면 지금까지 모인 명사들을 조합 계산에 넣음
                if current_nouns:
                    valid_unigrams = [w for w in current_nouns if w not in stopwords and not w.isdigit()]

                    # 단일 단어(Unigram) 카운트
                    for w in valid_unigrams:
                        unigram_counts[w] += 1

                    # 연속된 두 단어(Bigram) 카운트 (예: '동물' + '복지' = '동물복지')
                    if len(valid_unigrams) >= 2:
                        for i in range(len(valid_unigrams) - 1):
                            bigram = valid_unigrams[i] + valid_unigrams[i+1]
                            if bigram not in stopwords and not bigram.isdigit():
                                bigram_counts[bigram] += 1

                    current_nouns = [] # 초기화

        # 문장 끝에 남은 명사들 처리
        if current_nouns:
            valid_unigrams = [w for w in current_nouns if w not in stopwords and not w.isdigit()]
            for w in valid_unigrams:
                unigram_counts[w] += 1
            if len(valid_unigrams) >= 2:
                for i in range(len(valid_unigrams) - 1):
                    bigram = valid_unigrams[i] + valid_unigrams[i+1]
                    if bigram not in stopwords and not bigram.isdigit():
                        bigram_counts[bigram] += 1

    # 3. 빈도 상쇄 알고리즘 (부분 단어 잠식 해결)
    final_counts = unigram_counts.copy()

    for bigram, b_count in bigram_counts.items():
        # 두 단어가 연속해서 2번 이상 붙어 다녔다면 복합명사로 인정
        if b_count >= 2:
            final_counts[bigram] = b_count

            # 복합명사에 포함된 개별 단어들의 독립 카운트를 차감
            # 예: '동물복지'가 8번 나왔으므로, '동물'과 '복지'의 기존 카운트에서 8씩 뺌
            for unigram in list(unigram_counts.keys()):
                if unigram in bigram and unigram != bigram:
                    final_counts[unigram] -= b_count

    # 4. 최종 정제 및 정렬
    # 카운트가 0 이하가 된(복합명사에 완전히 흡수된) 단어 제거, 타겟 키워드 본인 제거
    clean_scores = {w: c for w, c in final_counts.items() if c > 0 and w != kw_no_space}

    # 빈도수 순으로 정렬
    sorted_final = sorted(clean_scores.items(), key=lambda item: item[1], reverse=True)

    # 상위 5개 추출
    most_common = [w for w, c in sorted_final[:5]]
    return ", ".join(most_common)

def extract_related_keywords(keyword, titles):
    if not titles or not groq_client: return ""
    
    titles_text = "\n".join(titles)
    
    system_prompt = f"""너는 트렌드 데이터분석가야. 
사용자가 '{keyword}'를 왜 검색했는지, 현재 어떤 사건이나 현상이 벌어지고 있는지 '핵심 트리거'를 찾아야 해.

[분석 가이드]:
1. 검색어와 완전히 똑같은 단어는 제외할 것.
2. 무의미한 불용어는 절대 포함하지 말 것.
2. 현상의 본질 추출: 단순히 관련 있는 물건이 아니라, 이 키워드가 뉴스에 도배된 '이유'와 '핵심 데이터'를 찾아.
3. 숫자와 가치에 주목: 구체적인 현상을 우선순위로 둬.
4. 지엽적 단어 제거: 기사 말미에 예시로 나온 품목이나 무의미한 고유명사는 철저히 배제해.
5. 연관성 검증: "{keyword} 때문에 이 단어가 나왔는가?"를 자문하고, 그렇다인 것만 골라.
6. 감성 분석: {keyword}의 연관 키워드가 긍정인지 부정인지 판단

출력 형식: 반드시 쉼표(,)로만 구분된 5개의 핵심 명사 + 6번의 감성 분석 결과만 출력해. (예: 동물복지란,점유율급증,가치소비,3배증가,착한소비,긍정)"""

    try:
        completion = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile", 
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"검색어: {keyword}\n뉴스 리스트:\n{titles_text}"}
            ],
            temperature=0, # 분석의 일관성을 위해 가장 낮은 온도로 설정
            max_tokens=60
        )
        
        result = completion.choices[0].message.content.strip()
        result = re.sub(r'[^가-힣a-zA-Z0-9,]', '', result)
        
        return result
        
    except Exception as e:
        print(f"⚠️ AI 연관어 추출 실패 ({e}) -> Mecab 우회 분석 시작")
        return manual_extract_related_keywords(keyword, titles)

def get_ai_insight(kw, related, total_search, search_dod, yt_views, yt_videos, trend_type):
    """
    Groq API를 사용하여 현재 데이터 지표와 문맥을 결합한 리포트 생성
    """
    # 1. 현재 데이터 요약
    search_info = f"검색량: {total_search} ({search_dod}% 증감)"
    yt_info = f"유튜브 조회수: {yt_views}회, 영상수: {yt_videos}개"
    

    prompt = f"""너는 냉철하고 철저하게 '데이터'에만 기반하여 판단하는 트렌드 분석가야.
                주어진 [데이터 정보]의 '수치'를 절대적인 팩트로 삼아 '{kw}'의 현재 상황을 진단해.

                [데이터 정보]
                - 키워드: {kw}
                - 검색량: {total_search} ({search_dod}% 증감)
                - 유튜브 화력: 조회수 {yt_views}회, 영상수 {yt_videos}개
                - 연관 키워드: {related}
                - 현재 상태(Trend Type): {trend_type}

                [절대 준수 가이드]
                1. 팩트 폭격: 수치가 0이거나 매우 낮다면 억지로 "트렌드"라고 포장하지 마. "데이터 수집 시점의 차이"나 "실제로는 주목받고 있다" 같은 변명과 추측은 절대 금지. 있는 그대로 관심도가 저조하거나 초기 단계임을 명확히 해.
                2. 수치 인용: 분석 내용에 검색량, 증감률, 조회수 등 주어진 수치를 반드시 포함하여 객관성을 높여.
                3. 상태(Trend Type) 해설: '{trend_type}' 상태로 분류된 이유를 현재 데이터 수치와 논리적으로 연결하여 설명해. (예: 수치가 낮아 관측 중, 검색량이 폭발하여 급상승 등)
                4. 맥락 추론: 수치가 낮더라도, '연관 키워드({related})'를 바탕으로 현재 어떤 뉴스나 맥락에서 이 단어가 언급되고 있는지 구체적인 브랜드나 사건을 포함하여 3줄 이내로 간결하게 요약해."""

    try:
        completion = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=200
        )
        return completion.choices[0].message.content.strip()
    except:
        return "분석 진행 중..."
    
# --- [3] 데이터 분석 및 증감률(DoD) 처리 로직 ---

def calculate_dod(current_val, past_val):
    """전일 대비 증감률(%) 계산 안전 함수"""
    if past_val == 0:
        return 100.0 if current_val > 0 else 0.0
    return round(((current_val - past_val) / past_val) * 100, 2)

def analyze_and_save(keywords, category_name="Auto"):
    n_data_map = get_naver_search_trend_advanced(keywords)
    g_scores = get_google_trend_score(keywords)
    
    # [핵심] 수동 분석일 경우 유튜브 API 호출 완전 스킵 (할당량 방어)
    is_manual = category_name.startswith("Manual")
    
    if is_manual:
        print(f"로그: 수동 분석 모드 - 유튜브 API를 건너뜁니다. (대상: {len(keywords)}개)")
        yt_data_map = {} # 빈 딕셔너리로 넘겨서 트래픽 0 소모
    else:
        yt_data_map = get_youtube_shorts_advanced(keywords)
    
    conn = get_db_connection()
    cursor = conn.cursor()
    report_list = []
    
    for k in keywords:
        titles = get_naver_news_titles(k)
        
        n_info = n_data_map.get(k, {"avg": 0, "instant_dod": 0.0})
        yt_info = yt_data_map.get(k, {"volume": 0, "views": 0, "instant_dod": 0.0})
        
        c_n_search = n_info['avg']
        c_g_search = g_scores.get(k, 0)
        c_total_search = round((c_n_search + c_g_search) / 2, 2)
        c_yt_videos = yt_info['volume']
        c_yt_views = yt_info['views']
        
        # --- [DB 확인 및 증감률 결정] ---
        # 1. 쿼리에 user_search_count 추가
        cursor.execute("""
            SELECT total_search, yt_videos, yt_views, user_search_count 
            FROM trend_history 
            WHERE keyword = %s ORDER BY date DESC LIMIT 1
        """, (k,))
        yesterday = cursor.fetchone()
        
        if yesterday:
            y_search = yesterday.get('total_search', 0) if isinstance(yesterday, dict) else yesterday[0]
            y_videos = yesterday.get('yt_videos', 0) if isinstance(yesterday, dict) else yesterday[1]
            y_views = yesterday.get('yt_views', 0) if isinstance(yesterday, dict) else yesterday[2]
            
            # 2. 어제 유저 검색량 안전하게 추출 (DB에 NULL이 있을 수 있으므로 0으로 처리)
            raw_user_count = yesterday.get('user_search_count', 0) if isinstance(yesterday, dict) else (yesterday[3] if len(yesterday) > 3 else 0)
            y_user_count = raw_user_count if raw_user_count is not None else 0
            
            search_dod = calculate_dod(c_total_search, y_search)
            video_dod = calculate_dod(c_yt_videos, y_videos)
            view_dod = calculate_dod(c_yt_views, y_views)
            is_new = False
        else:
            # DB 데이터 없을 시 API 자체 계산값(instant_dod) 사용
            search_dod = n_info['instant_dod']
            video_dod = 0.0 
            view_dod = yt_info['instant_dod']
            y_user_count = 0 # 신규 키워드는 어제 유저 기록이 0
            is_new = True

        # 3. [핵심] 어제 유저 기록을 바탕으로 신뢰도 보너스 계산 (1회당 0.2점, 최대 10점)
        trust_bonus = min(y_user_count * 0.2, 10)

        # --- [스코어링 및 유형 판별] ---
        if c_yt_views == 0:
            # 1. 체급 점수: 상한선(min) 제거. 100점 만점 중 70점 비중으로 정비례 환산
            base_search_score = c_total_search * 0.7 
            
            # 2. 가속도(증감률) 점수: 계단식(Step)이 아닌 연속적(Continuous) 반영
            if search_dod > 0:
                # 상승 시: 최대 30점 한도 내에서 비례 증가
                growth_bonus = min(search_dod * 0.4, 30) 
            else:
                # 하락 시: 최대 -10점 한도 내에서 비례 페널티 (예: -8.37% -> -1.67점)
                growth_bonus = max(search_dod * 0.2, -10)
            
            # 3. 최종 점수: 0~100 범위로 정규화 (+ 어제 유저 신뢰도 보너스 추가)
            final_score = round(max(0, min(100, base_search_score + growth_bonus + trust_bonus)), 2)

            # 4. 태그 판별 (점수 산출과 독립적으로 직관적 기준 적용)
            if search_dod >= 50:
                trend_type = "⚡ 실시간 검색 폭발"
            elif search_dod >= 15:
                trend_type = "📈 검색 수요 상승 중"
            elif search_dod <= -10:
                trend_type = "📉 수요 조정기"
            else:
                trend_type = "⏳ 관측 중"
        else:
            # 1. 검색 체급 (30%): 천장 없는 선형 점수
            score_s_base = c_total_search * 0.3
            
            # 2. 검색 가속도 (20%): 연속적 반영
            if search_dod > 0:
                score_s_dod = min(search_dod * 0.2, 20) # 최대 20점 보너스
            else:
                score_s_dod = max(search_dod * 0.1, -5) # 하락 시 페널티 완화
                
            # 3. 유튜브 화력 (50%): 조회수(20) + 영상수(10) + 화력증감(20)
            # 로그 스케일을 사용하되, 가중치를 정밀하게 배분
            score_v_views = min((math.log10(c_yt_views + 1) / 6) * 20, 20)
            score_v_vol = min((math.log10(c_yt_videos + 1) / 3) * 10, 10)
            
            if view_dod > 0:
                score_v_dod = min(view_dod * 0.2, 20)
            else:
                score_v_dod = max(view_dod * 0.1, -5)

            # 4. 최종 합산 (0~100 정규화) (+ 어제 유저 신뢰도 보너스 추가)
            final_score = round(max(0, min(100, score_s_base + score_s_dod + score_v_views + score_v_vol + score_v_dod + trust_bonus)), 2)

            # --- [유형 판별 가이드] ---
            if score_v_views >= 15 and score_v_vol < 5:
                trend_type = "💎 블루오션 (공급 부족)"
            elif score_v_views >= 15 and score_v_vol >= 7:
                trend_type = "🔥 옴니채널 메가트렌드"
            elif search_dod > 30 or view_dod > 30:
                trend_type = "🚀 급상승 저류 포착"
            elif is_new and final_score >= 40:
                trend_type = "✨ 신규 탐지: 라이징"
            else:
                trend_type = "👀 관측 중"

        # 🚀 여기서 Groq API가 호출되어 연관어를 기가 막히게 뽑아줌!
        related_kws  = extract_related_keywords(k, titles)
        analysis_txt = get_ai_insight(k,titles,c_total_search,search_dod,c_yt_views,c_yt_videos,trend_type)

        # --- [DB UPSERT] ---
        now = datetime.now().strftime('%Y-%m-%d')
        sql = '''INSERT INTO trend_history 
                    (date, keyword, category, n_search, g_search, total_search, search_dod, 
                    yt_videos, video_dod, yt_views, view_dod, final_score, trend_type, 
                    user_search_count, related_keywords, analysis_txt)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE 
                    n_search=VALUES(n_search), 
                    g_search=VALUES(g_search),
                    total_search=VALUES(total_search), 
                    search_dod=VALUES(search_dod), 
                    yt_videos=VALUES(yt_videos), 
                    video_dod=VALUES(video_dod),
                    yt_views=VALUES(yt_views), 
                    view_dod=VALUES(view_dod), 
                    final_score=VALUES(final_score), 
                    trend_type=VALUES(trend_type),
                    user_search_count=VALUES(user_search_count),
                    related_keywords=VALUES(related_keywords),
                    analysis_txt=VALUES(analysis_txt)
                    '''
                 
        params = (
            now, k, category_name, float(c_n_search), float(c_g_search), 
            float(c_total_search), float(search_dod), int(c_yt_videos), 
            float(video_dod), int(c_yt_views), float(view_dod), 
            float(final_score), trend_type, 0, related_kws, analysis_txt
        )
        
        cursor.execute(sql, params)
        
        report_list.append({"키워드": k, "최종점수": final_score, "유형": trend_type, "연관어": related_kws})
        
    conn.commit()
    conn.close()
    return pd.DataFrame(report_list).sort_values(by="최종점수", ascending=False)

# --- [4] 메인 실행부 ---
def main():
    init_db()
    init_log_table()
    
    print("\n[Trend-Undercurrent Engine v2.5 (Groq LLM Powered)]")
    mode = input("1: 수동 분석, 2: 자동 발굴 -> ")
    
    if mode == '1':
        keywords = [k.strip() for k in input("키워드(쉼표 구분): ").split(',')]
        print(analyze_and_save(keywords, "Manual"))
        
    elif mode == '2':
        cat_id = input("1:식품(50000006), 2:디지털/가전(50000003) 선택 -> ")
        target = "50000006" if cat_id == '1' else "50000003"
        seeds = get_realtime_seeds_selenium(target)
        print(analyze_and_save(seeds, f"Auto_{target}"))

if __name__ == "__main__":
    main()
