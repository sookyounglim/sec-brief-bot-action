import os
import datetime
import feedparser
import requests
from google import genai

# 환경 변수 설정
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")

client = genai.Client(api_key=GEMINI_API_KEY)

# 1. RSS 뉴스 수집 (최근 24시간 내 기사)
def fetch_rss_news():
    feeds = [
        "https://feeds.feedburner.com/TheHackersNews",
        "https://www.bleepingcomputer.com/feed/",
        "https://krebsonsecurity.com/feed/",
        "https://www.dailysecu.com/rss/clickTop.xml"  # 데일리시큐 RSS
    ]
    news_items = []
    for url in feeds:
        parsed = feedparser.parse(url)
        for entry in parsed.entries[:5]: # 매체당 최근 5개 추출
            news_items.append({
                "title": entry.title,
                "link": entry.link,
                "source": parsed.feed.title if hasattr(parsed.feed, 'title') else "보안뉴스"
            })
    return news_items

# 2. CISA KEV (최근 추가된 취약점) 수집
def fetch_cisa_kev():
    url = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
    res = requests.get(url).json()
    vulnerabilities = res.get("vulnerabilities", [])
    # 가장 최근 등록된 5개
    recent_kev = sorted(vulnerabilities, key=lambda x: x['dateAdded'], reverse=True)[:5]
    return recent_kev

# 3. Ransomware.live (최근 피해 사례) 수집
def fetch_ransomware_activity():
    url = "https://api.ransomware.live/v2/recentvictims"
    res = requests.get(url).json()
    return res[:5] # 최근 5건

# 4. Gemini API 활용 한국어 요약
def summarize_with_gemini(raw_text):
    prompt = f"""
    당신은 최고의 사이버 보안 분석가입니다. 
    다음 수집된 보안 소식들을 한국어로 친절하고 가독성 좋게 번역하고 요약해주세요.
    
    [수집 데이터]
    {raw_text}
    
    [출력 규칙]
    - 뉴스 제목은 자연스러운 한국어로 번역할 것
    - 핵심 내용을 3~4줄로 요약할 것
    - 기술적 맥락(공격 유형, 영향도)을 명확히 전달할 것
    """
    response = client.models.generate_content(
        model='gemini-3.5-flash',
        contents=prompt
    )
    return response.text

# 5. 디스코드 Webhook 전송 (이모티콘 및 Embed 적용)
def send_discord_message(summary_text, news, kevs, ransoms):
    embeds = [
        {
            "title": "🚨 오늘의 주요 보안 뉴스",
            "description": summary_text,
            "color": 3447003
        }
    ]
    
    # KEV 섹션
    kev_fields = []
    for k in kevs:
        kev_fields.append({
            "name": f"⚠️ {k['cveID']} - {k['vendorProject']} {k['product']}",
            "value": f"**설명:** {k['shortDescription']}\n**조치사항:** {k['requiredAction']}",
            "inline": False
        })
    if kev_fields:
        embeds.append({
            "title": "🔥 CISA KEV (실제 악용 확인된 취약점)",
            "fields": kev_fields,
            "color": 15158332
        })

    # 랜섬웨어 동향 섹션
    ransom_fields = []
    for r in ransoms:
        ransom_fields.append({
            "name": f"☣️ [그룹: {r.get('group_name')}] {r.get('post_title')}",
            "value": f"**피해 대상:** {r.get('victim', 'N/A')} | **공개일:** {r.get('discovered')}",
            "inline": False
        })
    if ransom_fields:
        embeds.append({
            "title": "☠️ Ransomware.live 최신 피싱/감염 동향",
            "fields": ransom_fields,
            "color": 10038562
        })

    # 링크 연결 섹션
    link_text = ""
    for item in news:
        link_text += f"• [{item['title']}]({item['link']}) ({item['source']})\n"
    
    embeds.append({
        "title": "🔗 상세 뉴스 바로가기",
        "description": link_text,
        "color": 3066993
    })

    payload = {
        "username": "🛡️ Security Briefing Bot",
        "avatar_url": "https://cdn-icons-png.flaticon.com/512/2092/2092663.png",
        "embeds": embeds
    }
    
    requests.post(DISCORD_WEBHOOK_URL, json=payload)

if __name__ == "__main__":
    news = fetch_rss_news()
    kevs = fetch_cisa_kev()
    ransoms = fetch_ransomware_activity()
    
    raw_data = f"News: {news}\nKEV: {kevs}\nRansomware: {ransoms}"
    ai_summary = summarize_with_gemini(raw_data)
    
    send_discord_message(ai_summary, news, kevs, ransoms)
