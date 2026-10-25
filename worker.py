import os
import re
import json
import math
import socket
import ssl
import urllib.request
import urllib.parse
from datetime import datetime
from email.utils import parsedate_to_datetime
import feedparser
from google import genai
from google.genai import types
from supabase import create_client

socket.setdefaulttimeout(10)

SUPABASE_URL = os.environ.get("SUPABASE_URL") or "https://cmjlebtqwyyplwdcklzn.supabase.co"
SUPABASE_KEY = (
    os.environ.get("SUPABASE_KEY") 
    or os.environ.get("SUPABASE_SERVICE_KEY") 
    or os.environ.get("SUPABASE_ANON_KEY") 
    or "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImNtamxlYnRxd3l5cGx3ZGNrbHpuIiwicm9sZSI6ImFub24iLCJpYXQiOjE3OTIxNTk5OTksImV4cCI6MjEwNzczNTk5OX0.YczVUJnRBcjMBdaKzgQij7MENxN6vDWfe8Btt2sP5-c"
)
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

NTFY_TOPIC_ALL = "straz-alert-polska-998"
NTFY_TOPIC_LOCAL = "straz-alert-lodzkie-998"
APP_URL = "https://debiuuxd.github.io/straz-alert/"

PSP_LODZ_FEED = "https://interwencje.straz.lodz.pl/feed"

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
ai_client = genai.Client(api_key=GEMINI_API_KEY)

FAST_MODELS = ["gemini-flash-lite-latest", "gemini-2.5-flash-lite", "gemini-2.5-flash", "gemini-2.0-flash"]

RSS_FEEDS = [
    # 1. Google News zmuszony do indeksowania samej strony interwencji PSP Łódź:
    "https://news.google.com/rss/search?q=site:interwencje.straz.lodz.pl+OR+site:straz.lodz.pl&hl=pl&gl=PL&ceid=PL:pl",
    # 2. Lokalne portale i trasy wokół Strykowa i Głowna:
    "https://news.google.com/rss/search?q=(wypadek+OR+po%C5%BCar+OR+kolizja+OR+stra%C5%BC+OR+zderzenie)+(Stryk%C3%B3w+OR+G%C5%82owno+OR+Bratoszewice+OR+Dmosin+OR+Niesu%C5%82k%C3%B3w)+when:1d&hl=pl&gl=PL&ceid=PL:pl",
    "https://news.google.com/rss/search?q=(wypadek+OR+po%C5%BCar+OR+zablokowana+OR+dachowanie+OR+zderzenie)+(A1+OR+A2+OR+DK14+OR+DK92)+(Stryk%C3%B3w+OR+G%C5%82owno+OR+%C5%81owicz+OR+Sosnowiec)+when:1d&hl=pl&gl=PL&ceid=PL:pl",
    "https://remiza.com.pl/feed/",
    "https://news.google.com/rss/search?q=(stra%C5%BC+po%C5%BCarna+OR+JRG+OR+OSP)+(po%C5%BCar+OR+wypadek+OR+zderzenie+OR+interwencja)+when:1d&hl=pl&gl=PL&ceid=PL:pl"
]

IGNORE_TITLE_WORDS = [
    "zbiórka", "nieśmiertelniki", "ślubowanie", "awans", "odznaczenia", 
    "przetarg", "wspomnienie", "rocznica", "pogrzeb", "żegnają", 
    "oddali hołd", "nie żyje komendant", "był zastępcą", "lotto", 
    "losowania", "szczęśliwe liczby", "skrót dnia", "trening", 
    "szkolenie", "ćwiczenia", "zawody", "turniej", "humbak", "wieloryb", 
    "sekcje zwłok", "posadzili", "centrum handlowe", "otworzą", "koncert", "atrakcje",
    "kanada", "kanady", "albercie", "usa", "niemczech", "ukrain",
    "rozbudowa", "przebudowa", "remont", "objazdy", "odcinkowe", 
    "zmieni przejazd", "potrwają do", "nowej remizie", "nowa remiza",
    "sebastian m", "sebastiana m", "ekstradycj", "akt oskarżenia", "aktem oskarżenia",
    "ławie oskarżonych", "przed sądem", "ruszył proces", "proces w sprawie",
    "wznowiono proces", "opinia biegłych", "biegli o wypadku", "prawomocny wyrok",
    "zapadł wyrok", "skazany za wypadek", "zjednoczonych emiratach", "dubaju"
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

STRICT_LOCAL_TOWNS = {
    "stryków", "strykow", "strykowie", "strykowa",
    "niesułków", "niesulkow", "nowostawy", "swędów", "swedow", "bratoszewice",
    "dobra", "dobrej", "sosnowiec", "kiełmina", "kielmina", "kalinów", "kalinow",
    "tymianka", "smolice", "warszewice", "koźle", "kozle", "pludwiny", "sadówka", "sadowka",
    "zelgoszcz", "wyskoki", "anielin", "bartolin", "cesarka", "ciołek", "ciolek",
    "krucice", "lipka", "ługi", "lugi", "michałówek", "michalowek", "orzechówek", "orzechowek",
    "rokitnica", "sierżnia", "sierznia", "wola błędowa", "zagłoba", "zagloba", "zarębów", "zarebow",
    "głowno", "glowno", "głownie", "glownie", "głowna", "glowna",
    "ostrołęka", "lubianków", "lubiankow", "mąkolice", "makolice", "wola zbrożkowa",
    "popów", "popow", "kadzielin", "bronisławów", "bronislawow", "albinów", "albinow",
    "boczki", "chlebowice", "domaradzyn", "glinnik", "helenów", "helenow", "konarzew",
    "rudniczek", "ziewanice", "wola łokotowa", "antoniew", "gawronki", "jasionna", "karasica",
    "dmosin", "dmosinie", "nadolna", "szczecin", "kołacin", "kolacin", "kołacinek", "kolacinek",
    "lubowidza", "wola cyrusowa", "kuźmy", "kuzmy", "grodzisk", "kraszew", "borkowizna"
}

def log(msg):
    print(msg, flush=True)

def is_local_incident(title, location, region, lat, lng):
    loc_clean = location.lower().strip()
    full_text = f"{title} {location}".lower()

    if loc_clean in ["łódź", "lodz", "zgierz", "łowicz", "lowicz", "pabianice", "kutno", "skierniewice", "brzeziny"]:
        return False

    if loc_clean in STRICT_LOCAL_TOWNS:
        return True

    for town in STRICT_LOCAL_TOWNS:
        if len(town) >= 5 and re.search(r'\b' + re.escape(town) + r'\b', full_text):
            return True

    if any(road in full_text for road in ["dk14", "dk 14", "k14", "a2", "a1", "węzeł łódź północ", "łódź północ"]):
        if any(w in full_text for w in ["stryków", "strykowie", "strykowa", "głowno", "głownie", "głowna", "dmosin", "sosnowiec", "łowicz"]):
            return True

    try:
        f_lat = float(lat)
        f_lng = float(lng)
        if f_lat >= 51.85 and f_lng >= 19.53:
            dist_strykow = math.hypot(f_lat - 51.901, f_lng - 19.602)
            dist_glowno = math.hypot(f_lat - 51.964, f_lng - 19.712)
            if dist_strykow < 0.09 or dist_glowno < 0.09:
                return True
    except Exception:
        pass

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

def geocode_place(query_str, geo_cache):
    if query_str in geo_cache:
        return geo_cache[query_str]

    lat, lng = 51.901, 19.602
    geo_url = f"https://nominatim.openstreetmap.org/search?q={urllib.parse.quote(query_str)}&format=json&limit=1"
    geo_req = urllib.request.Request(geo_url, headers={'User-Agent': 'StrazAlertApp/1.0'})
    try:
        with urllib.request.urlopen(geo_req, timeout=5) as geo_res:
            geo_data = json.loads(geo_res.read().decode())
            if geo_data and len(geo_data) > 0:
                lat = float(geo_data[0]['lat'])
                lng = float(geo_data[0]['lon'])
                geo_cache[query_str] = (lat, lng)
    except Exception:
        pass
    return lat, lng

def fetch_psp_lodz_entries():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    pl_headers = {
        'User-Agent': 'Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)',
        'Accept': '*/*',
        'Accept-Language': 'pl-PL,pl;q=0.9',
        'X-Forwarded-For': '83.8.0.1'
    }

    encoded_url = urllib.parse.quote(PSP_LODZ_FEED, safe='')
    
    endpoints = [
        ("Google Translate Proxy", "https://interwencje-straz-lodz-pl.translate.goog/feed?_x_tr_sl=pl&_x_tr_tl=pl&_x_tr_hl=pl", 6),
        ("Corsfix Proxy", f"https://proxy.corsfix.com/?{PSP_LODZ_FEED}", 6),
        ("ThingProxy", f"https://thingproxy.freeboard.io/fetch/{PSP_LODZ_FEED}", 6),
        ("Jina Reader", f"https://r.jina.ai/{PSP_LODZ_FEED}", 8),
    ]

    for label, url, tout in endpoints:
        try:
            req = urllib.request.Request(url, headers=pl_headers)
            with urllib.request.urlopen(req, timeout=tout, context=ctx) as resp:
                raw_bytes = resp.read()
            parsed = feedparser.parse(raw_bytes)
            if parsed.entries and len(parsed.entries) > 0:
                log(f"   ✓ Pobrano wykaz PSP Łódź przez: {label} ({len(parsed.entries)} wpisów)")
                return list(parsed.entries)
        except Exception as e:
            log(f"   ↪ Próba ({label}) nieudana: {str(e)[:45]}...")
            continue

    return []

def process_psp_lodz_feed(existing_ids, recent_incidents, today_str, now_pl, headers, geo_cache):
    log(f"\n🚒 [PSP ŁÓDŹ] Pobieram oficjalny wykaz interwencji: {PSP_LODZ_FEED}...")
    entries = fetch_psp_lodz_entries()
    if not entries:
        log("⚠️ Serwer PSP Łódź odrzucił wszystkie bramki z USA (silny Geo-blocking na Polskę).")
        return

    added_psp = 0

    for entry in reversed(entries[:30]):
        external_id = entry.get('id') or entry.get('guid') or entry.get('link')
        raw_title = (entry.get('title') or '').strip()
        raw_desc = (entry.get('summary') or entry.get('description') or '').strip()
        link = entry.get('link') or PSP_LODZ_FEED

        if not external_id or not raw_title or external_id in existing_ids:
            continue

        if " - " in raw_title:
            main_type, town = raw_title.split(" - ", 1)
            main_type = main_type.strip()
            town = town.strip()
        else:
            main_type = raw_title
            town = "woj. łódzkie"

        sub_type = re.sub(r'<[^>]+>', '', raw_desc).strip()
        if sub_type.startswith("MZ - ") or sub_type.startswith("P - ") or sub_type.startswith("AF - "):
            sub_type = sub_type.split(" - ", 1)[1].strip()
        if sub_type.lower() in ["brak", "", "inne mz", "inne pożary"]:
            sub_type_clean = ""
        else:
            sub_type_clean = sub_type

        mt_lower = main_type.lower()
        st_lower = sub_type.lower()

        if "pożar" in mt_lower:
            category = "fire"
            nice_title = f"Pożar ({sub_type_clean}) – {town}" if sub_type_clean else f"Pożar – {town}"
            nice_desc = f"Zdarzenie pożarowe w m. {town}. Kwalifikacja PSP: {raw_desc if raw_desc != 'Brak' else 'Pożar'}."
        elif "alarm fałszywy" in mt_lower:
            category = "hazard"
            nice_title = f"⚠️ Alarm fałszywy – {town}"
            nice_desc = f"🚨 ALARM FAŁSZYWY w m. {town} (zgłoszenie niepotwierdzone po dojeździe zastępów lub z instalacji wykrywania)."
        else:
            if any(k in st_lower for k in ["kolizja", "wypadek", "drogow", "komunikac", "pojazd"]):
                category = "accident"
                nice_title = f"{sub_type_clean} – {town}" if sub_type_clean else f"Zdarzenie drogowe – {town}"
                nice_desc = f"Zdarzenie drogowe w m. {town}. Kwalifikacja PSP: {raw_desc}."
            else:
                category = "hazard"
                nice_title = f"Miejscowe zagrożenie ({sub_type_clean}) – {town}" if sub_type_clean else f"Miejscowe zagrożenie – {town}"
                nice_desc = f"Interwencja straży pożarnej w m. {town}. Rodzaj zagrożenia: {raw_desc if raw_desc != 'Brak' else 'Miejscowe zagrożenie'}."

        inc_date = today_str
        inc_time = now_pl.strftime("%H:%M")
        pub_str = entry.get('published') or entry.get('updated')
        if pub_str:
            try:
                dt = parsedate_to_datetime(pub_str).astimezone()
                inc_date = dt.strftime("%Y-%m-%d")
                inc_time = dt.strftime("%H:%M")
            except Exception:
                pass

        geo_query = f"{town}, województwo łódzkie, Polska"
        lat, lng = geocode_place(geo_query, geo_cache)

        offset_lat = ((hash(external_id) % 70) - 35) * 0.00035
        offset_lng = (((hash(external_id + "x") % 70) - 35)) * 0.00035
        final_lat = lat + offset_lat
        final_lng = lng + offset_lng

        insert_data = {
            "external_id": external_id,
            "title": nice_title[:120],
            "category": category,
            "status": "Zakończone" if "alarm fałszywy" in mt_lower else "Trwa akcja",
            "location": town,
            "region": "woj. łódzkie (SWD PSP)",
            "lat": final_lat,
            "lng": final_lng,
            "date_str": inc_date,
            "time_str": inc_time,
            "units": "Zastępy PSP / OSP (SWD-ST Łódź)",
            "description": nice_desc,
            "source_url": link
        }

        try:
            supabase.table('incidents').insert(insert_data).execute()
            existing_ids.add(external_id)
            recent_incidents.insert(0, insert_data)
            added_psp += 1
            log(f"✅ [PSP ŁÓDŹ] Dodano na mapę: {nice_title}")

            if "alarm fałszywy" not in mt_lower:
                send_ntfy_push(NTFY_TOPIC_ALL, insert_data, is_local=False)

            if is_local_incident(nice_title, town, "łódzkie", final_lat, final_lng):
                send_ntfy_push(NTFY_TOPIC_LOCAL, insert_data, is_local=True)

        except Exception as e:
            log(f"❌ Błąd zapisu wpisu PSP: {e}")

    log(f"📊 [PSP ŁÓDŹ] Nowe interwencje dodane w tym cyklu: {added_psp}")

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
    log("🚀 Start skanowania (PSP Łódź bez AI + Polska przez AI + ntfy.sh)...")
    now_pl = datetime.now()
    today_str = now_pl.strftime("%Y-%m-%d")

    existing_res = supabase.table('incidents').select('id, external_id, title, description, location, category, lat, lng, date_str').order('id', desc=True).limit(400).execute()
    all_rows = existing_res.data or []
    existing_ids = set(r['external_id'] for r in all_rows if r.get('external_id'))
    recent_incidents = all_rows[:80]

    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0'}
    geo_cache = {}

    process_psp_lodz_feed(existing_ids, recent_incidents, today_str, now_pl, headers, geo_cache)

    ai_calls_this_run = 0
    MAX_AI_CALLS_PER_RUN = 8
    seen_in_this_run = []

    for feed_url in RSS_FEEDS:
        if ai_calls_this_run >= MAX_AI_CALLS_PER_RUN:
            log("⏹️ Osiągnięto limit 8 analiz AI dla mediów w tym cyklu.")
            break

        log(f"\n📡 Skanuję kanał prasowy: {feed_url[:65]}...")
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
            full_check_text = f"{clean_title} {summary}".lower()

            if any(bad in full_check_text for bad in IGNORE_TITLE_WORDS):
                log(f"🚫 Odrzucono (filtr sądowy/remontowy): {clean_title[:55]}...")
                existing_ids.add(external_id)
                continue

            if not any(w in clean_title.lower() for w in TITLE_ACTION_WORDS):
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

            Oceń, czy to DZISIEJSZE, nagłe zdarzenie ratownicze, drogowe lub pożarowe w Polsce (pożar, wypadek, kolizja, zderzenie, potrącenie, wyciek gazu, interwencja straży/służb).
            BARDZO WAŻNE ZASADY ODRZUCANIA ("is_incident": false):
            1. Jeśli artykuł dotyczy rozprawy sądowej, procesu, aktu oskarżenia, ekstradycji, opinii biegłych, wyroku sądu lub wspomnień wypadku sprzed miesięcy/lat (np. sprawa Sebastiana M. na A1) -> MUSISZ ustawić "is_incident": false!
            2. (Uwaga: Jeśli wypadek wydarzył się DZISIAJ, np. pijany kierowca wjechał do rowu, a na końcu artykułu wspomniano tylko, że grozi mu sąd/więzienie -> to JEST świeży wypadek, ustaw "is_incident": true).
            3. Jeśli to planowany remont drogi, rozbudowa autostrady, objazdy, ćwiczenia lub zdarzenie za granicą -> MUSISZ ustawić "is_incident": false!

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
                    log("   -> AI odrzuciło (stara sprawa sądowa / brak akcji ratowniczej).")
                    continue

                lat = float(data.get("estimated_lat") or 51.90)
                lng = float(data.get("estimated_lng") or 19.50)
                geo_q = data.get("geocode_query", "")

                if geo_q:
                    g_lat, g_lng = geocode_place(f"{geo_q}, Polska", geo_cache)
                    if (g_lat, g_lng) != (51.901, 19.602):
                        lat, lng = g_lat, g_lng

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
