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
    "https://remiza.com.pl/feed/",
    "https://news.google.com/rss/search?q=(wypadek+OR+po%C5%BCar+OR+stra%C5%BC+OR+kolizja)+(Stryk%C3%B3w+OR+G%C5%82owno+OR+Zgierz+OR+%C5%81%C3%B3d%C5%BA+OR+zgierski)+when:1d&hl=pl&gl=PL&ceid=PL:pl",
    "https://news.google.com/rss/search?q=(wypadek+OR+po%C5%BCar+OR+zablokowana)+(A1+OR+A2+OR+S8+OR+DK14+OR+%C5%82%C3%B3dzkie)+when:1d&hl=pl&gl=PL&ceid=PL:pl",
    "https://news.google.com/rss/search?q=(stra%C5%BC+po%C5%BCarna+OR+JRG+OR+OSP)+(po%C5%BCar+OR+wypadek)+when:1d&hl=pl&gl=PL&ceid=PL:pl"
]

def run_worker():
    print("Rozpoczynam pobieranie wiadomości z wielu źródeł (Kraj + Okolice)...")
    
    existing_res = supabase.table('incidents').select('external_id').order('created_at', desc=True).limit(300).execute()
    existing_ids = set(r['external_id'] for r in (existing_res.data or []) if r.get('external_id'))

    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36'}

    for feed_url in RSS_FEEDS:
        print(f"\nSkanuję źródło: {feed_url[:70]}...")
        try:
            req = urllib.request.Request(feed_url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                xml_data = resp.read()
            parsed_feed = feedparser.parse(xml_data)
        except Exception as e:
            print(f"Błąd pobierania RSS: {e}")
            continue

        print(f"Znaleziono wpisów w kanale: {len(parsed_feed.entries)}")
        
        for entry in parsed_feed.entries[:5]:
            external_id = entry.get('id') or entry.get('link')
            title = entry.get('title', '')
            summary = entry.get('summary', '') or entry.get('description', '')
            link = entry.get('link', '')

            if not external_id or not title:
                continue

            if external_id in existing_ids:
                continue

            print(f"Analizuję przez AI nowy wpis: {title}")

            prompt = f"""
            Przeanalizuj poniższy artykuł lub nagłówek dotyczący zdarzenia ratowniczego w Polsce.
            Tytuł: {title}
            Treść: {summary}

            Zwróć wynik WYŁĄCZNIE jako czysty obiekt JSON, zawierający dokładnie te pola:
            - "is_incident": boolean (true, jeśli to konkretne zdarzenie: wypadek, pożar, kolizja, utrudnienia drogowe, akcja ratownicza; false jeśli to felieton, polityka, zakupy lub wspomnienie sprzed lat).
            - "title": string (Krótki tytuł meldunku po polsku, np. "Wypadek na autostradzie A2", "Pożar budynku mieszkalnego").
            - "category": string (dokładnie jedno z: "fire", "accident", "hazard").
            - "status": string (dokładnie jedno z: "Trwa akcja", "Opanowane", "Zakończone").
            - "location": string (Miejscowość i ulica lub numer drogi, np. "Stryków, DK14" lub "Łódź, ul. Aleksandrowska").
            - "geocode_query": string (Sama nazwa miejscowości i województwa do mapy, np. "Stryków, łódzkie" lub "Zgierz").
            - "region": string (Województwo lub powiat, np. "powiat zgierski", "łódzkie", "mazowieckie").
            - "units": string (Wymień Siły i Środki po przecinku, np. "PSP, OSP, ZRM, Policja").
            - "description": string (Krótki meldunek operacyjny 1-3 zdania po polsku).
            - "estimated_lat": number (Przybliżona szerokość geograficzna GPS miejsca w Polsce, np. 51.90).
            - "estimated_lng": number (Przybliżona długość geograficzna GPS miejsca w Polsce, np. 19.59).
            """

            try:
                # Używamy bezpośrednio najnowszego, stabilnego modelu Google GenAI
                response = ai_client.models.generate_content(
                    model='gemini-2.5-flash',
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        temperature=0.1
                    )
                )

                result_text = response.text.strip()
                if result_text.startswith("```"):
                    result_text = result_text.split("```")[1]
                    if result_text.startswith("json"):
                        result_text = result_text[4:]
                
                data = json.loads(result_text.strip())

                if not data.get("is_incident", False):
                    print("-> Pominięto (nie jest to bieżąca akcja ratownicza).")
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
                date_str = now_pl.strftime("%Y-%m-%d")
                time_str = now_pl.strftime("%H:%M")

                insert_data = {
                    "external_id": external_id,
                    "title": data.get("title", title)[:120],
                    "category": data.get("category", "hazard"),
                    "status": data.get("status", "Trwa akcja"),
                    "location": data.get("location", "Polska"),
                    "region": data.get("region", "Polska"),
                    "lat": lat,
                    "lng": lng,
                    "date_str": date_str,
                    "time_str": time_str,
                    "units": data.get("units", "PSP, OSP, Policja"),
                    "description": data.get("description", title),
                    "source_url": link
                }

                supabase.table('incidents').insert(insert_data).execute()
                existing_ids.add(external_id)
                print(f"✅ SUKCES! Dodano na mapę: {insert_data['title']} ({insert_data['location']})")

            except Exception as e:
                print(f"❌ Błąd analizy lub zapisu dla '{title[:30]}...': {e}")

if __name__ == "__main__":
    run_worker()
