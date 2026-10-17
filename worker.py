import os
import json
import feedparser
from google import genai
from google.genai import types
from supabase import create_client

# Konfiguracja klientów z tajnych kluczy GitHuba
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
ai_client = genai.Client(api_key=GEMINI_API_KEY)

# Lista źródeł: Portal ogólnopolski + Agregatory Google News dedykowane pod Twoje okolice i Polskę
RSS_FEEDS = [
    # Główne źródło ogólnopolskie
    "https://remiza.com.pl/feed/",
    
    # Regionalne radary Google News (Stryków, Zgierz, Głowno, Łódź, A1/A2)
    "https://news.google.com/rss/search?q=stra%C5%BC+po%C5%BCarna+wypadek+Stry%C3%B3w+G%C5%82owno+Zgierz+%C5%81%C3%B3d%C5%BA&hl=pl&gl=PL&ceid=PL:pl",
    "https://news.google.com/rss/search?q=po%C5%BCar+wypadek+autostrada+A1+A2+%C5%82%C3%B3dzkie&hl=pl&gl=PL&ceid=PL:pl",
    
    # Ogólnopolskie wypadki i pożary
    "https://news.google.com/rss/search?q=wypadek+po%C5%BCar+stra%C5%BC+po%C5%BCarna+polska&hl=pl&gl=PL&ceid=PL:pl"
]

def run_worker():
    print("Rozpoczynam pobieranie wiadomości z wielu źródeł (Kraj + Okolice)...")
    
    for feed_url in RSS_FEEDS:
        print(f"Skanuję źródło: {feed_url}")
        parsed_feed = feedparser.parse(feed_url)
        
        for entry in parsed_feed.entries[:5]: # Pobieramy 5 najnowszych z każdego źródła
            external_id = entry.get('id') or entry.get('link')
            title = entry.get('title', '')
            summary = entry.get('summary', '') or entry.get('description', '')
            link = entry.get('link', '')

            if not external_id or not title:
                continue

            # Sprawdzamy w bazie Supabase, czy ten artykuł był już przetwarzany
            existing = supabase.table('incidents').select('id').eq('external_id', external_id).execute()
            if existing.data and len(existing.data) > 0:
                continue # Już go znamy, pomijamy

            print(f"Analizuję przez AI nowy wpis: {title}")

            # Instrukcja dla Gemini AI
            prompt = f"""
            Przeanalizuj poniższy artykuł dotyczący zdarzenia ratowniczego w Polsce.
            Tytuł: {title}
            Treść: {summary}

            Zwróć wynik WYŁĄCZNIE jako czysty obiekt JSON (bez znaczników markdown typu ```json), zawierający dokładnie te pola:
            - "is_incident": boolean (true, jeśli to konkretne zdarzenie ratownicze: wypadek, pożar, kolizja, plama oleju, powalone drzewo itp.; false jeśli to ogólny artykuł, felieton, statystyka lub wspomnienie).
            - "title": string (Krótki, profesjonalny tytuł meldunku po polsku, np. "Wypadek osobówki na A2", "Pożar budynku gospodarczego").
            - "category": string (dokładnie jedno z: "fire", "accident", "hazard").
            - "status": string (dokładnie jedno z: "Trwa akcja", "Opanowane", "Zakończone").
            - "location": string (Dokładna miejscowość i ulica lub numer drogi, np. "Stryków, ul. Warszawska" lub "Autostrada A1 km 300").
            - "geocode_query": string (Najlepsze zapytanie do wyszukiwarki map, np. "Stryków, łódzkie" lub "Zgierz, polska").
            - "region": string (Podaj region, np. "powiat zgierski", "łódzkie", "Warszawa", "pomorskie" itp.).
            - "units": string (Wymień Siły i Środki po przecinku, np. "JRG Stryków, OSP Głowno, ZRM, Policja" - jeśli brak w tekście, oszacuj na podstawie typu zdarzenia).
            - "description": string (Zwięzły meldunek operacyjny 2-3 zdania: co się stało, jakie są utrudnienia).
            - "estimated_lat": number (Przybliżona szerokość geograficzna GPS dla tej lokalizacji w Polsce).
            - "estimated_lng": number (Przybliżona długość geograficzna GPS dla tej lokalizacji w Polsce).
            """

            try:
                response = ai_client.models.generate_content(
                    model="gemini-3.8-flash",
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        temperature=0.1
                    )
                )

                result_text = response.text.strip()
                # Usuwamy znaczniki markdown, gdyby model je dodał
                if result_text.startswith("```"):
                    result_text = result_text.split("```")[1]
                    if result_text.startswith("json"):
                        result_text = result_text[4:]
                
                data = json.loads(result_text.strip())

                if not data.get("is_incident", False):
                    print("-> Pominięto (artykuł ogólny / niebędący akcją).")
                    continue

                # Geokodowanie przez Nominatim (OpenStreetMap) dla idealnych współrzędnych
                lat = data.get("estimated_lat", 51.90)
                lng = data.get("estimated_lng", 19.50)
                geo_q = data.get("geocode_query", "Polska")

                import urllib.request
                import urllib.parse
                
                geo_url = f"[https://nominatim.openstreetmap.org/search?q=](https://nominatim.openstreetmap.org/search?q=){urllib.parse.quote(geo_q + ', Polska')}&format=json&limit=1"
                req = urllib.request.Request(geo_url, headers={'User-Agent': 'StrazAlertBot/1.0'})
                try:
                    with urllib.request.urlopen(req) as geo_res:
                        geo_data = json.loads(geo_res.read().decode())
                        if geo_data and len(geo_data) > 0:
                            lat = float(geo_data[0]['lat'])
                            lng = float(geo_data[0]['lon'])
                except Exception as e:
                    print(f"Błąd geokodowania, używam domyślnych z AI: {e}")

                # Pobieramy dzisiejszą datę w Polsce
                from datetime import datetime
                now_pl = datetime.now()
                date_str = now_pl.strftime("%Y-%m-%d")
                time_str = now_pl.strftime("%H:%M")

                # Zapisujemy do bazy Supabase
                insert_data = {
                    "external_id": external_id,
                    "title": data.get("title", title),
                    "category": data.get("category", "hazard"),
                    "status": data.get("status", "Trwa akcja"),
                    "location": data.get("location", "Nieznana lokalizacja"),
                    "region": data.get("region", "Polska"),
                    "lat": lat,
                    "lng": lng,
                    "date_str": date_str,
                    "time_str": time_str,
                    "units": data.get("units", "PSP, OSP"),
                    "description": data.get("description", summary[:200]),
                    "source_url": link
                }

                supabase.table('incidents').insert(insert_data).execute()
                print(f"Sukces! Zapisano zdarzenie: {data.get('title')} ({data.get('region')})")

            except Exception as e:
                print(f"Błąd podczas analizy Gemini lub zapisu: {e}")

if __name__ == "__main__":
    run_worker()
