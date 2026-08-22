import os
import feedparser
import requests
from google import genai
import time
from google.genai import errors

# 환경 변수 설정
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
RANSOMWARE_API_KEY = os.environ.get("RANSOMWARE_API_KEY")  # 1. API 키 환경 변수 추가

client = genai.Client(api_key=GEMINI_API_KEY)

# 1. RSS 뉴스 수집 (뉴스사별 최신 2개씩 수집)
def fetch_rss_news():
    feeds = [
        ("The Hacker News", "https://feeds.feedburner.com/TheHackersNews"),
        ("BleepingComputer", "https://www.bleepingcomputer.com/feed/"),
        ("Krebs on Security", "https://krebsonsecurity.com/feed/"),
        ("DailySecu", "https://www.dailysecu.com/rss/clickTop.xml")
    ]
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    }
    news_items = []

    for default_source, url in feeds:
        try:
            res = requests.get(url, headers=headers, timeout=10)
            res.raise_for_status()

            parsed = feedparser.parse(res.content)
            source_title = parsed.feed.title if hasattr(parsed.feed, 'title') and parsed.feed.title else default_source

            # 언론사별 최신 기사 2개씩 추출
            for entry in parsed.entries[:2]:
                news_items.append({
                    "title": entry.title,
                    "link": entry.link,
                    "source": source_title
                })
        except Exception as e:
            print(f"[!] [{default_source}] 수집 실패: {e}")

    return news_items

# 2. CISA KEV 5개 수집
def fetch_cisa_kev():
    url = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
    try:
        res = requests.get(url, headers=headers, timeout=10)
        res.raise_for_status()
        vulnerabilities = res.json().get("vulnerabilities", [])
        return sorted(vulnerabilities, key=lambda x: x['dateAdded'], reverse=True)[:5]
    except Exception as e:
        print(f"[!] CISA KEV 수집 실패: {e}")
        return []

# 3. Ransomware.live 5개 수집 (API PRO 기준)
def fetch_ransomware_activity():
    if not RANSOMWARE_API_KEY:
        print("[!] RANSOMWARE_API_KEY 환경 변수가 설정되지 않았습니다.")
        return []

    url = "https://api-pro.ransomware.live/victims/recent?order=discovered"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "accept": "application/json",
        "X-API-KEY": RANSOMWARE_API_KEY.strip()
    }

    try:
        res = requests.get(url, headers=headers, timeout=10)
        res.raise_for_status()
        data = res.json()

        # API PRO 응답 구조: {'client': '...', 'count': 100, 'victims': [...]}
        # victims 키에서 리스트 데이터 추출
        victims_list = data.get("victims", []) if isinstance(data, dict) else data

        parsed_victims = []
        for item in victims_list[:5]:
            parsed_victims.append({
                "group": item.get("group", "Unknown"),
                "victim": item.get("victim", "Unknown"),
                "country": item.get("country", "N/A"),
                "activity": item.get("activity", "N/A"),
                "discovered": item.get("discovered", item.get("attackdate", "N/A")),
                "website": item.get("website", "")
            })
        return parsed_victims

    except Exception as e:
        print(f"[!] Ransomware.live API 호출 실패: {e}")
        return []

# 4. 섹션별 Gemini 요약 생성
def summarize_section(category_title, raw_data, max_retries=4):
    if not raw_data:
        return "수집된 데이터가 없거나 수집 중 오류가 발생했습니다."

    prompt = f"""
    당신은 전문 사이버 보안 분석가입니다.
    다음 [{category_title}] 수집 데이터를 디스코드 채널에 올릴 브리핑 형태로 요약해주세요.

    [수집 데이터]
    {raw_data}

    [출력 규칙]
    - 핵심 내용을 자연스러운 한국어로 항목별(불렛포인트) 정리할 것
    - 기사나 출처 링크가 있는 경우 디스코드 마크다운 형식([제목](링크))으로 가독성 좋게 포함할 것
    - 읽기 편하도록 적절한 이모티콘을 사용할 것
    - 총 길이는 1,000자 이내로 작성할 것
    """
    last_err = None
    for attempt in range(1, max_retries + 1):
        try:
            chat = client.chats.create(model='gemini-3.5-flash')
            response = chat.send_message(prompt)
            return response.text
        except errors.ServerError as e:
            last_err = e
            wait = min(60, 5 * (2 ** (attempt - 1)))  # 5, 10, 20, 40, ... 최대 60초
            print(f"[!] [{category_title}] Gemini 호출 실패 (시도 {attempt}/{max_retries}): {e}. {wait}초 후 재시도")
            time.sleep(wait)
        except errors.APIError as e:
            # 429 Too Many Requests 등도 같이 재시도하고 싶으면 여기서 처리
            last_err = e
            wait = min(60, 5 * (2 ** (attempt - 1)))
            print(f"[!] [{category_title}] Gemini API 오류 (시도 {attempt}/{max_retries}): {e}. {wait}초 후 재시도")
            time.sleep(wait)

    print(f"[!] [{category_title}] 최종 실패: {last_err}")
    return f"⚠️ Gemini API 과부하로 이번 회차 요약 생성에 실패했습니다. (원본 데이터 {len(raw_data)}건 수집됨)"

# 5. 디스코드 Webhook 메시지 분할 전송 (1,900자 초과 시 자동 연속 전송)
def send_discord_message(title, content, color):
    if not content:
        return

    chunk_limit = 1900
    chunks = []
    current_chunk = ""

    for line in content.splitlines(keepends=True):
        if len(current_chunk) + len(line) > chunk_limit:
            if current_chunk.strip():
                chunks.append(current_chunk.strip())
            current_chunk = line
        else:
            current_chunk += line

    if current_chunk.strip():
        chunks.append(current_chunk.strip())

    for idx, chunk in enumerate(chunks):
        msg_title = title if idx == 0 else f"{title} (이어서)"
        payload = {
            "username": "🛡️ Security Briefing Bot",
            "avatar_url": "https://cdn-icons-png.flaticon.com/512/2092/2092663.png",
            "embeds": [
                {
                    "title": msg_title,
                    "description": chunk,
                    "color": color
                }
            ]
        }
        res = requests.post(DISCORD_WEBHOOK_URL, json=payload)
        res.raise_for_status()

# 6. 메인 실행 프로세스
if __name__ == "__main__":
    # 1) 주요 보안 뉴스 (메시지 1)
    try:
        news_data = fetch_rss_news()
        news_summary = summarize_section("주요 보안 뉴스", news_data)
        send_discord_message("🚨 오늘의 주요 보안 뉴스 요약", news_summary, 3447003)
    except Exception as e:
        print(f"[!] 뉴스 섹션 처리 중 오류: {e}")

    # 2) CISA KEV (메시지 2)
    try:
        kev_data = fetch_cisa_kev()
        kev_summary = summarize_section("CISA KEV 취약점", kev_data)
        send_discord_message("🔥 CISA KEV (실제 악용 확인된 취약점)", kev_summary, 15158332)
    except Exception as e:
        print(f"[!] KEV 섹션 처리 중 오류: {e}")

    # 3) 랜섬웨어 동향 (메시지 3)
    try:
        ransom_data = fetch_ransomware_activity()
        ransom_summary = summarize_section("랜섬웨어 최신 피해 사례", ransom_data)
        send_discord_message("☠️ Ransomware.live 최신 감염 동향", ransom_summary, 10038562)
    except Exception as e:
        print(f"[!] 랜섬웨어 섹션 처리 중 오류: {e}")
