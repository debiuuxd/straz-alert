"""Pure rules shared by ingestion and offline regression tests. No network on import."""
import html
import json
import math
import re
import unicodedata
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
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


def canonical_url(value):
    try:
        url = urlsplit(str(value or ''))
        if url.scheme not in ('http', 'https') or not url.netloc:
            return ''
        query = [(key, val) for key, val in parse_qsl(url.query, keep_blank_values=True)
                 if not key.lower().startswith('utm_') and key.lower() not in ('fbclid', 'gclid', 'oc')]
        return urlunsplit(('https', url.netloc.lower().removeprefix('www.'),
                           url.path.rstrip('/'), urlencode(sorted(query)), ''))
    except ValueError:
        return ''


def _event_kind(row):
    # Classify the wording, not the coarse AI category (shootings were classified
    # both as accident and hazard). Ambiguous wording cannot establish equivalence.
    text = normalize(row.get('title'))
    if re.search(r'strzel|strzal|postrzel|ostrzel', text):
        return 'shooting'
    if re.search(r'pozar|plon|splon', text):
        return 'fire'
    if re.search(r'wypad|zderz|koliz|dachow|potrac', text):
        return 'traffic'
    return ''


def _title_tokens(text):
    stop = {'w', 'we', 'na', 'pod', 'i', 'oraz', 'z', 'ze', 'do', 'o', 'po', 'przy', 'sa', 'jest'}
    result = set()
    for word in re.findall(r'[a-z0-9]+', normalize(text)):
        if word in stop:
            continue
        if re.match(r'strzel|strzal|postrzel|ostrzel', word):
            word = 'shooting'
        elif word in ('rynek', 'rynku'):
            word = 'rynek'
        result.add(word[:5] if len(word) > 5 else word)
    return result


def _location_tokens(value):
    return _title_tokens(re.sub(r'\b(?:ulica|ulicy|ul|aleja|alei|al|plac|pl)\b', ' ', normalize(value)))


def _same_report_window(new, old):
    # Detection/publication dates are only a matching hint, never an invented event date.
    day1 = str(new.get('event_date') or new.get('date_str') or '')
    day2 = str(old.get('event_date') or old.get('date_str') or '')
    if not day1 or day1 != day2:
        return False
    if new.get('event_time') and old.get('event_time'):
        return new['event_time'] == old['event_time']
    time1 = parse_timestamp(new.get('published_at') or new.get('created_at') or new.get('updated_at'))
    time2 = parse_timestamp(old.get('published_at') or old.get('created_at'))
    return bool(time1 and time2 and abs(time1-time2) <= timedelta(hours=2))


def _same_specific_location(new, old):
    points = [coordinates(row.get('lat'), row.get('lng')) for row in (new, old)]
    if all(points):
        # Names repeat nationwide. GPS can reject distant namesakes, but cannot
        # establish a match. Tolerate legacy geocoding several km off in one city.
        a, b = points
        distance_km = math.hypot((a[0]-b[0])*111, (a[1]-b[1])*70)
        if distance_km > 15:
            return False
    locations = [normalize(row.get('location')) for row in (new, old)]
    # A shared city centre coordinate or a shared city name is not enough.
    marker = r'\b(?:ulica|ulicy|ul|aleja|alei|al|rynek|rynku|plac|pl|rondo|ronda|osiedle|wezel|wezla|skrzyzowanie|skrzyzowaniu)\b|\b(?:a|s|dk|dw)\s?\d+\b'
    if not all(re.search(marker, location) for location in locations):
        return False
    sets = [_location_tokens(location) for location in locations]
    common = sets[0] & sets[1]
    if len(common) < 2 or len(common) / max(len(sets[0] | sets[1]), 1) < .8:
        return False
    # Different named streets, buildings or road numbers remain distinct.
    for pattern in (r'\b(?:a|s|dk|dw)\s?\d+\b', r'\b\d+[a-z]?\b'):
        anchors = [{re.sub(r'\s+', '', anchor) for anchor in re.findall(pattern, location)} for location in locations]
        if anchors[0] != anchors[1]:
            return False
    return True


def find_duplicate(new, recent):
    """Use source identity first, then strong place/text/time evidence; never GPS alone."""
    links = {canonical_url(s.get("url")) for s in new.get("sources", [])}
    links.add(canonical_url(new.get('source_url')))
    links.discard('')
    for old in recent:
        if old.get('merged_into'):
            continue
        if new.get('external_id') and old.get("external_id") == new["external_id"]:
            return old
        old_links = {canonical_url(s.get("url")) for s in old.get("sources", [])}
        old_links.add(canonical_url(old.get('source_url')))
        old_links.discard('')
        if links & old_links:
            return old
    # Identity is checked across ALL candidates before any similarity match.
    for old in recent:
        if old.get('merged_into'):
            continue
        if str(old.get('external_id', '')).startswith('manual-'):
            continue
        # Exact/reworded reports about a specific place can match without an event
        # hour. The same kind of event is required even if AI category labels differ.
        kind = _event_kind(new)
        if kind and kind == _event_kind(old) and _same_report_window(new, old) and _same_specific_location(new, old):
            words, other = _title_tokens(new.get('title')), _title_tokens(old.get('title'))
            common = words & other
            if len(common) >= 3 and len(common) / max(min(len(words), len(other)), 1) >= .75:
                return old
        # City-only reports require matching explicit date AND time and strong text.
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
    sources = {}
    for row in (old, new):
        entries = ([{"url": row['source_url'], "title": row.get('title', 'Źródło')}]
                   if row.get('source_url') else []) + row.get('sources', [])
        for source in entries:
            key = canonical_url(source.get('url'))
            if key:
                sources[key] = {**sources.get(key, {}), **source}
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
    if incident.get('merged_into') or not source_ready or not coordinates(incident.get("lat"), incident.get("lng")):
        return False
    if incident.get("status") == "Zakończone" or "alarm falszywy" in normalize(incident.get("title")):
        return False
    published = parse_timestamp(incident.get("published_at"))
    # Publication freshness is a conservative notification gate, never an event date.
    return bool(published and timedelta(minutes=-5) <= now - published <= timedelta(hours=2)
                and (not incident.get("event_date") or incident["event_date"] == now.astimezone(WARSAW).date()))
