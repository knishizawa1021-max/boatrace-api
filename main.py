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
CACHE_TTL_SHORT = 60
CACHE_TTL_LONG = 3600

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

async def fetch(url, retries=3):
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

def today():
    return date.today().strftime("%Y%m%d")

def cached(key, ttl, data):
    cache[key] = {"data": data, "ts": datetime.now().timestamp()}
    return data

def get_cache(key, ttl):
    if key in cache and datetime.now().timestamp() - cache[key]["ts"] < ttl:
        return cache[key]["data"]
    return None

def parse_exhibition(html):
    soup = BeautifulSoup(html, "lxml")
    result = {}
    try:
        rows = soup.select("table.is-w748 tbody tr")
        for row in rows:
            course_el = row.select_one("td.is-fs14")
            if not course_el:
                continue
            m = re.search(r"\d", course_el.get_text())
            if not m:
                continue
            course = m.group()
            et = st = None
            for td in row.find_all("td"):
                txt = td.get_text(strip=True)
                if re.match(r"^6\.\d{2}$", txt):
                    et = txt
                elif re.match(r"^[FLS]?\d?\.\d{2}$", txt) and txt != et:
                    st = txt
            result[course] = {"exhibitionTime": et, "startTiming": st}
    except Exception as e:
        logger.error(f"Exhibition parse error: {e}")
    return result

def parse_odds(html):
    soup = BeautifulSoup(html, "lxml")
    odds = {}
    try:
        combos = [f"{i}-{j}-{k}" for i in range(1,7) for j in range(1,7) for k in range(1,7) if len({i,j,k})==3]
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

def parse_racelist(html):
    soup = BeautifulSoup(html, "lxml")
    races = []
    try:
        items = soup.select(".raceList_item, .is-race")
        for i, item in enumerate(items, 1):
            time_el = item.select_one(".raceList_itemTime, .time")
            status_el = item.select_one(".raceList_itemStatus, .status")
            time_str = time_el.get_text(strip=True) if time_el else f"{9+i}:00"
            status_raw = status_el.get_text(strip=True) if status_el else ""
            status = "upcoming"
            if "展示" in status_raw: status = "exhibition"
            elif "競走" in status_raw or "レース" in status_raw: status = "racing"
            elif "終了" in status_raw or "確定" in status_raw: status = "finished"
            races.append({"no": i, "startTime": time_str, "status": status})
    except Exception as e:
        logger.error(f"Racelist parse error: {e}")
    return races

def parse_racecard(html):
    soup = BeautifulSoup(html, "lxml")
    racers = []
    try:
        rows = soup.select("tbody.is-fs12 tr, .table1 tbody tr")
        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 5:
                continue
            course_el = row.select_one(".table1_bodyNumber, td:first-child")
            if not course_el:
                continue
            m = re.search(r"\d", course_el.get_text())
            if not m:
                continue
            course = int(m.group())
            name_el = row.select_one(".is-fs18, .table1_bodyName")
            name = name_el.get_text(strip=True) if name_el else "---"
            racer = {"course": course, "name": name}
            racers.append(racer)
    except Exception as e:
        logger.error(f"Racecard parse error: {e}")
    return racers

def parse_result(html):
    soup = BeautifulSoup(html, "lxml")
    results = []
    try:
        rows = soup.select(".table1 tbody tr, table.is-w748 tbody tr")
        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 3:
                continue
            rank_el = cells[0]
            course_el = cells[1] if len(cells) > 1 else None
            name_el = cells[2] if len(cells) > 2 else None
            rank = rank_el.get_text(strip=True)
            course = course_el.get_text(strip=True) if course_el else ""
            name = name_el.get_text(strip=True) if name_el else ""
            if re.match(r"[1-6]", rank) and re.match(r"[1-6]", course):
                results.append({"rank": int(rank), "course": int(course), "name": name})
    except Exception as e:
        logger.error(f"Result parse error: {e}")
    return results

@app.get("/")
async def root():
    return {"status": "ok", "message": "BoatRace API Server"}

@app.get("/api/health")
async def health():
    return {"status": "healthy", "time": datetime.now().isoformat()}

@app.get("/api/racelist/{venue_id}")
async def get_racelist(venue_id: str):
    key = f"racelist_{venue_id}_{today()}"
    if c := get_cache(key, CACHE_TTL_SHORT): return c
    code = VENUE_CODES.get(venue_id)
    if not code: return {"error": "Invalid venue", "races": []}
    html = await fetch(f"{BASE_URL}/owpc/pc/race/raceindex?jcd={code}&hd={today()}")
    if not html: return {"error": "Failed", "races": []}
    races = parse_racelist(html)
    return cached(key, CACHE_TTL_SHORT, {"venue_id": venue_id, "races": races, "fetched_at": datetime.now().isoformat()})

@app.get("/api/racecard/{venue_id}/{race_no}")
async def get_racecard(venue_id: str, race_no: int):
    key = f"racecard_{venue_id}_{race_no}_{today()}"
    if c := get_cache(key, CACHE_TTL_LONG): return c
    code = VENUE_CODES.get(venue_id)
    if not code: return {"error": "Invalid venue", "racers": []}
    html = await fetch(f"{BASE_URL}/owpc/pc/race/racelist?rno={race_no}&jcd={code}&hd={today()}")
    if not html: return {"error": "Failed", "racers": []}
    racers = parse_racecard(html)
    return cached(key, CACHE_TTL_LONG, {"venue_id": venue_id, "race_no": race_no, "racers": racers, "fetched_at": datetime.now().isoformat()})

@app.get("/api/exhibition/{venue_id}/{race_no}")
async def get_exhibition(venue_id: str, race_no: int):
    key = f"ex_{venue_id}_{race_no}_{today()}"
    if c := get_cache(key, CACHE_TTL_SHORT): return c
    code = VENUE_CODES.get(venue_id)
    if not code: return {"error": "Invalid venue", "times": {}}
    html = await fetch(f"{BASE_URL}/owpc/pc/race/beforeinfo?rno={race_no}&jcd={code}&hd={today()}")
    if not html: return {"error": "Failed", "times": {}}
    times = parse_exhibition(html)
    return cached(key, CACHE_TTL_SHORT, {"venue_id": venue_id, "race_no": race_no, "times": times, "fetched_at": datetime.now().isoformat()})

@app.get("/api/odds/{venue_id}/{race_no}")
async def get_odds(venue_id: str, race_no: int):
    key = f"odds_{venue_id}_{race_no}_{today()}"
    if c := get_cache(key, CACHE_TTL_SHORT): return c
    code = VENUE_CODES.get(venue_id)
    if not code: return {"error": "Invalid venue", "odds": {}}
    html = await fetch(f"{BASE_URL}/owpc/pc/race/odds3t?rno={race_no}&jcd={code}&hd={today()}")
    if not html: return {"error": "Failed", "odds": {}}
    odds = parse_odds(html)
    return cached(key, CACHE_TTL_SHORT, {"venue_id": venue_id, "race_no": race_no, "odds": odds, "fetched_at": datetime.now().isoformat()})

@app.get("/api/result/{venue_id}/{race_no}")
async def get_result(venue_id: str, race_no: int):
    key = f"result_{venue_id}_{race_no}_{today()}"
    if c := get_cache(key, CACHE_TTL_SHORT): return c
    code = VENUE_CODES.get(venue_id)
    if not code: return {"error": "Invalid venue", "results": []}
    html = await fetch(f"{BASE_URL}/owpc/pc/race/raceresult?rno={race_no}&jcd={code}&hd={today()}")
    if not html: return {"error": "Failed", "results": []}
    results = parse_result(html)
    return cached(key, CACHE_TTL_SHORT, {"venue_id": venue_id, "race_no": race_no, "results": results, "fetched_at": datetime.now().isoformat()})
