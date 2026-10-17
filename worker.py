import os
import json
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
    # 1. Okolice: Stryków, Głowno, Zgierz, Łódź, powiat zgierski (PRIORYTET 1)
    "https://news.google.com/rss/search?q=(wypadek+OR+po%C5%BCar+OR+kolizja+OR+zderzenie)+(Stryk%C3%B3w+OR+G%C5%82owno+OR+Zgierz+OR+%C5%81%C3%B3d%C5%BA+OR+zgierski)+when:1d&hl=pl&gl=PL&ceid=PL:pl",
    
    # 2. Autostrady A1 i A2 oraz drogi w woj. łódzkim (PRIORYTET 2)
    "https://news.google.com/rss/search?q=(wypadek+OR+po%C5%BCar+OR+zablokowana+OR+dachowanie)+(A1+OR+A2+OR+S8+OR+DK14+OR+%C5%82%C3%B3dzkie)+when:1d&hl=pl&gl=PL&ceid=PL:pl",
    
    # 3. Ogólnopolski portal strażacki
    "https://remiza.com.pl/feed/",
    
    # 4. Ogólnopolskie akcje straży pożarnej z dzisiaj
    "https://news.google.com/rss/search?q=(stra%C5%BC+po%C5%BCarna+OR+JRG+OR+OSP)+(po%C5%BCar+OR+wypadek+OR+zderzenie)+when:1d&hl=pl&gl=PL&ceid=PL:pl"
]

# Słowa, które sugerują, że to zwykły artykuł/wspomnienie/polityka, a nie bieżąca akcja (oszczędzamy limit AI!)
IGNORE_WORDS = [
    "zbiórka", "pieniądze", "miliony", "nieśmiertelniki", "ślubowanie", "awans", 
    "odznaczenia", "przetarg", "zakup", "wspomnienie", "rocznica", "pogrzeb", 
    "żegnają", "oddali hołd", "nie żyje komendant", "był zastępcą", "lotto", 
    "skrót dnia", "przegląd", "treningi", "szkolenie", "ćwiczenia", "zawody", "turniej"
]

# Słowa wymagane, by w ogóle pytać AI
ACTION_WORDS = [
    "pożar", "płonie", "spłonął", "ogień", "wypadek", "kolizja", "zderzenie", 
    "dachowanie", "potrącenie", "zablokowana", "utrudnienia", "akcja", "zastęp", 
    "straż", "uderzył", "wjechał", "wyciek", "plama", "drzewo", "ewakuacja"
]

def get_available_models():
    """Pobiera listę modeli dostępnych na kluczu użytkownika, z priorytetem dla serii 3.8 i lite."""
    preferred = ["gemini-3.8-flash", "gemini-3.8-flash-lite", "gemini-3.8-pro"]
    try:
        for m in ai_client.models.list():
            name = m.name.replace("models/", "")
            if "gemini" in name and ("flash" in name or "pro" in name) and "vision" not in name and "embedding" not in name:
                if name not in preferred:
                    preferred.append(name)
    except Exception as e:
        print(f"Info o liście modeli: {e}")
    return preferred

def run_worker():
    print("Rozpoczynam inteligentne skanowanie (z ochroną limitu zapytań AI)...")
    
    available_models = get_available_models()
    print(f"Dostępne modele AI w kolejności użycia: {available_models[:5]}")

    existing_res = supabase.table('incidents').select('external_id').order('created_at', desc=True).limit(400).execute()
    existing_ids = set(r['external_id'] for r in (existing_res.data or []) if r.get('external_id'))

    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36'}
    ai_calls_this_run = 0
    MAX_AI_CALLS_PER_RUN = 4 # Maksymalnie 4 zapytania na jeden przelot, by oszczędzać dzienną pulę!

    for feed_url in RSS_FEEDS:
        if ai_calls_this_run >= MAX_AI_CALLS_PER_RUN:
            print("Osiągnięto bezpieczny limit zapytań w tym cyklu (ochrona dziennego limitu). Reszta w kolejnym cyklu.")
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

        for entry in parsed_feed.entries[:4]:
            if ai_calls_this_run >= MAX_AI_CALLS_PER_RUN:
                break

            external_id = entry.get('id') or entry.get('link')
            title = entry.get('title', '')
            summary = entry.get('summary', '') or entry.get('description', '')
            link = entry.get('link', '')

            if not external_id or not title or external_id in existing_ids:
                continue

            # WSTĘPNY FILTR W PYTHONIE (0 zużycia limitu Gemini!)
            text_lower = (title + " " + summary).lower()
            if any(bad in text_lower for bad in IGNORE_WORDS):
                print(f"⏭️ Pominięto bez użycia AI (artykuł ogólny/pieniądze/wspomnienie): {title[:55]}...")
                existing_ids.add(external_id)
                continue

            if not any(good in text_lower for good in ACTION_WORDS):
                print(f"⏭️ Pominięto bez użycia AI (brak słów o akcji ratowniczej): {title[:55]}...")
                existing_ids.add(external_id)
                continue

            print(f"🔍 Analizuję przez AI nowy wpis: {title}")

            prompt = f"""
            Przeanalizuj poniższy artykuł dotyczący zdarzenia ratowniczego w Polsce.
            Tytuł: {title}
            Treść: {summary}

            Zwróć wynik WYŁĄCZNIE jako czysty obiekt JSON:
            - "is_incident": boolean (true tylko jeśli to konkretny pożar, wypadek, kolizja lub akcja służb; false jeśli to felieton lub statystyka).
            - "title": string (Krótki tytuł meldunku po polsku, np. "Wypadek pod Skierniewicami", "Zablokowana autostrada A2").
            - "category": string (jedno z: "fire", "accident", "hazard").
            - "status": string (jedno z: "Trwa akcja", "Opanowane", "Zakończone").
            - "location": string (Miejscowość i droga/ulica, np. "Skierniewice", "Autostrada A1 pod Kutnem").
            - "geocode_query": string (Sama nazwa miejscowości do mapy, np. "Skierniewice, łódzkie" lub "Kutno, łódzkie").
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
                    err_str = str(e)
                    if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                        print(f"   ⚠️ Model {model_name} wyczerpał dzienny limit (429), sprawdzam zapasowy model...")
                        continue
                    elif "404" in err_str or "NOT_FOUND" in err_str:
                        continue
                    else:
                        print(f"   ⚠️ Inny błąd modelu {model_name}: {e}")
                        continue

            if not response:
                print("❌ Wszystkie modele wyczerpały aktualny darmowy limit! Odczekaj chwilę na odnowienie puli.")
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
                    print("-> AI uznało, że to nie jest bieżąca akcja.")
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

                now_pl = datetime.now()
                insert_data = {
                    "external_id": external_id,
                    "title": data.get("title", title)[:120],
                    "category": data.get("category", "hazard"),
                    "status": data.get("status", "Trwa akcja"),
                    "location": data.get("location", "Polska"),
                    "region": data.get("region", "Polska"),
                    "lat": lat,
                    "lng": lng,
                    "date_str": now_pl.strftime("%Y-%m-%d"),
                    "time_str": now_pl.strftime("%H:%M"),
                    "units": data.get("units", "PSP, OSP, Policja"),
                    "description": data.get("description", title),
                    "source_url": link
                }

                supabase.table('incidents').insert(insert_data).execute()
                print(f"✅ SUKCES! Dodano na mapę: {insert_data['title']} ({insert_data['location']})")

            except Exception as e:
                print(f"❌ Błąd zapisu: {e}")

if __name__ == "__main__":
    run_worker()
