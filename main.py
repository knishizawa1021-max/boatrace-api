from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import httpx
from bs4 import BeautifulSoup
import asyncio
from datetime import datetime, date
import re
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

cache = {}
CACHE_TTL = 60

VENUE_CODES = {
    "kiryu":"01","toda":"02","edogawa":"03","heiwajima":"04",
    "tamagawa":"05","hamanako":"06","gamagori":"07","tsuru":"08",
    "mikuni":"09","biwako":"10","suminoe":"11","amagasaki":"12",
    "naruto":"13","marugame":"14","kojima":"15","miyajima":"16",
    "tokuyama":"17","shimonoseki":"18","wakamatsu":"19","ashiya":"20",
    "karatsu":"21","omura":"22","fukuoka":"23","saga":"24"
}

BASE_URL = "https://www.boatrace.jp"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; BoatRaceApp/1.0; personal use)", "Accept-Language": "ja-JP,ja;q=0.9"}

async def fetch_with_retry(url, retries=3):
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        for i in range(retries):
            try:
                await asyncio.sleep(1)
                r = await client.get(url, headers=HEADERS)
                if r.status_code == 200:
                    return r.text
            except Exception as e:
                logger.warning(f"Fetch error ({i+1}/{retries}): {e}")
                await asyncio.sleep(2)
    return None

def get_today():
    return date.today().strftime("%Y%m%d")

def parse_exhibition_times(html):
    soup = BeautifulSoup(html, "lxml")
    result = {}
    try:
        rows = soup.select("table.is-w748 tbody tr")
        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 5:
                continue
            course_el = row.select_one("td.is-fs14")
            if not course_el:
                continue
            m = re.search(r"\d", course_el.get_text())
            if not m:
                continue
            course = m.group()
            et_cells = row.select("td.is-fs14")
            et = None
            st = None
            all_tds = row.find_all("td")
            for idx, td in enumerate(all_tds):
                txt = td.get_text(strip=True)
                if re.match(r"^6\.\d{2}$", txt):
                    et = txt
                if re.match(r"^[FLS]?\d?\.\d{2}$", txt) and txt != et:
                    st = txt
            result[course] = {"exhibitionTime": et, "startTiming": st}
    except Exception as e:
        logger.error(f"Parse error: {e}")
    return result

def parse_odds(html):
    soup = BeautifulSoup(html, "lxml")
    odds = {}
    try:
        tbody = soup.select_one("table.is-w748 tbody")
        if not tbody:
            return odds
        rows = tbody.find_all("tr")
        combo_idx = 0
        combos = []
        for i in range(1,7):
            for j in range(1,7):
                for k in range(1,7):
                    if len({i,j,k}) == 3:
                        combos.append(f"{i}-{j}-{k}")
        cells = soup.select("td.oddsPoint, td.is-oddsPoint")
        for idx, cell in enumerate(cells):
            txt = cell.get_text(strip=True)
            if idx < len(combos) and re.match(r"[\d.]+", txt):
                try:
                    odds[combos[idx]] = float(txt)
                except:
                    pass
    except Exception as e:
        logger.error(f"Odds parse error: {e}")
    return odds

@app.get("/")
async def root():
    return {"status": "ok", "message": "BoatRace API Server"}

@app.get("/api/health")
async def health():
    return {"status": "healthy", "time": datetime.now().isoformat()}

@app.get("/api/exhibition/{venue_id}/{race_no}")
async def get_exhibition(venue_id: str, race_no: int):
    cache_key = f"ex_{venue_id}_{race_no}_{get_today()}"
    now = datetime.now().timestamp()
    if cache_key in cache and now - cache[cache_key]["ts"] < CACHE_TTL:
        return cache[cache_key]["data"]
    code = VENUE_CODES.get(venue_id)
    if not code:
        return {"error": "Invalid venue", "times": {}}
    url = f"{BASE_URL}/owpc/pc/race/beforeinfo?rno={race_no}&jcd={code}&hd={get_today()}"
    html = await fetch_with_retry(url)
    if not html:
        return {"error": "Failed to fetch", "times": {}}
    times = parse_exhibition_times(html)
    data = {"venue_id": venue_id, "race_no": race_no, "times": times, "fetched_at": datetime.now().isoformat()}
    cache[cache_key] = {"data": data, "ts": now}
    return data

@app.get("/api/odds/{venue_id}/{race_no}")
async def get_odds(venue_id: str, race_no: int):
    cache_key = f"odds_{venue_id}_{race_no}_{get_today()}"
    now = datetime.now().timestamp()
    if cache_key in cache and now - cache[cache_key]["ts"] < CACHE_TTL:
        return cache[cache_key]["data"]
    code = VENUE_CODES.get(venue_id)
    if not code:
        return {"error": "Invalid venue", "odds": {}}
    url = f"{BASE_URL}/owpc/pc/race/odds3t?rno={race_no}&jcd={code}&hd={get_today()}"
    html = await fetch_with_retry(url)
    if not html:
        return {"error": "Failed to fetch", "odds": {}}
    odds = parse_odds(html)
    data = {"venue_id": venue_id, "race_no": race_no, "odds": odds, "fetched_at": datetime.now().isoformat()}
    cache[cache_key] = {"data": data, "ts": now}
    return data
