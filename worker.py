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
    # 1. Lokalne portale z regionu (Łowiczanin, TuZgierz, TuŁódź, Express Ilustrowany, Dziennik Łódzki)
    "https://news.google.com/rss/search?q=(site:lowiczanin.info+OR+site:tuzgierz.pl+OR+site:tulodz.pl+OR+site:expressilustrowany.pl+OR+site:dzienniklodzki.pl)+(stra%C5%BC+OR+po%C5%BCar+OR+wypadek+OR+kolizja+OR+zderzenie)+when:1d&hl=pl&gl=PL&ceid=PL:pl",

    # 2. Okolice: Stryków, Głowno, Zgierz, Łódź, Brzeziny, Łowicz, Kutno, powiat zgierski
    "https://news.google.com/rss/search?q=(wypadek+OR+po%C5%BCar+OR+kolizja+OR+stra%C5%BC+OR+zderzenie)+(Stryk%C3%B3w+OR+G%C5%82owno+OR+Zgierz+OR+%C5%81%C3%B3d%C5%BA+OR+zgierski+OR+%C5%81owicz+OR+Kutno+OR+Brzeziny)+when:1d&hl=pl&gl=PL&ceid=PL:pl",
    
    # 3. Autostrady A1 i A2 oraz główne drogi w woj. łódzkim
    "https://news.google.com/rss/search?q=(wypadek+OR+po%C5%BCar+OR+zablokowana+OR+dachowanie+OR+utrudnienia)+(A1+OR+A2+OR+S8+OR+S14+OR+DK14+OR+DK92+OR+%C5%82%C3%B3dzkie)+when:1d&hl=pl&gl=PL&ceid=PL:pl",
    
    # 4. Ogólnopolski portal strażacki Remiza.com.pl
    "https://remiza.com.pl/feed/",
    
    # 5. Ogólnopolskie akcje straży pożarnej z ostatnich 24h
    "https://news.google.com/rss/search?q=(stra%C5%BC+po%C5%BCarna+OR+JRG+OR+OSP)+(po%C5%BCar+OR+wypadek+OR+zderzenie+OR+akcja)+when:1d&hl=pl&gl=PL&ceid=PL:pl"
]

# Sprawdzane TYLKO w tytule artykułu, żeby boczne linki w opisie nie blokowały prawdziwych wypadków
IGNORE_TITLE_WORDS = [
    "zbiórka", "nieśmiertelniki", "ślubowanie", "awans", "odznaczenia", 
    "przetarg", "wspomnienie", "rocznica", "pogrzeb", "żegnają", 
    "oddali hołd", "nie żyje komendant", "był zastępcą", "lotto", 
    "losowania", "szczęśliwe liczby", "skrót dnia", "trening", 
    "szkolenie", "ćwiczenia", "zawody", "turniej", "humbak", "wieloryb", 
    "sekcje zwłok", "prokuratura", "areszt", "zarzuty", "wyrok"
]

ACTION_STEMS = [
    "pożar", "płon", "płomien", "spłon", "ogień", "ogni", "zadymien", "czad", "gaz",
    "wypadk", "wypadek", "kolizj", "zderz", "dachowa", "potrąc", "zablokow", 
    "utrudnieni", "akcj", "zastęp", "straż", "interwenc", "interweniowa", "uderz", 
    "wjechał", "wyciek", "plam", "drzew", "ewakuac", "przewróc", "lawet", 
    "karambol", "rann", "poszkodowan", "śmierteln", "runął", "tragedi", "wybuch", "eksploz"
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

def is_duplicate_recent(new_title, new_loc, new_cat, new_lat, new_lng, recent_incidents):
    """Precyzyjny Anty-Duplikator: blokuje powtórki tej samej akcji, ale pozwala na wiele różnych akcji w tym samym mieście."""
    t1 = new_title.lower().strip()
    l1 = new_loc.lower().strip()
    stop_words = {"pożar", "wypadek", "kolizja", "zderzenie", "straż", "straży", "akcja", "ulicy", "droga", "powiat", "okolice"}
    words1 = set(w for w in t1.replace(",", " ").replace("-", " ").split() if len(w) > 3 and w not in stop_words)

    for inc in recent_incidents:
        t2 = (inc.get("title") or "").lower().strip()
        l2 = (inc.get("location") or "").lower().strip()
        cat2 = inc.get("category")

        # 1. Identyczny tytuł
        if t1 == t2:
            return True

        # 2. Podobne kluczowe słowa w tytule (np. "szybowiec", "stajni", "nisku", "tuszewskiej")
        words2 = set(w for w in t2.replace(",", " ").replace("-", " ").split() if len(w) > 3 and w not in stop_words)
        if len(words1 & words2) >= 2:
            return True

        # 3. Bardzo bliska odległość GPS (~2 km) ORAZ ta sama kategoria zdarzenia
        if new_cat == cat2 and inc.get("lat") and inc.get("lng"):
            dist = math.hypot(new_lat - float(inc["lat"]), new_lng - float(inc["lng"]))
            if dist < 0.018:
                return True

    return False

def run_worker():
    print("Rozpoczynam głębokie skanowanie (20 wpisów na kanał + Poprawiony Anty-Duplikator)...")
    
    available_models = get_available_models()
    print(f"Kolejność modeli AI: {available_models[:4]}")

    now_pl = datetime.now()
    today_str = now_pl.strftime("%Y-%m-%d")

    existing_res = supabase.table('incidents').select('id, external_id, title, location, category, lat, lng, date_str').order('id', desc=True).limit(400).execute()
    all_rows = existing_res.data or []
    existing_ids = set(r['external_id'] for r in all_rows if r.get('external_id'))
    
    recent_incidents = all_rows[:50]
    recent_summary_for_ai = "\n".join(
        [f"- {r.get('title')} ({r.get('location')}, data: {r.get('date_str')})" for r in all_rows[:35]]
    )

    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36'}
    ai_calls_this_run = 0
    MAX_AI_CALLS_PER_RUN = 8

    for feed_url in RSS_FEEDS:
        if ai_calls_this_run >= MAX_AI_CALLS_PER_RUN:
            print("Osiągnięto limit 8 analiz AI w tym cyklu.")
            break

        print(f"\nSkanuję źródło: {feed_url[:68]}...")
        try:
            req = urllib.request.Request(feed_url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                xml_data = resp.read()
            parsed_feed = feedparser.parse(xml_data)
        except Exception as e:
            print(f"Błąd pobierania RSS: {e}")
            continue

        # Sortujemy wpisy od najnowszych, żeby poranne artykuły nie blokowały wieczornych!
        entries = list(parsed_feed.entries)
        entries.sort(key=lambda x: x.get('published_parsed') or (0,), reverse=True)

        # Sprawdzamy aż 20 najnowszych wpisów w każdym kanale (zamiast tylko 5!)
        for entry in entries[:20]:
            if ai_calls_this_run >= MAX_AI_CALLS_PER_RUN:
                break

            external_id = entry.get('id') or entry.get('link')
            title = entry.get('title', '')
            summary = entry.get('summary', '') or entry.get('description', '')
            link = entry.get('link', '')

            if not external_id or not title or external_id in existing_ids:
                continue

            title_lower = title.lower()
            text_lower = (title + " " + summary).lower()

            if any(bad in title_lower for bad in IGNORE_TITLE_WORDS):
                print(f"⏭️ Pominięto (temat poboczny w tytule): {title[:55]}...")
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

            LISTA ZDARZEŃ, KTÓRE JUŻ MAMY W BAZIE:
            {recent_summary_for_ai}

            ZASADY:
            1. Jeśli ten artykuł opisuje DOKŁADNIE TO SAMO zdarzenie, które już jest na powyższej liście (np. kolejny artykuł o pożarze w Nisku, szybowcu w Pruszczu Gdańskim itp.), ustaw "is_incident": false.
            2. Jeśli to NOWY pożar, wypadek, kolizja lub interwencja straży w Polsce (nawet w mieście, które już było na liście, ale inne zdarzenie), ustaw "is_incident": true!

            Zwróć wynik WYŁĄCZNIE jako czysty obiekt JSON:
            - "is_incident": boolean
            - "title": string (Krótki, konkretny tytuł meldunku po polsku).
            - "category": string (jedno z: "fire", "accident", "hazard").
            - "status": string (jedno z: "Trwa akcja", "Opanowane", "Zakończone").
            - "location": string (Miejscowość i ulica/droga, np. "Zgierz, ul. Długa", "Łowicz, DK92").
            - "geocode_query": string (Miejscowość i ulica do mapy, np. "Zgierz, łódzkie").
            - "region": string (Województwo lub powiat, np. "powiat zgierski", "łódzkie").
            - "units": string (Siły i Środki, np. "PSP, OSP, ZRM, Policja").
            - "description": string (Krótki opis 1-2 zdania po polsku).
            - "estimated_lat": number (Szerokość GPS w Polsce, np. 51.85).
            - "estimated_lng": number (Długość GPS w Polsce, np. 19.41).
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
                    print("-> AI odrzuciło (duplikat lub artykuł nienewsowy).")
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

                if is_duplicate_recent(inc_title, inc_loc, inc_cat, lat, lng, recent_incidents):
                    print(f"🔄 Anty-Duplikator zablokował powtórkę: {inc_title} ({inc_loc})")
                    continue

                # Lekki offset, żeby różne zdarzenia w tym samym mieście nie przykrywały się idealnie w 1 punkcie
                offset = ((hash(external_id) % 60) - 30) * 0.0003

                insert_data = {
                    "external_id": external_id,
                    "title": inc_title,
                    "category": inc_cat,
                    "status": data.get("status", "Trwa akcja"),
                    "location": inc_loc,
                    "region": data.get("region", "Polska"),
                    "lat": lat + offset,
                    "lng": lng + offset,
                    "date_str": today_str,
                    "time_str": now_pl.strftime("%H:%M"),
                    "units": data.get("units", "PSP, OSP, Policja"),
                    "description": data.get("description", title),
                    "source_url": link
                }

                supabase.table('incidents').insert(insert_data).execute()
                recent_incidents.insert(0, insert_data)
                print(f"✅ SUKCES! Dodano na mapę: {inc_title} ({inc_loc})")

            except Exception as e:
                print(f"❌ Błąd zapisu: {e}")

if __name__ == "__main__":
    run_worker()
