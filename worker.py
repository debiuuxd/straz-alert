import os
import json
import math
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
    # 1. Okolice: Stryków, Głowno, Zgierz, Łódź, Brzeziny, Łowicz, Kutno, powiat zgierski (PRIORYTET 1)
    "https://news.google.com/rss/search?q=(wypadek+OR+po%C5%BCar+OR+kolizja+OR+stra%C5%BC)+(Stryk%C3%B3w+OR+G%C5%82owno+OR+Zgierz+OR+%C5%81%C3%B3d%C5%BA+OR+zgierski+OR+Brzeziny)+when:1d&hl=pl&gl=PL&ceid=PL:pl",
    
    # 2. Autostrady A1 i A2 oraz drogi w woj. łódzkim (PRIORYTET 2)
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
    except Exception as e:
        print(f"Info o liście modeli: {e}")
    return preferred

def is_duplicate_today(new_title, new_loc, new_cat, new_lat, new_lng, today_incidents):
    """Sprawdza, czy inna redakcja nie opisała już dzisiaj tego samego zdarzenia."""
    t1 = new_title.lower().strip()
    l1 = new_loc.lower().strip()

    for inc in today_incidents:
        t2 = (inc.get("title") or "").lower().strip()
        l2 = (inc.get("location") or "").lower().strip()
        cat2 = inc.get("category")

        # 1. Identyczny tytuł lub identyczna lokalizacja w tej samej kategorii
        if t1 == t2 or (l1 == l2 and l1 != "polska" and new_cat == cat2):
            return True

        # 2. Bardzo bliska odległość GPS (ok. 3-4 km) w tej samej kategorii zdarzenia
        if new_cat == cat2 and inc.get("lat") and inc.get("lng"):
            dist = math.hypot(new_lat - float(inc["lat"]), new_lng - float(inc["lng"]))
            if dist < 0.035: # ~3.5 km w linii prostej
                return True

    return False

def run_worker():
    print("Rozpoczynam inteligentne skanowanie (z Anty-Duplikatorem)...")
    
    available_models = get_available_models()
    print(f"Kolejność modeli AI: {available_models[:4]}")

    now_pl = datetime.now()
    today_str = now_pl.strftime("%Y-%m-%d")

    # Pobieramy ostatnie wpisy do sprawdzenia linków oraz dzisiejsze wpisy do Anty-Duplikatora
    existing_res = supabase.table('incidents').select('external_id, title, location, category, lat, lng, date_str').order('created_at', desc=True).limit(400).execute()
    all_rows = existing_res.data or []
    existing_ids = set(r['external_id'] for r in all_rows if r.get('external_id'))
    today_incidents = [r for r in all_rows if r.get('date_str') == today_str]

    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36'}
    ai_calls_this_run = 0
    MAX_AI_CALLS_PER_RUN = 5

    for feed_url in RSS_FEEDS:
        if ai_calls_this_run >= MAX_AI_CALLS_PER_RUN:
            print("Osiągnięto limit 5 analiz w tym cyklu.")
            break

        print(f"\nSkanuję źródło: {feed_url[:65]}...")
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
                print(f"⏭️ Pominięto bez AI (temat poboczny): {title[:55]}...")
                existing_ids.add(external_id)
                continue

            if not any(stem in text_lower for stem in ACTION_STEMS):
                print(f"⏭️ Pominięto bez AI (brak słów o akcji): {title[:55]}...")
                existing_ids.add(external_id)
                continue

            print(f"🔍 Analizuję przez AI nowy wpis: {title}")

            prompt = f"""
            Przeanalizuj poniższy artykuł dotyczący zdarzenia ratowniczego w Polsce.
            Tytuł: {title}
            Treść: {summary}

            Zwróć wynik WYŁĄCZNIE jako czysty obiekt JSON:
            - "is_incident": boolean (true tylko jeśli to konkretny pożar, wypadek, kolizja lub akcja służb w Polsce; false jeśli to zagranica, felieton, śledztwo po dniach lub statystyka).
            - "title": string (Krótki tytuł meldunku po polsku, np. "Wypadek na A1 pod Kutnem", "Zablokowana autostrada A2").
            - "category": string (jedno z: "fire", "accident", "hazard").
            - "status": string (jedno z: "Trwa akcja", "Opanowane", "Zakończone").
            - "location": string (Miejscowość i droga/ulica, np. "Kutno, Autostrada A1", "Stryków, DK14").
            - "geocode_query": string (Sama nazwa miejscowości do mapy, np. "Kutno, łódzkie" lub "Stryków, łódzkie").
            - "region": string (Województwo lub powiat, np. "łódzkie", "powiat zgierski").
            - "units": string (Siły i Środki, np. "PSP, OSP, ZRM, Policja").
            - "description": string (Krótki opis 1-2 zdania po polsku).
            - "estimated_lat": number (Szerokość GPS w Polsce, np. 51.95).
            - "estimated_lng": number (Długość GPS w Polsce, np. 19.45).
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
                    print(f"   (Odpowiedział model: {model_name})")
                    break
                except Exception as e:
                    if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                        print(f"   ⚠️ Model {model_name} wyczerpał limit (429), przełączam...")
                        continue
                    continue

            if not response:
                print("❌ Wszystkie modele wyczerpały aktualny limit!")
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
                    print("-> AI uznało, że to nie jest bieżąca akcja w Polsce.")
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

                # SPRAWDZAMY DUPLIKATY Z DZISIAJ
                if is_duplicate_today(inc_title, inc_loc, inc_cat, lat, lng, today_incidents):
                    print(f"🔄 Pominięto duplikat (to zdarzenie jest już dzisiaj na mapie): {inc_title} ({inc_loc})")
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
