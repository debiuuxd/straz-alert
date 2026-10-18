import os
import re
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
    "kanada", "kanady", "albercie", "usa", "niemczech", "ukrain"
]

# Precyzyjne słowa-klucze (sprawdzane w samym TYTULE artykułu, bez fałszywych trafień jak "atrakcje")
TITLE_ACTION_WORDS = [
    "pożar", "płonie", "płoną", "spłonął", "spłonęła", "ogień", "zadymienie", "czad", "wyciek",
    "wypadek", "wypadku", "kolizja", "kolizji", "zderzenie", "zderzyły", "dachowanie", "dachował",
    "potrącenie", "potrącony", "potrącona", "zablokowana", "utrudnienia", "straż", "strażacy",
    "interwencja", "interweniowała", "uderzył", "wbił się", "wjechał", "powalone drzewo", "ewakuacja",
    "przewróciła", "karambol", "ranny", "ranna", "ranni", "poszkodowany", "poszkodowana", "śmiertelny",
    "runął", "wybuch", "eksplozja", "szpitalu", "szpitala"
]

STOP_WORDS = {
    "pożar", "wypadek", "kolizja", "zderzenie", "straż", "straży", "strażacy", "akcja", 
    "ulicy", "droga", "powiat", "okolice", "łodzi", "łódź", "łowiczu", "łowicz", "policja",
    "kierowca", "szpitala", "szpitalu", "centrum", "nocny", "jedna", "osoba", "trafiła"
}

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

def extract_keywords(text):
    clean = re.sub(r'[^a-ząćęłńóśźż0-9\s]', ' ', text.lower())
    return set(w for w in clean.split() if len(w) > 3 and w not in STOP_WORDS)

def is_pre_duplicate(raw_title, recent_incidents, seen_in_this_run):
    """Sprawdza PRZED zapytaniem do AI, czy ten temat już był (oszczędza limit AI!)."""
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
    """Sprawdza po analizie AI, czy identyczna akcja nie wisi już na mapie."""
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

        # Bardzo bliska odległość GPS (~1.5 km) ORAZ ta sama kategoria zdarzenia
        if new_cat == cat2 and inc.get("lat") and inc.get("lng"):
            dist = math.hypot(new_lat - float(inc["lat"]), new_lng - float(inc["lng"]))
            if dist < 0.015:
                return True

    return False

def run_worker():
    print("Rozpoczynam szybkie skanowanie (Pre-Duplikator + Czysty Prompt AI)...")
    
    available_models = get_available_models()
    print(f"Kolejność modeli AI: {available_models[:4]}")

    now_pl = datetime.now()
    today_str = now_pl.strftime("%Y-%m-%d")

    existing_res = supabase.table('incidents').select('id, external_id, title, description, location, category, lat, lng, date_str').order('id', desc=True).limit(400).execute()
    all_rows = existing_res.data or []
    existing_ids = set(r['external_id'] for r in all_rows if r.get('external_id'))
    recent_incidents = all_rows[:60]

    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36'}
    ai_calls_this_run = 0
    MAX_AI_CALLS_PER_RUN = 12
    seen_in_this_run = []

    for feed_url in RSS_FEEDS:
        if ai_calls_this_run >= MAX_AI_CALLS_PER_RUN:
            print("Osiągnięto limit 12 analiz AI w tym cyklu.")
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

        entries = list(parsed_feed.entries)
        entries.sort(key=lambda x: x.get('published_parsed') or (0,), reverse=True)

        for entry in entries[:20]:
            if ai_calls_this_run >= MAX_AI_CALLS_PER_RUN:
                break

            external_id = entry.get('id') or entry.get('link')
            title = entry.get('title', '')
            summary = entry.get('summary', '') or entry.get('description', '')
            link = entry.get('link', '')

            if not external_id or not title or external_id in existing_ids:
                continue

            # Odcinamy nazwę gazety z końca tytułu (np. " - TuLodz.pl")
            clean_title = title.rsplit(" - ", 1)[0] if " - " in title else title
            title_lower = clean_title.lower()

            if any(bad in title_lower for bad in IGNORE_TITLE_WORDS):
                existing_ids.add(external_id)
                continue

            if not any(w in title_lower for w in TITLE_ACTION_WORDS):
                existing_ids.add(external_id)
                continue

            # Sprawdzamy PRZED wysłaniem do AI, czy to nie powtórka (np. Lexus w Łowiczu lub drugi artykuł o tym samym tramwaju)
            if is_pre_duplicate(clean_title, recent_incidents, seen_in_this_run):
                print(f"⏭️ Pominięto przed AI (temat jest już w bazie): {clean_title[:60]}...")
                existing_ids.add(external_id)
                continue

            print(f"🔍 Analizuję przez AI nowy wpis: {title}")

            prompt = f"""
            Przeanalizuj poniższy artykuł prasowy o zdarzeniu w Polsce.
            Tytuł: {clean_title}
            Opis: {summary}

            Oceń, czy to konkretne zdarzenie ratownicze, drogowe lub pożarowe w Polsce (pożar, wypadek, kolizja, zderzenie tramwajów/aut, potrącenie, wyciek gazu, interwencja straży/służb).
            Jeśli to zdarzenie za granicą (np. Kanada, USA), otwarcie sklepu lub sadzenie drzew -> ustaw "is_incident": false.
            Jeśli to realne zdarzenie w Polsce -> ustaw "is_incident": true!

            Zwróć WYŁĄCZNIE obiekt JSON:
            - "is_incident": boolean
            - "title": string (Krótki tytuł meldunku po polsku, np. "Zderzenie tramwajów na ul. Narutowicza w Łodzi").
            - "category": string (jedno z: "fire", "accident", "hazard").
            - "status": string (jedno z: "Trwa akcja", "Opanowane", "Zakończone").
            - "location": string (Miejscowość i ulica/droga, np. "Łódź, ul. Narutowicza").
            - "geocode_query": string (Miejscowość i województwo do mapy, np. "Łódź, Narutowicza" lub "Stryków, łódzkie").
            - "region": string (Województwo lub powiat, np. "łódzkie").
            - "units": string (Siły i Środki, np. "PSP, OSP, ZRM, Policja").
            - "description": string (Krótki opis 1-2 zdania po polsku).
            - "estimated_lat": number (Szerokość GPS w Polsce, np. 51.77).
            - "estimated_lng": number (Długość GPS w Polsce, np. 19.47).
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
                seen_in_this_run.append(extract_keywords(clean_title))

                if not data.get("is_incident", False):
                    print("-> AI odrzuciło (to nie jest akcja ratownicza w Polsce).")
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

                inc_title = data.get("title", clean_title)[:120]
                inc_loc = data.get("location", "Polska")
                inc_cat = data.get("category", "hazard")

                if is_duplicate_recent(inc_title, inc_loc, inc_cat, lat, lng, recent_incidents):
                    print(f"🔄 Anty-Duplikator zablokował powtórkę: {inc_title} ({inc_loc})")
                    continue

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
                    "description": data.get("description", clean_title),
                    "source_url": link
                }

                supabase.table('incidents').insert(insert_data).execute()
                recent_incidents.insert(0, insert_data)
                print(f"✅ SUKCES! Dodano na mapę: {inc_title} ({inc_loc})")

            except Exception as e:
                print(f"❌ Błąd zapisu: {e}")

if __name__ == "__main__":
    run_worker()
