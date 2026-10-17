import os
import re
import json
import math
import ssl
import urllib.request
import urllib.parse
from datetime import datetime
import feedparser
from google import genai
from google.genai import types
from supabase import create_client

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_ANON_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
ai_client = genai.Client(api_key=GEMINI_API_KEY)

RSS_FEEDS = [
    # 1. Okolice: Stryków, Głowno, Zgierz, Łódź, Brzeziny, Łowicz, Kutno, powiat zgierski
    "https://news.google.com/rss/search?q=(wypadek+OR+po%C5%BCar+OR+kolizja+OR+stra%C5%BC)+(Stryk%C3%B3w+OR+G%C5%82owno+OR+Zgierz+OR+%C5%81%C3%B3d%C5%BA+OR+zgierski+OR+Brzeziny)+when:1d&hl=pl&gl=PL&ceid=PL:pl",
    
    # 2. Autostrady A1 i A2 oraz drogi w woj. łódzkim
    "https://news.google.com/rss/search?q=(wypadek+OR+po%C5%BCar+OR+zablokowana+OR+dachowanie)+(A1+OR+A2+OR+S8+OR+DK14+OR+DK92+OR+%C5%82%C3%B3dzkie)+when:1d&hl=pl&gl=PL&ceid=PL:pl",
    
    # 3. Ogólnopolski portal strażacki
    "https://remiza.com.pl/feed/",
    
    # 4. Ogólnopolskie akcje straży pożarnej z dzisiaj
    "https://news.google.com/rss/search?q=(stra%C5%BC+po%C5%BCarna+OR+JRG+OR+OSP)+(po%C5%BCar+OR+wypadek+OR+zderzenie)+when:1d&hl=pl&gl=PL&ceid=PL:pl"
]

IGNORE_WORDS = [
    "zbiórka", "pieniądze", "miliony", "nieśmiertelniki", "ślubowanie", "awans", 
    "odznaczenia", "przetarg", "zakup", "wspomnienie", "rocznica", "pogrzeb", 
    "żegnają", "oddali hołd", "nie żyje komendant", "był zastępcą", "lotto", 
    "losowania", "szczęśliwe liczby", "skrót dnia", "przegląd", "trening", 
    "szkolenie", "ćwiczenia", "zawody", "turniej", "humbak", "wieloryb", "sekcje zwłok"
]

ACTION_STEMS = [
    "pożar", "płon", "płomien", "spłon", "ogień", "ogni", "wypadk", "wypadek", 
    "kolizj", "zderz", "dachowa", "potrąc", "zablokow", "utrudnieni", "akcj", 
    "zastęp", "straż", "uderz", "wjechał", "wyciek", "plam", "drzew", "ewakuac", 
    "przewróc", "lawet", "karambol", "rann", "poszkodowan", "śmierteln", "runął", "tragedi"
]

# Współrzędne powiatów woj. łódzkiego dla szybkiego stawiania pinezek z KW PSP Łódź
POWIAT_COORDS = {
    "zgierski": (51.855, 19.406, "Powiat zgierski"),
    "łódź": (51.759, 19.456, "Łódź"),
    "łódzki wschodni": (51.730, 19.650, "Powiat łódzki wschodni"),
    "brzeziński": (51.800, 19.750, "Powiat brzeziński"),
    "łowicki": (52.107, 19.945, "Powiat łowicki"),
    "kutnowski": (52.231, 19.364, "Powiat kutnowski"),
    "łęczycki": (52.059, 19.199, "Powiat łęczycki"),
    "pabianicki": (51.664, 19.354, "Powiat pabianicki"),
    "skierniewicki": (51.954, 20.158, "Powiat skierniewicki"),
    "rawski": (51.764, 20.254, "Powiat rawski"),
    "piotrkowski": (51.405, 19.703, "Powiat piotrkowski"),
    "sieradzki": (51.595, 18.730, "Powiat sieradzki"),
}

def scrape_psp_lodz(existing_ids, today_incidents):
    """Pobiera wyjazdy bezpośrednio z tabeli KW PSP Łódź (interwencje.straz.lodz.pl)."""
    url = "https://interwencje.straz.lodz.pl/"
    print(f"\n🚒 Sprawdzam rejestr na żywo KW PSP Łódź: {url}...")
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Accept-Language': 'pl-PL,pl;q=0.9'
    }
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            html = resp.read().decode('utf-8', errors='ignore')
    except Exception as e:
        print(f"⚠️ Serwer interwencje.straz.lodz.pl niedostępny z IP GitHuba: {e}")
        return

    # Wyciągamy czysty tekst wiersz po wierszu (usuwamy tagi HTML)
    clean_text = re.sub(r'<script.*?>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    clean_text = re.sub(r'<style.*?>.*?</style>', '', clean_text, flags=re.DOTALL | re.IGNORECASE)
    rows = re.findall(r'<tr.*?>(.*?)</tr>', clean_text, flags=re.DOTALL | re.IGNORECASE)

    print(f"Pobrano stronę KW PSP Łódź! Znaleziono wierszy tabeli: {len(rows)}")
    
    added = 0
    for row_html in rows[:15]:
        cols = [re.sub(r'<.*?>', ' ', c).strip() for c in re.findall(r'<t[dh].*?>(.*?)</t[dh]>', row_html, flags=re.DOTALL | re.IGNORECASE)]
        cols = [re.sub(r'\s+', ' ', c) for c in cols if c]
        if not cols:
            continue
        
        row_str = " | ".join(cols)
        # Wypisujemy pierwsze 5 wierszy w logach dla podglądu struktury!
        if added < 3:
            print(f"   [Podgląd wiersza PSP]: {row_str[:120]}")

        # Szukamy daty i godziny w formacie DD.MM.YYYY HH:MM lub YYYY-MM-DD HH:MM
        m_date = re.search(r'(\d{2}\.\d{2}\.\d{4}|\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})', row_str)
        if not m_date:
            continue

        raw_date, time_str = m_date.group(1), m_date.group(2)
        if "." in raw_date:
            d, m, y = raw_date.split(".")
            date_str = f"{y}-{m}-{d}"
        else:
            date_str = raw_date

        row_lower = row_str.lower()
        # Sprawdzamy czy to nasz priorytetowy powiat
        matched_powiat = None
        for p_key, p_val in POWIAT_COORDS.items():
            if p_key in row_lower:
                matched_powiat = (p_key, p_val)
                break

        if not matched_powiat:
            continue

        ext_id = f"psp_lodz_{date_str}_{time_str}_{matched_powiat[0]}"
        if ext_id in existing_ids:
            continue

        # Określamy kategorię na podstawie opisu w wierszu
        cat = "hazard"
        title_prefix = "Interwencja PSP (Miejscowe zagrożenie)"
        if "pożar" in row_lower or "płonie" in row_lower or "zadymienie" in row_lower:
            cat = "fire"
            title_prefix = "Pożar - Interwencja PSP"
        elif any(w in row_lower for w in ["wypadek", "kolizja", "zderzenie", "drogow", "dachowanie"]):
            cat = "accident"
            title_prefix = "Zdarzenie drogowe - Interwencja PSP"

        lat, lng, loc_name = matched_powiat[1]
        # Lekki rozrzut współrzędnych, by pinezki z tego samego powiatu nie nakładały się w 1 punkt
        offset = ((hash(ext_id) % 100) - 50) * 0.0006

        insert_data = {
            "external_id": ext_id,
            "title": f"{title_prefix} ({loc_name})",
            "category": cat,
            "status": "Trwa akcja",
            "location": loc_name,
            "region": "łódzkie (SWD PSP)",
            "lat": lat + offset,
            "lng": lng + offset,
            "date_str": date_str,
            "time_str": time_str,
            "units": "Zastępy PSP / OSP (SWD KW PSP Łódź)",
            "description": f"Oficjalny meldunek z rejestru KW PSP Łódź: {row_str}",
            "source_url": url
        }

        try:
            supabase.table('incidents').insert(insert_data).execute()
            existing_ids.add(ext_id)
            today_incidents.append(insert_data)
            added += 1
            print(f"✅ [KW PSP ŁÓDŹ] Dodano wyjazd na żywo: {insert_data['title']} [{time_str}]")
            if added >= 4:
                break
        except Exception as e:
            print(f"Błąd zapisu wyjazdu PSP: {e}")

def get_available_models():
    preferred = ["gemini-flash-lite-latest", "gemini-3.8-flash-lite", "gemini-3.8-flash", "gemini-flash-latest"]
    try:
        for m in ai_client.models.list():
            name = m.name.replace("models/", "")
            if "gemini" in name and ("flash" in name or "pro" in name):
                if any(bad in name for bad in ["tts", "vision", "embedding", "audio", "image", "thinking"]):
                    continue
                if name not in preferred:
                    preferred.append(name)
    except Exception:
        pass
    return preferred

def is_duplicate_today(new_title, new_loc, new_cat, new_lat, new_lng, today_incidents):
    t1 = new_title.lower().strip()
    l1 = new_loc.lower().strip()
    for inc in today_incidents:
        t2 = (inc.get("title") or "").lower().strip()
        l2 = (inc.get("location") or "").lower().strip()
        cat2 = inc.get("category")
        if t1 == t2 or (l1 == l2 and l1 != "polska" and new_cat == cat2):
            return True
        if new_cat == cat2 and inc.get("lat") and inc.get("lng"):
            dist = math.hypot(new_lat - float(inc["lat"]), new_lng - float(inc["lng"]))
            if dist < 0.035:
                return True
    return False

def run_worker():
    print("Rozpoczynam inteligentne skanowanie (KW PSP Łódź + Kraj + Okolice)...")
    
    available_models = get_available_models()
    now_pl = datetime.now()
    today_str = now_pl.strftime("%Y-%m-%d")

    existing_res = supabase.table('incidents').select('external_id, title, location, category, lat, lng, date_str').order('created_at', desc=True).limit(400).execute()
    all_rows = existing_res.data or []
    existing_ids = set(r['external_id'] for r in all_rows if r.get('external_id'))
    today_incidents = [r for r in all_rows if r.get('date_str') == today_str]

    # KROK 1: Skanujemy oficjalną stronę KW PSP Łódź (0 zużycia limitu AI!)
    scrape_psp_lodz(existing_ids, today_incidents)

    # KROK 2: Skanujemy kanały RSS z udziałem AI
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36'}
    ai_calls_this_run = 0
    MAX_AI_CALLS_PER_RUN = 5

    for feed_url in RSS_FEEDS:
        if ai_calls_this_run >= MAX_AI_CALLS_PER_RUN:
            break

        print(f"\nSkanuję źródło RSS: {feed_url[:65]}...")
        try:
            req = urllib.request.Request(feed_url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                xml_data = resp.read()
            parsed_feed = feedparser.parse(xml_data)
        except Exception as e:
            print(f"Błąd pobierania RSS: {e}")
            continue

        for entry in parsed_feed.entries[:5]:
            if ai_calls_this_run >= MAX_AI_CALLS_PER_RUN:
                break

            external_id = entry.get('id') or entry.get('link')
            title = entry.get('title', '')
            summary = entry.get('summary', '') or entry.get('description', '')
            link = entry.get('link', '')

            if not external_id or not title or external_id in existing_ids:
                continue

            text_lower = (title + " " + summary).lower()
            if any(bad in text_lower for bad in IGNORE_WORDS):
                existing_ids.add(external_id)
                continue

            if not any(stem in text_lower for stem in ACTION_STEMS):
                existing_ids.add(external_id)
                continue

            print(f"🔍 Analizuję przez AI nowy wpis: {title}")

            prompt = f"""
            Przeanalizuj poniższy artykuł dotyczący zdarzenia ratowniczego w Polsce.
            Tytuł: {title}
            Treść: {summary}

            Zwróć wynik WYŁĄCZNIE jako czysty obiekt JSON:
            - "is_incident": boolean (true tylko jeśli to konkretny pożar, wypadek, kolizja lub akcja służb w Polsce; false jeśli to zagranica, felieton, śledztwo po dniach lub statystyka).
            - "title": string (Krótki tytuł meldunku po polsku).
            - "category": string (jedno z: "fire", "accident", "hazard").
            - "status": string (jedno z: "Trwa akcja", "Opanowane", "Zakończone").
            - "location": string (Miejscowość i droga/ulica).
            - "geocode_query": string (Sama nazwa miejscowości do mapy).
            - "region": string (Województwo lub powiat).
            - "units": string (Siły i Środki, np. "PSP, OSP, ZRM, Policja").
            - "description": string (Krótki opis 1-2 zdania po polsku).
            - "estimated_lat": number (Szerokość GPS w Polsce).
            - "estimated_lng": number (Długość GPS w Polsce).
            """

            response = None
            for model_name in available_models:
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
                    break
                except Exception:
                    continue

            if not response:
                return

            try:
                result_text = response.text.strip()
                if result_text.startswith("```"):
                    result_text = result_text.split("```")[1]
                    if result_text.startswith("json"):
                        result_text = result_text[4:]
                
                data = json.loads(result_text.strip())
                existing_ids.add(external_id)

                if not data.get("is_incident", False):
                    continue

                lat = float(data.get("estimated_lat") or 51.90)
                lng = float(data.get("estimated_lng") or 19.50)
                geo_q = data.get("geocode_query", "")

                if geo_q:
                    geo_url = f"https://nominatim.openstreetmap.org/search?q={urllib.parse.quote(geo_q + ', Polska')}&format=json&limit=1"
                    geo_req = urllib.request.Request(geo_url, headers={'User-Agent': 'StrazAlertApp/1.0'})
                    try:
                        with urllib.request.urlopen(geo_req, timeout=10) as geo_res:
                            geo_data = json.loads(geo_res.read().decode())
                            if geo_data and len(geo_data) > 0:
                                lat = float(geo_data[0]['lat'])
                                lng = float(geo_data[0]['lon'])
                    except Exception:
                        pass

                inc_title = data.get("title", title)[:120]
                inc_loc = data.get("location", "Polska")
                inc_cat = data.get("category", "hazard")

                if is_duplicate_today(inc_title, inc_loc, inc_cat, lat, lng, today_incidents):
                    print(f"🔄 Pominięto duplikat: {inc_title}")
                    continue

                insert_data = {
                    "external_id": external_id,
                    "title": inc_title,
                    "category": inc_cat,
                    "status": data.get("status", "Trwa akcja"),
                    "location": inc_loc,
                    "region": data.get("region", "Polska"),
                    "lat": lat,
                    "lng": lng,
                    "date_str": today_str,
                    "time_str": now_pl.strftime("%H:%M"),
                    "units": data.get("units", "PSP, OSP, Policja"),
                    "description": data.get("description", title),
                    "source_url": link
                }

                supabase.table('incidents').insert(insert_data).execute()
                today_incidents.append(insert_data)
                print(f"✅ SUKCES! Dodano na mapę: {inc_title} ({inc_loc})")

            except Exception as e:
                print(f"❌ Błąd zapisu: {e}")

if __name__ == "__main__":
    run_worker()
