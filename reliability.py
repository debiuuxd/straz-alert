"""Pure rules shared by ingestion and offline regression tests. No network on import."""
import html
import json
import math
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo("Europe/Warsaw")
AREA = json.loads(Path(__file__).with_name("local-alert-area.json").read_text())


def normalize(value):
    value = str(value or "").lower().replace("ł", "l")
    return " ".join("".join(c for c in unicodedata.normalize("NFKD", value)
                           if not unicodedata.combining(c)).split())


def plain_text(value):
    return html.unescape(re.sub(r"<[^>]*>", " ", str(value or ""))).strip()


def coordinates(lat, lng):
    try:
        if lat is None or lng is None or isinstance(lat, bool) or isinstance(lng, bool):
            return None
        lat, lng = float(lat), float(lng)
        if math.isfinite(lat) and math.isfinite(lng) and 49 <= lat <= 55.1 and 14 <= lng <= 24.2:
            return lat, lng
    except (TypeError, ValueError):
        pass
    return None


def is_local_incident(title, location, region, lat, lng):
    point = coordinates(lat, lng)
    if not point:
        return False
    lat, lng = point
    text = normalize(f"{title} {location}")
    loc = normalize(location)
    road = bool(re.search(r"\b(?:a[12]|dk\s?14)\b|lodz polnoc", text))
    junction = road and "lodz polnoc" in loc
    if not junction and any(re.match(r"^" + re.escape(normalize(city)) + r"(?:\b|,)", loc)
                            for city in AREA["excluded_cities"]):
        return False
    # Keep the original small coordinate circles; never accept an unknown point.
    nearby = lat >= 51.85 and lng >= 19.53 and any(
        math.hypot(lat - c[0], lng - c[1]) < 0.09 for c in AREA["centres"])
    # Names repeat elsewhere in Poland. Names alone must not admit distant events.
    south, west, north, east = AREA["name_check_bounds"]
    in_region = south <= lat <= north and west <= lng <= east
    named = any(re.search(r"\b" + re.escape(normalize(t)) + r"\b", text)
                for t in AREA["towns"])
    return nearby or (in_region and named)


def parse_timestamp(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = parsedate_to_datetime(str(value))
        except (TypeError, ValueError, OverflowError):
            return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def evidence_present(evidence, source_text):
    return bool(evidence and len(normalize(evidence)) >= 3
                and normalize(evidence) in normalize(source_text))


def confirmed_units(data, source_text):
    evidence = data.get('units_evidence')
    if not evidence_present(evidence, source_text):
        return 'Brak danych'
    requested, fragment = normalize(data.get('units')), normalize(evidence)
    patterns = {
        'PSP': r'\bpsp\b|\bjrg\b|panstwow\w* straz',
        'OSP': r'\bosp\b|ochotnicz\w* straz',
        'Policja': r'policj',
        'ZRM': r'\bzrm\b|pogotowi|zespol\w* ratownictwa medycznego',
        'LPR': r'\blpr\b|lotnicz\w* pogotowi',
        'Straż pożarna': r'straz|strazak',
    }
    found = [label for label, pattern in patterns.items()
             if re.search(pattern, requested) and re.search(pattern, fragment)]
    if 'PSP' in found or 'OSP' in found:
        found = [label for label in found if label != 'Straż pożarna']
    return ', '.join(found) if found else 'Brak danych'


def event_fields(data, source_text, published_at, now):
    """Unknown is preferable to silently treating publication/detection as event time."""
    event_date = None
    event_time = None
    evidence = data.get("date_evidence")
    if evidence_present(evidence, source_text):
        ev = normalize(evidence)
        if "dzis" in ev and published_at:
            event_date = published_at.astimezone(WARSAW).date()
        elif "wczoraj" in ev and published_at:
            event_date = published_at.astimezone(WARSAW).date() - timedelta(days=1)
        else:
            value = str(data.get("event_date") or "")
            try:
                candidate = datetime.strptime(value, "%Y-%m-%d").date()
                # Require an explicit numeric date in the cited source fragment.
                forms = [candidate.isoformat(), candidate.strftime("%d.%m.%Y")]
                if not re.search(r'\b(?:19|20)\d{2}\b', str(evidence)):
                    forms.append(candidate.strftime("%d.%m"))
                if any(form in str(evidence) for form in forms):
                    event_date = candidate
            except ValueError:
                pass
    if event_date and not now.astimezone(WARSAW).date() - timedelta(days=7) <= event_date <= now.astimezone(WARSAW).date():
        event_date = None
    value = str(data.get("event_time") or "")
    if event_date and re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
        if evidence_present(data.get("time_evidence"), source_text) and value in data["time_evidence"]:
            event_time = value
    return {"event_date": event_date.isoformat() if event_date else None,
            "event_time": event_time}


def find_duplicate(new, recent):
    """Conservative: proximity/two common words are never sufficient to merge."""
    links = {s.get("url") for s in new.get("sources", []) if s.get("url")}
    for old in recent:
        if old.get("external_id") == new.get("external_id"):
            return old
        old_links = {s.get("url") for s in old.get("sources", []) if s.get("url")}
        if old.get("source_url"):
            old_links.add(old["source_url"])
        if links & old_links:
            return old
        # Cross-article merges need matching explicit date AND time and strong text.
        if not new.get("event_date") or not new.get("event_time"):
            continue
        if (new["event_date"], new["event_time"], new["category"]) != (
                old.get("event_date"), old.get("event_time"), old.get("category")):
            continue
        if normalize(new["location"]) != normalize(old.get("location")):
            continue
        words = set(normalize(new["title"]).split())
        other = set(normalize(old.get("title")).split())
        if len(words & other) >= 4 and len(words & other) / max(len(words | other), 1) >= .8:
            return old
    return None


def merge_incident(old, new):
    sources = {s["url"]: s for s in old.get("sources", []) if s.get("url")}
    if old.get("source_url") and old["source_url"] not in sources:
        sources[old["source_url"]] = {"url": old["source_url"], "title": old.get("title", "Źródło")}
    sources.update({s["url"]: s for s in new.get("sources", []) if s.get("url")})
    patch = {"sources": list(sources.values()), "updated_at": new["updated_at"]}
    for field in ("event_date", "event_time"):
        if not old.get(field) and new.get(field):
            patch[field] = new[field]
    # A report of an action in progress must never reopen a completed incident.
    observed = parse_timestamp(new.get("status_observed_at"))
    previous = parse_timestamp(old.get("status_observed_at"))
    if new.get("status") != "Nieustalony" and observed and (not previous or observed >= previous):
        if old.get("status") != "Zakończone" or new["status"] == "Zakończone":
            patch.update(status=new["status"], status_observed_at=new["status_observed_at"])
    if old.get("units") in (None, "", "Brak danych") and new.get("units") not in (None, "", "Brak danych"):
        patch["units"] = new["units"]
    if not coordinates(old.get("lat"), old.get("lng")) and coordinates(new.get("lat"), new.get("lng")):
        for field in ("lat", "lng", "location_accuracy", "local_alert"):
            patch[field] = new[field]
    return patch


def notification_eligible(incident, now, source_ready):
    if not source_ready or not coordinates(incident.get("lat"), incident.get("lng")):
        return False
    if incident.get("status") == "Zakończone" or "alarm falszywy" in normalize(incident.get("title")):
        return False
    published = parse_timestamp(incident.get("published_at"))
    # Publication freshness is a conservative notification gate, never an event date.
    return bool(published and timedelta(minutes=-5) <= now - published <= timedelta(hours=2)
                and (not incident.get("event_date") or incident["event_date"] == now.astimezone(WARSAW).date()))
