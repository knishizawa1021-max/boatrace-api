from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import httpx
from bs4 import BeautifulSoup
import asyncio
from datetime import datetime, date, timedelta
import re
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler

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
VENUE_NAMES = {v: k for k, v in VENUE_CODES.items()}

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

def get_date(offset=0):
    return (date.today() + timedelta(days=offset)).strftime("%Y%m%d")

def cached(key, data):
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
        tables = soup.select("table.is-w748")
        for table in tables:
            rows = table.select("tbody tr")
            for row in rows:
                cells = row.find_all("td")
                if len(cells) < 3:
                    continue
                course_el = row.select_one("td.is-fs14")
                if not course_el:
                    continue
                m = re.search(r"^[1-6]$", course_el.get_text(strip=True))
                if not m:
                    continue
                course = m.group()
                def safe_val(txt):
                    return txt if txt and txt != "-" and txt != "--" else None
                et = st = tilt = ot = None
                for td in cells:
                    txt = td.get_text(strip=True)
                    if re.match(r"^6\.\d{2}$", txt):
                        et = txt
                    elif re.match(r"^[FL]?\d?\.\d{2}$", txt) and txt != et:
                        try:
                            if float(re.sub(r"[FL]", "0", txt)) <= 0.99:
                                st = txt
                        except:
                            pass
                    elif re.match(r"^[+-]?\d+\.5$|^[+-]?\d+\.0$", txt):
                        try:
                            v = float(txt)
                            if -1.0 <= v <= 3.0:
                                tilt = txt
                        except:
                            pass
                    elif re.match(r"^3\.\d{3}$", txt):
                        ot = txt
                result[course] = {
                    "exhibitionTime": safe_val(et),
                    "startTiming": safe_val(st),
                    "tilt": safe_val(tilt),
                    "lapTime": safe_val(ot),
                }
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
            course_el = row.select_one(".table1_bodyNumber, td:first-child")
            if not course_el:
                continue
            m = re.search(r"\d", course_el.get_text())
            if not m:
                continue
            course = int(m.group())
            name_el = row.select_one(".is-fs18, .table1_bodyName")
            name = name_el.get_text(strip=True) if name_el else "---"
            racers.append({"course": course, "name": name})
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
            rank = cells[0].get_text(strip=True)
            course = cells[1].get_text(strip=True) if len(cells) > 1 else ""
            name = cells[2].get_text(strip=True) if len(cells) > 2 else ""
            if re.match(r"[1-6]", rank) and re.match(r"[1-6]", course):
                results.append({"rank": int(rank), "course": int(course), "name": name})
    except Exception as e:
        logger.error(f"Result parse error: {e}")
    return results

def parse_schedule(html, target_date):
    soup = BeautifulSoup(html, "lxml")
    venues = []
    try:
        links = soup.select("a[href*='raceindex'], a[href*='assen']")
        seen = set()
        for link in links:
            href = link.get("href", "")
            m = re.search(r"jcd=(\d+)&hd=(\d+)", href)
            if not m:
                continue
            jcd = m.group(1)
            hd = m.group(2)
            if hd != target_date:
                continue
            if jcd in seen:
                continue
            seen.add(jcd)
            venue_id = VENUE_NAMES.get(jcd)
            if venue_id:
                venues.append({"venue_id": venue_id, "jcd": jcd, "date": hd})
    except Exception as e:
        logger.error(f"Schedule parse error: {e}")
    return venues

async def fetch_tomorrow_schedule():
    tomorrow = get_date(1)
    logger.info(f"翌日スケジュール取得開始: {tomorrow}")
    html = await fetch(f"{BASE_URL}/owpc/pc/race/monthlyschedule?ym={tomorrow[:6]}")
    if not html:
        logger.error("スケジュール取得失敗")
        return
    venues = parse_schedule(html, tomorrow)
    key = f"schedule_{tomorrow}"
    cached(key, {"date": tomorrow, "venues": venues, "fetched_at": datetime.now().isoformat()})
    logger.info(f"翌日スケジュール取得完了: {len(venues)}場")

scheduler = AsyncIOScheduler(timezone="Asia/Tokyo")

@app.on_event("startup")
async def startup():
    scheduler.add_job(fetch_tomorrow_schedule, "cron", hour=23, minute=0)
    scheduler.start()
    logger.info("スケジューラー起動: 毎日23時に翌日情報を取得")
    await fetch_tomorrow_schedule()

@app.on_event("shutdown")
async def shutdown():
    scheduler.shutdown()

@app.get("/")
async def root():
    return {"status": "ok", "message": "BoatRace API Server"}

@app.get("/api/health")
async def health():
    return {"status": "healthy", "time": datetime.now().isoformat()}

@app.get("/api/schedule/{target_date}")
async def get_schedule(target_date: str):
    key = f"schedule_{target_date}"
    if c := get_cache(key, CACHE_TTL_LONG): return c
    html = await fetch(f"{BASE_URL}/owpc/pc/race/monthlyschedule?ym={target_date[:6]}")
    if not html: return {"error": "Failed", "venues": []}
    venues = parse_schedule(html, target_date)
    return cached(key, {"date": target_date, "venues": venues, "fetched_at": datetime.now().isoformat()})

@app.get("/api/racelist/{venue_id}")
async def get_racelist(venue_id: str):
    key = f"racelist_{venue_id}_{get_date()}"
    if c := get_cache(key, CACHE_TTL_SHORT): return c
    code = VENUE_CODES.get(venue_id)
    if not code: return {"error": "Invalid venue", "races": []}
    html = await fetch(f"{BASE_URL}/owpc/pc/race/raceindex?jcd={code}&hd={get_date()}")
    if not html: return {"error": "Failed", "races": []}
    races = parse_racelist(html)
    return cached(key, {"venue_id": venue_id, "races": races, "fetched_at": datetime.now().isoformat()})

@app.get("/api/racecard/{venue_id}/{race_no}")
async def get_racecard(venue_id: str, race_no: int):
    key = f"racecard_{venue_id}_{race_no}_{get_date()}"
    if c := get_cache(key, CACHE_TTL_LONG): return c
    code = VENUE_CODES.get(venue_id)
    if not code: return {"error": "Invalid venue", "racers": []}
    html = await fetch(f"{BASE_URL}/owpc/pc/race/racelist?rno={race_no}&jcd={code}&hd={get_date()}")
    if not html: return {"error": "Failed", "racers": []}
    racers = parse_racecard(html)
    return cached(key, {"venue_id": venue_id, "race_no": race_no, "racers": racers, "fetched_at": datetime.now().isoformat()})

@app.get("/api/exhibition/{venue_id}/{race_no}")
async def get_exhibition(venue_id: str, race_no: int):
    key = f"ex_{venue_id}_{race_no}_{get_date()}"
    if c := get_cache(key, CACHE_TTL_SHORT): return c
    code = VENUE_CODES.get(venue_id)
    if not code: return {"error": "Invalid venue", "times": {}}
    html = await fetch(f"{BASE_URL}/owpc/pc/race/beforeinfo?rno={race_no}&jcd={code}&hd={get_date()}")
    if not html: return {"error": "Failed", "times": {}}
    times = parse_exhibition(html)
    return cached(key, {"venue_id": venue_id, "race_no": race_no, "times": times, "fetched_at": datetime.now().isoformat()})

@app.get("/api/odds/{venue_id}/{race_no}")
async def get_odds(venue_id: str, race_no: int):
    key = f"odds_{venue_id}_{race_no}_{get_date()}"
    if c := get_cache(key, CACHE_TTL_SHORT): return c
    code = VENUE_CODES.get(venue_id)
    if not code: return {"error": "Invalid venue", "odds": {}}
    html = await fetch(f"{BASE_URL}/owpc/pc/race/odds3t?rno={race_no}&jcd={code}&hd={get_date()}")
    if not html: return {"error": "Failed", "odds": {}}
    odds = parse_odds(html)
    return cached(key, {"venue_id": venue_id, "race_no": race_no, "odds": odds, "fetched_at": datetime.now().isoformat()})

@app.get("/api/result/{venue_id}/{race_no}")
async def get_result(venue_id: str, race_no: int):
    key = f"result_{venue_id}_{race_no}_{get_date()}"
    if c := get_cache(key, CACHE_TTL_SHORT): return c
    code = VENUE_CODES.get(venue_id)
    if not code: return {"error": "Invalid venue", "results": []}
    html = await fetch(f"{BASE_URL}/owpc/pc/race/raceresult?rno={race_no}&jcd={code}&hd={get_date()}")
    if not html: return {"error": "Failed", "results": []}
    results = parse_result(html)
    return cached(key, {"venue_id": venue_id, "race_no": race_no, "results": results, "fetched_at": datetime.now().isoformat()})



def parse_kimari(html):
    soup = BeautifulSoup(html, "lxml")
    result = {}
    try:
        def safe_float(t):
            t = t.replace("%","").strip()
            try: return float(t)
            except: return None

        tables = soup.select("table")
        nyuuritsu = {}
        sanrenritsu = {}
        avg_st = {}

        for table in tables:
            header = table.find("th") or table.find("td")
            if not header:
                continue
            header_text = header.get_text(strip=True)
            rows = table.find_all("tr")[1:]
            for row in rows:
                cells = row.find_all(["td","th"])
                if len(cells) < 2:
                    continue
                course = cells[0].get_text(strip=True)
                val = cells[1].get_text(strip=True)
                if not re.match(r"^[1-6]$", course):
                    continue
                if "進入率" in header_text:
                    nyuuritsu[course] = safe_float(val)
                elif "3連対率" in header_text:
                    sanrenritsu[course] = safe_float(val)
                elif "スタートタイミング" in header_text:
                    avg_st[course] = safe_float(val)

        for course in "123456":
            result[course] = {
                "nyuuritsu": nyuuritsu.get(course),
                "sanrenritsu": sanrenritsu.get(course),
                "avg_st": avg_st.get(course),
            }
    except Exception as e:
        logger.error(f"Kimari parse error: {e}")
    return result

@app.get("/api/racer/{racer_id}/kimari")
async def get_racer_kimari(racer_id: str):
    key = f"kimari_{racer_id}"
    if c := get_cache(key, 86400):
        return c
    url = f"{BASE_URL}/owpc/pc/data/racersearch/course?toban={racer_id}"
    html = await fetch(url)
    if not html:
        return {"error": "Failed", "kimari": {}}
    kimari = parse_kimari(html)
    return cached(key, {"racer_id": racer_id, "kimari": kimari, "fetched_at": datetime.now().isoformat()})


@app.get("/api/debug/racer/{racer_id}")
async def debug_racer(racer_id: str):
    url = f"{BASE_URL}/owpc/pc/data/racersearch/course?toban={racer_id}"
    html = await fetch(url)
    if not html:
        return {"error": "Failed to fetch"}
    soup = BeautifulSoup(html, "lxml")
    tables = soup.select("table")
    result = []
    for i, table in enumerate(tables):
        rows = table.find_all("tr")
        table_data = []
        for row in rows:
            cells = [c.get_text(strip=True) for c in row.find_all(["td","th"])]
            if cells:
                table_data.append(cells)
        result.append({"table": i, "rows": table_data[:5]})
    return {"tables": result}



@app.get("/api/debug/exhibition/{venue_id}/{race_no}")
async def debug_exhibition(venue_id: str, race_no: int):
    code = VENUE_CODES.get(venue_id)
    if not code:
        return {"error": "Invalid venue"}
    url = f"{BASE_URL}/owpc/pc/race/beforeinfo?rno={race_no}&jcd={code}&hd={get_date()}"
    html = await fetch(url)
    if not html:
        return {"error": "Failed to fetch"}
    soup = BeautifulSoup(html, "lxml")
    tables = soup.select("table.is-w748")
    result = []
    for i, table in enumerate(tables):
        rows = table.find_all("tr")
        table_data = []
        for row in rows:
            cells = [c.get_text(strip=True) for c in row.find_all(["td","th"])]
            if cells:
                table_data.append(cells)
        result.append({"table_index": i, "rows": table_data[:8]})
    return {"url": url, "tables_found": len(tables), "data": result}
