import os
import re
import json
import math
import socket
import urllib.request
import urllib.parse
from datetime import datetime
import feedparser
from google import genai
from google.genai import types
from supabase import create_client

# Twardy limit czasu na każde połączenie sieciowe (10 sekund), żeby skrypt nigdy nie zawisł
socket.setdefaulttimeout(10)

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_ANON_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

NTFY_TOPIC_ALL = "straz-alert-polska-998"
NTFY_TOPIC_LOCAL = "straz-alert-lodzkie-998"
APP_URL = "https://debiuuxd.github.io/straz-alert/"

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
ai_client = genai.Client(api_key=GEMINI_API_KEY)

FAST_MODELS = ["gemini-flash-lite-latest", "gemini-2.5-flash-lite", "gemini-2.5-flash", "gemini-2.0-flash"]

RSS_FEEDS = [
    "https://news.google.com/rss/search?q=(site:lowiczanin.info+OR+site:tuzgierz.pl+OR+site:tulodz.pl+OR+site:expressilustrowany.pl+OR+site:dzienniklodzki.pl)+(stra%C5%BC+OR+po%C5%BCar+OR+wypadek+OR+kolizja+OR+zderzenie)+when:1d&hl=pl&gl=PL&ceid=PL:pl",
    "https://news.google.com/rss/search?q=(wypadek+OR+po%C5%BCar+OR+kolizja+OR+stra%C5%BC+OR+zderzenie)+(Stryk%C3%B3w+OR+G%C5%82owno+OR+Zgierz+OR+%C5%81%C3%B3d%C5%BA+OR+zgierski+OR+%C5%81owicz+OR+Kutno+OR+Brzeziny)+when:1d&hl=pl&gl=PL&ceid=PL:pl",
    "https://news.google.com/rss/search?q=(wypadek+OR+po%C5%BCar+OR+zablokowana+OR+dachowanie+OR+zderzenie)+(A1+OR+A2+OR+S8+OR+S14+OR+DK14+OR+DK92+OR+%C5%82%C3%B3dzkie)+when:1d&hl=pl&gl=PL&ceid=PL:pl",
    "https://remiza.com.pl/feed/",
    "https://news.google.com/rss/search?q=(stra%C5%BC+po%C5%BCarna+OR+JRG+OR+OSP)+(po%C5%BCar+OR+wypadek+OR+zderzenie+OR+interwencja)+when:1d&hl=pl&gl=PL&ceid=PL:pl"
]

IGNORE_TITLE_WORDS = [
    "zbiórka", "nieśmiertelniki", "ślubowanie", "awans", "odznaczenia", 
    "przetarg", "wspomnienie", "rocznica", "pogrzeb", "żegnają", 
    "oddali hołd", "nie żyje komendant", "był zastępcą", "lotto", 
    "losowania", "szczęśliwe liczby", "skrót dnia", "trening", 
    "szkolenie", "ćwiczenia", "zawody", "turniej", "humbak", "wieloryb", 
    "sekcje zwłok", "prokuratura", "areszt", "zarzuty", "wyrok",
    "posadzili", "centrum handlowe", "otworzą", "koncert", "atrakcje",
    "kanada", "kanady", "albercie", "usa", "niemczech", "ukrain",
    "rozbudowa", "przebudowa", "remont", "objazdy", "odcinkowe", 
    "zmieni przejazd", "potrwają do", "nowej remizie", "nowa remiza"
]

TITLE_ACTION_WORDS = [
    "pożar", "płonie", "płoną", "spłonął", "spłonęła", "ogień", "zadymienie", "czad", "wyciek",
    "wypadek", "wypadku", "kolizja", "kolizji", "zderzenie", "zderzyły", "dachowanie", "dachował",
    "potrącenie", "potrącony", "potrącona", "zablokowana", "straż", "strażacy",
    "interwencja", "interweniowała", "uderzył", "wbił się", "wjechał", "powalone drzewo", "ewakuacja",
    "przewróciła", "karambol", "ranny", "ranna", "ranni", "poszkodowany", "poszkodowana", "śmiertelny",
    "runął", "wybuch", "eksplozja", "szpitalu", "szpitala"
]

STOP_WORDS = {
    "pożar", "wypadek", "kolizja", "zderzenie", "straż", "straży", "strażacy", "akcja", 
    "ulicy", "droga", "powiat", "okolice", "łodzi", "łódź", "łowiczu", "łowicz", "policja",
    "kierowca", "szpitala", "szpitalu", "centrum", "nocny", "jedna", "osoba", "trafiła"
}

LOCAL_KEYWORDS = [
    "stryków", "strykow", "zgierz", "zgierski", "głowno", "glowno", 
    "łowicz", "lowicz", "kutno", "brzeziny", "łódź", "lodz", "łódzkie", 
    "lodzkie", "a1", "a2", "dk14", "dk92", "skierniewic", "pabianic", "łęczyc"
]

def log(msg):
    print(msg, flush=True)

def is_local_incident(title, location, region, lat, lng):
    text = f"{title} {location} {region}".lower()
    if any(k in text for k in LOCAL_KEYWORDS):
        return True
    try:
        dist = math.hypot(float(lat) - 51.90, float(lng) - 19.60)
        return dist < 0.65
    except Exception:
        return False

def send_ntfy_push(topic, inc_data, is_local=False):
    cat_emoji = "🔥" if inc_data["category"] == "fire" else ("🚗" if inc_data["category"] == "accident" else "⚠️")
    prefix = "🚨 ALARM LOKALNY: " if is_local else f"{cat_emoji} "
    body = (
        f"📍 Miejsce: {inc_data['location']} ({inc_data['region']})\n"
        f"🚒 SiŚ: {inc_data['units']}\n"
        f"📝 {inc_data['description']}"
    )
    payload = {
        "topic": topic,
        "title": f"{prefix}{inc_data['title']}"[:110],
        "message": body,
        "priority": 4 if is_local else 3,
        "tags": ["rotating_light", "fire_engine"],
        "click": APP_URL
    }
    try:
        req = urllib.request.Request(
            "https://ntfy.sh/",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=6) as _:
            log(f"📲 Wysłano powiadomienie Push na kanał: {topic}")
    except Exception as e:
        log(f"⚠️ Błąd wysyłki Push ({topic}): {e}")

def extract_keywords(text):
    clean = re.sub(r'[^a-ząćęłńóśźż0-9\s]', ' ', text.lower())
    return set(w for w in clean.split() if len(w) > 3 and w not in STOP_WORDS)

def is_pre_duplicate(raw_title, recent_incidents, seen_in_this_run):
    kw1 = extract_keywords(raw_title)
    if len(kw1) < 2:
        return False
    for seen_kw in seen_in_this_run:
        if len(kw1 & seen_kw) >= 2:
            return True
    for inc in recent_incidents:
        inc_text = f"{inc.get('title', '')} {inc.get('description', '')}"
        kw2 = extract_keywords(inc_text)
        if len(kw1 & kw2) >= 2:
            return True
    return False

def is_duplicate_recent(new_title, new_loc, new_cat, new_lat, new_lng, recent_incidents):
    t1 = new_title.lower().strip()
    kw1 = extract_keywords(new_title)
    for inc in recent_incidents:
        t2 = (inc.get("title") or "").lower().strip()
        cat2 = inc.get("category")
        if t1 == t2:
            return True
        kw2 = extract_keywords(t2)
        if len(kw1 & kw2) >= 2 and new_cat == cat2:
            return True
        if new_cat == cat2 and inc.get("lat") and inc.get("lng"):
            dist = math.hypot(new_lat - float(inc["lat"]), new_lng - float(inc["lng"]))
            if dist < 0.015:
                return True
    return False

def run_worker():
    log("🚀 Start szybkiego skanowania (Logi na żywo + Push ntfy.sh)...")
    now_pl = datetime.now()
    today_str = now_pl.strftime("%Y-%m-%d")

    existing_res = supabase.table('incidents').select('id, external_id, title, description, location, category, lat, lng, date_str').order('id', desc=True).limit(350).execute()
    all_rows = existing_res.data or []
    existing_ids = set(r['external_id'] for r in all_rows if r.get('external_id'))
    recent_incidents = all_rows[:60]

    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0'}
    ai_calls_this_run = 0
    MAX_AI_CALLS_PER_RUN = 8
    seen_in_this_run = []

    for feed_url in RSS_FEEDS:
        if ai_calls_this_run >= MAX_AI_CALLS_PER_RUN:
            log("⏹️ Osiągnięto limit 8 analiz AI w tym cyklu.")
            break

        log(f"\n📡 Skanuję kanał: {feed_url[:65]}...")
        try:
            req = urllib.request.Request(feed_url, headers=headers)
            with urllib.request.urlopen(req, timeout=8) as resp:
                xml_data = resp.read()
            parsed_feed = feedparser.parse(xml_data)
        except Exception as e:
            log(f"⚠️ Błąd pobierania RSS: {e}")
            continue

        entries = list(parsed_feed.entries)
        entries.sort(key=lambda x: x.get('published_parsed') or (0,), reverse=True)

        for entry in entries[:15]:
            if ai_calls_this_run >= MAX_AI_CALLS_PER_RUN:
                break

            external_id = entry.get('id') or entry.get('link')
            title = entry.get('title', '')
            summary = entry.get('summary', '') or entry.get('description', '')
            link = entry.get('link', '')

            if not external_id or not title or external_id in existing_ids:
                continue

            clean_title = title.rsplit(" - ", 1)[0] if " - " in title else title
            title_lower = clean_title.lower()

            if any(bad in title_lower for bad in IGNORE_TITLE_WORDS):
                existing_ids.add(external_id)
                continue

            if not any(w in title_lower for w in TITLE_ACTION_WORDS):
                existing_ids.add(external_id)
                continue

            if is_pre_duplicate(clean_title, recent_incidents, seen_in_this_run):
                log(f"⏭️ Pominięto przed AI (już w bazie): {clean_title[:55]}...")
                existing_ids.add(external_id)
                continue

            log(f"🔍 Analizuję przez AI: {clean_title[:65]}...")

            prompt = f"""
            Przeanalizuj poniższy artykuł prasowy o zdarzeniu w Polsce.
            Tytuł: {clean_title}
            Opis: {summary}

            Oceń, czy to nagłe zdarzenie ratownicze, drogowe lub pożarowe w Polsce (pożar, wypadek, kolizja, zderzenie, potrącenie, wyciek gazu, interwencja straży/służb).
            UWAGA: Jeśli to planowany remont drogi, rozbudowa autostrady, przebudowa ronda, objazdy, otwarcie remizy lub zdarzenie za granicą -> MUSISZ ustawić "is_incident": false!

            Zwróć WYŁĄCZNIE obiekt JSON:
            - "is_incident": boolean
            - "title": string (Krótki tytuł meldunku po polsku).
            - "category": string (jedno z: "fire", "accident", "hazard").
            - "status": string (jedno z: "Trwa akcja", "Opanowane", "Zakończone").
            - "location": string (Miejscowość i ulica/droga).
            - "geocode_query": string (Miejscowość i województwo do mapy).
            - "region": string (Województwo lub powiat).
            - "units": string (Siły i Środki, np. "PSP, OSP, ZRM, Policja").
            - "description": string (Krótki opis 1-2 zdania po polsku).
            - "estimated_lat": number (Szerokość GPS w Polsce, np. 51.77).
            - "estimated_lng": number (Długość GPS w Polsce, np. 19.47).
            """

            response = None
            for model_name in FAST_MODELS:
                try:
                    response = ai_client.models.generate_content(
                        model=model_name,
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            response_mime_type="application/json",
                            temperature=0.1
                        )
                    )
                    ai_calls_this_run += 1
                    log(f"   ✓ Odpowiedział model: {model_name}")
                    break
                except Exception as err:
                    log(f"   ⚠️ Model {model_name} niedostępny ({str(err)[:35]}), próbuję kolejny...")
                    continue

            if not response:
                log("❌ Chwilowy brak wolnego limitu AI – kończę ten cykl.")
                return

            try:
                result_text = response.text.strip()
                if result_text.startswith("```"):
                    result_text = result_text.split("```")[1]
                    if result_text.startswith("json"):
                        result_text = result_text[4:]
                
                data = json.loads(result_text.strip())
                existing_ids.add(external_id)
                seen_in_this_run.append(extract_keywords(clean_title))

                if not data.get("is_incident", False):
                    log("   -> AI odrzuciło (brak akcji ratowniczej).")
                    continue

                lat = float(data.get("estimated_lat") or 51.90)
                lng = float(data.get("estimated_lng") or 19.50)
                geo_q = data.get("geocode_query", "")

                if geo_q:
                    geo_url = f"https://nominatim.openstreetmap.org/search?q={urllib.parse.quote(geo_q + ', Polska')}&format=json&limit=1"
                    geo_req = urllib.request.Request(geo_url, headers={'User-Agent': 'StrazAlertApp/1.0'})
                    try:
                        with urllib.request.urlopen(geo_req, timeout=5) as geo_res:
                            geo_data = json.loads(geo_res.read().decode())
                            if geo_data and len(geo_data) > 0:
                                lat = float(geo_data[0]['lat'])
                                lng = float(geo_data[0]['lon'])
                    except Exception:
                        pass

                inc_title = data.get("title", clean_title)[:120]
                inc_loc = data.get("location", "Polska")
                inc_cat = data.get("category", "hazard")
                inc_reg = data.get("region", "Polska")

                if is_duplicate_recent(inc_title, inc_loc, inc_cat, lat, lng, recent_incidents):
                    log(f"🔄 Anty-Duplikator zablokował powtórkę: {inc_title} ({inc_loc})")
                    continue

                offset = ((hash(external_id) % 60) - 30) * 0.0003
                final_lat = lat + offset
                final_lng = lng + offset

                insert_data = {
                    "external_id": external_id,
                    "title": inc_title,
                    "category": inc_cat,
                    "status": data.get("status", "Trwa akcja"),
                    "location": inc_loc,
                    "region": inc_reg,
                    "lat": final_lat,
                    "lng": final_lng,
                    "date_str": today_str,
                    "time_str": now_pl.strftime("%H:%M"),
                    "units": data.get("units", "PSP, OSP, Policja"),
                    "description": data.get("description", clean_title),
                    "source_url": link
                }

                supabase.table('incidents').insert(insert_data).execute()
                recent_incidents.insert(0, insert_data)
                log(f"✅ SUKCES! Dodano na mapę: {inc_title} ({inc_loc})")

                send_ntfy_push(NTFY_TOPIC_ALL, insert_data, is_local=False)
                if is_local_incident(inc_title, inc_loc, inc_reg, final_lat, final_lng):
                    send_ntfy_push(NTFY_TOPIC_LOCAL, insert_data, is_local=True)

            except Exception as e:
                log(f"❌ Błąd zapisu: {e}")

    log("🏁 Skanowanie zakończone pomyślnie!")

if __name__ == "__main__":
    run_worker()
