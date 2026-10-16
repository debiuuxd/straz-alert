import os
import json
import hashlib
import feedparser
import requests
from datetime import datetime
from google import genai
from google.genai import types
from supabase import create_client, Client

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
ai_client = genai.Client(api_key=GEMINI_API_KEY)

RSS_FEEDS = [
    "https://remiza.com.pl/feed/",
]

def get_coordinates(location_query: str):
    try:
        url = "https://nominatim.openstreetmap.org/search"
        params = {
            "q": f"{location_query}, Polska",
            "format": "json",
            "limit": 1,
            "countrycodes": "pl"
        }
        headers = {"User-Agent": "StrazAlertLiveApp/1.0"}
        res = requests.get(url, params=params, headers=headers, timeout=10)
        data = res.json()
        if data:
            return float(data[0]["lat"]), float(data[0]["lon"])
    except Exception as e:
        print(f"Błąd geokodowania dla {location_query}: {e}")
    return None, None

def analyze_with_gemini(title: str, content: str):
    prompt = f"""
    Przeanalizuj poniższy komunikat prasowy/strażacki i oceń, czy dotyczy konkretnego zdarzenia ratowniczego (pożar, wypadek, kolizja, zagrożenie miejscowe, wichura itp.) w Polsce.
    Jeśli to artykuł ogólny (np. podsumowanie roku, polityka, ćwiczenia, zbiórka krwi), ustaw is_incident na false.
    
    Tytuł: {title}
    Treść: {content}
    
    Zwróć wyłącznie czysty obiekt JSON zgodny ze schematem:
    {{
      "is_incident": boolean,
      "title": "Krótki, konkretny tytuł taktyczny (max 50 znaków)",
      "category": "fire" | "accident" | "hazard",
      "status": "Trwa akcja" | "Opanowane" | "Zakończone",
      "location": "Miejscowość i ulica/numer drogi",
      "geocode_query": "Najlepsze zapytanie dla mapy, np. 'Stryków, łódzkie'",
      "region": "Powiat lub województwo",
      "units": "Wyciągnięte lub oszacowane SiŚ po przecinku, np. JRG PSP, OSP, ZRM, Policja",
      "description": "Zwięzły meldunek operacyjny (2-3 zdania: co się stało, jakie są utrudnienia)",
      "estimated_lat": number,
      "estimated_lng": number
    }}
    """
    try:
        response = ai_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.1
            )
        )
        return json.loads(response.text)
    except Exception as e:
        print(f"Błąd Gemini API: {e}")
        return None

def process_feeds():
    for feed_url in RSS_FEEDS:
        feed = feedparser.parse(feed_url)
        for entry in feed.entries[:10]:
            link = entry.get("link", "")
            ext_id = hashlib.md5(link.encode("utf-8")).hexdigest()
            
            existing = supabase.table("incidents").select("id").eq("external_id", ext_id).execute()
            if existing.data:
                continue
                
            raw_title = entry.get("title", "")
            raw_desc = entry.get("summary", "")
            
            print(f"Analizuję nowy wpis: {raw_title}")
            ai_data = analyze_with_gemini(raw_title, raw_desc)
            
            if not ai_data or not ai_data.get("is_incident"):
                print(" -> Pominięto (brak konkretnego zdarzenia ratowniczego).")
                continue
                
            lat, lng = get_coordinates(ai_data.get("geocode_query", ai_data["location"]))
            if not lat or not lng:
                lat = ai_data.get("estimated_lat", 52.0)
                lng = ai_data.get("estimated_lng", 19.5)
                
            now = datetime.now()
            record = {
                "external_id": ext_id,
                "title": ai_data["title"],
                "category": ai_data["category"],
                "status": ai_data.get("status", "Trwa akcja"),
                "location": ai_data["location"],
                "region": ai_data.get("region", "Polska"),
                "lat": lat,
                "lng": lng,
                "date_str": now.strftime("%Y-%m-%d"),
                "time_str": now.strftime("%H:%M"),
                "units": ai_data.get("units", "Zastępy PSP i OSP"),
                "description": ai_data["description"],
                "source_url": link
            }
            
            supabase.table("incidents").insert(record).execute()
            print(f" [+] Dodano nowe zdarzenie: {record['title']} ({record['location']})")

if __name__ == "__main__":
    process_feeds()
