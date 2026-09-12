import os
import json
import re
import hashlib
import time
import copy
import subprocess
import signal
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
import xml.etree.ElementTree as ET

import requests
from requests_oauthlib import OAuth1
from dotenv import load_dotenv

load_dotenv()

FPL_BOOTSTRAP_URL = "https://fantasy.premierleague.com/api/bootstrap-static/"
FPL_FIXTURES_URL = "https://fantasy.premierleague.com/api/fixtures/"
X_POST_URL = "https://api.x.com/2/tweets"
STATE_FILE = Path("state.json")

# Central press-conference source.
FFSCOUT_ROOT = "https://www.fantasyfootballscout.co.uk"
FFSCOUT_DISCOVERY_URLS = [
    f"{FFSCOUT_ROOT}/category/team-news",
    f"{FFSCOUT_ROOT}/articles",
    FFSCOUT_ROOT + "/",
]

# Official FPL predictor settings.
PREDICTION_THRESHOLD = float(os.getenv("PREDICTION_THRESHOLD", "85"))
PREDICTION_POST_HOUR = int(os.getenv("PREDICTION_POST_HOUR", "23"))
PREDICTION_POST_MINUTE = int(os.getenv("PREDICTION_POST_MINUTE", "30"))

# Fast official-price watch around 00:00 Europe/London.
FAST_PRICE_POLL_SECONDS = int(os.getenv("FAST_PRICE_POLL_SECONDS", "15"))
FAST_PRICE_START_MINUTES_BEFORE = int(os.getenv("FAST_PRICE_START_MINUTES_BEFORE", "5"))
FAST_PRICE_END_MINUTES_AFTER = int(os.getenv("FAST_PRICE_END_MINUTES_AFTER", "20"))

NEWS_LOGIC_VERSION = 8
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"
# Keep automated activity natural even when several match events arrive close
# together, while FPL remains polled every 15 seconds in the background.
X_POST_MIN_INTERVAL_SECONDS = max(0, int(os.getenv("X_POST_MIN_INTERVAL_SECONDS", "0")))
X_POST_LOCK = threading.Lock()
LAST_X_POST_AT = None

# Keep API use controlled.
MAX_NEWS_AI_CALLS_PER_RUN = int(os.getenv("MAX_NEWS_AI_CALLS_PER_RUN", "12"))
MAX_SCHEDULE_AI_CALLS_PER_RUN = int(os.getenv("MAX_SCHEDULE_AI_CALLS_PER_RUN", "8"))
MAX_NEWS_TWEETS_PER_RUN = int(os.getenv("MAX_NEWS_TWEETS_PER_RUN", "4"))
MAX_CONFERENCE_ARTICLE_AGE_HOURS = int(os.getenv("MAX_CONFERENCE_ARTICLE_AGE_HOURS", "18"))
CONFERENCE_SCHEDULE_TTL_HOURS = int(os.getenv("CONFERENCE_SCHEDULE_TTL_HOURS", "36"))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.8,*/*;q=0.7",
    "Accept-Language": "en-GB,en;q=0.9",
    "Cache-Control": "no-cache",
}

# Fast discovery + official competition/club pages.
NEWS_SOURCES = [
    {"name": "AFC Bournemouth", "url": "https://www.afcb.co.uk/news/", "type": "html", "official": True, "path_hints": ["/news/"]},
    {"name": "Arsenal", "url": "https://www.arsenal.com/news", "type": "html", "official": True, "path_hints": ["/news/"]},
    {"name": "Aston Villa", "url": "https://www.avfc.co.uk/news/", "type": "html", "official": True, "path_hints": ["/news/"]},
    {"name": "Brentford", "url": "https://www.brentfordfc.com/en/news", "type": "html", "official": True, "path_hints": ["/en/news/"]},
    {"name": "Brighton Press Conferences", "url": "https://www.brightonandhovealbion.com/media-video", "type": "html", "official": True, "club_name": "Brighton", "path_hints": ["/media-video/"]},
    {"name": "Brighton", "url": "https://www.brightonandhovealbion.com/media-article/news", "type": "html", "official": True, "club_name": "Brighton", "path_hints": ["/media-article/"]},
    {"name": "Chelsea", "url": "https://www.chelseafc.com/en/news", "type": "html", "official": True, "path_hints": ["/en/news/article/"]},
    {"name": "Coventry City", "url": "https://www.ccfc.co.uk/news/", "type": "html", "official": True, "path_hints": ["/news/"]},
    {"name": "Crystal Palace", "url": "https://www.cpfc.co.uk/news/", "type": "html", "official": True, "path_hints": ["/news/"]},
    {"name": "Everton", "url": "https://www.evertonfc.com/news", "type": "html", "official": True, "path_hints": ["/news/"]},
    {"name": "Fulham", "url": "https://www.fulhamfc.com/news/", "type": "html", "official": True, "path_hints": ["/news/"]},
    {"name": "Hull City", "url": "https://www.wearehullcity.co.uk/news/", "type": "html", "official": True, "path_hints": ["/news/"]},
    {"name": "Ipswich Town", "url": "https://www.itfc.co.uk/news/", "type": "html", "official": True, "path_hints": ["/news/"]},
    {"name": "Leeds United", "url": "https://www.leedsunited.com/en/news", "type": "html", "official": True, "path_hints": ["/en/news/"]},
    {"name": "Liverpool", "url": "https://www.liverpoolfc.com/news", "type": "html", "official": True, "path_hints": ["/news/"]},
    {"name": "Manchester City", "url": "https://www.mancity.com/news", "type": "html", "official": True, "path_hints": ["/news/"]},
    {"name": "Manchester United Press Conference", "url": "https://www.manutd.com/en/news/category/press-conference?page=0", "type": "html", "official": True, "club_name": "Manchester United", "path_hints": ["/en/news/"]},
    {"name": "Manchester United", "url": "https://www.manutd.com/en/news/listing/mens-news?page=1&type=news", "type": "html", "official": True, "club_name": "Manchester United", "path_hints": ["/en/news/"]},
    {"name": "Newcastle United", "url": "https://www.newcastleunited.com/en/news", "type": "html", "official": True, "path_hints": ["/en/news/"]},
    {"name": "Nottingham Forest", "url": "https://www.nottinghamforest.co.uk/news", "type": "html", "official": True, "path_hints": ["/news/"]},
    {"name": "Sunderland", "url": "https://www.safc.com/news", "type": "html", "official": True, "path_hints": ["/news/"]},
    {"name": "Tottenham Hotspur", "url": "https://www.tottenhamhotspur.com/news", "type": "html", "official": True, "path_hints": ["/news/"]},
]

TEAM_AR = {
    "Bournemouth": "بورنموث",
    "Arsenal": "أرسنال",
    "Aston Villa": "أستون فيلا",
    "Brentford": "برينتفورد",
    "Brighton": "برايتون",
    "Chelsea": "تشيلسي",
    "Coventry": "كوفنتري",
    "Crystal Palace": "كريستال بالاس",
    "Everton": "إيفرتون",
    "Fulham": "فولهام",
    "Hull": "هال سيتي",
    "Ipswich": "إيبسويتش",
    "Leeds": "ليدز يونايتد",
    "Liverpool": "ليفربول",
    "Man City": "مانشستر سيتي",
    "Manchester City": "مانشستر سيتي",
    "Man Utd": "مانشستر يونايتد",
    "Manchester United": "مانشستر يونايتد",
    "Newcastle": "نيوكاسل",
    "Nott'm Forest": "نوتنغهام فورست",
    "Nottingham Forest": "نوتنغهام فورست",
    "Sunderland": "سندرلاند",
    "Spurs": "توتنهام",
    "Tottenham": "توتنهام",
}

SOURCE_TO_FPL_TEAM = {
    "AFC Bournemouth": "Bournemouth",
    "Coventry City": "Coventry",
    "Hull City": "Hull",
    "Ipswich Town": "Ipswich",
    "Leeds United": "Leeds",
    "Manchester City": "Man City",
    "Manchester United": "Man Utd",
    "Newcastle United": "Newcastle",
    "Nottingham Forest": "Nott'm Forest",
    "Tottenham Hotspur": "Spurs",
}

FPL_NEWS_KEYWORDS = [
    "injury", "injured", "fitness", "fit", "available", "unavailable",
    "doubt", "doubtful", "ruled out", "training", "trained", "return",
    "returns", "recover", "recovery", "illness", "sick", "knock",
    "hamstring", "ankle", "knee", "muscle",
    "rotation", "rest", "rested", "suspended", "suspension",
    "ban", "team news", "press conference", "press-conference",
    "update", "assessment", "assess", "came off", "substituted",
    "withdrawn", "availability",
    "squad", "medical", "scan",
]

ALLOWED_STATUS = {
    "AVAILABLE", "OUT", "DOUBT", "ASSESSMENT", "TRAINING",
    "INJURY", "SUSPENDED", "ROTATION"
}


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RuntimeError('state.json is unreadable; stopping to prevent duplicate posts') from exc
    return {
        "alerted_rise": {},
        "alerted_fall": {},
        "last_prices": {},
        "deadline_alerts": {},
        "news_seen": {},
        "recent_news_updates": {},
        "prediction_posts": {},
        "news_logic_version": NEWS_LOGIC_VERSION,
        "conference_schedule": {},
        "ffscout_sections": {},
    }


def save_state(state):
    temporary = STATE_FILE.with_suffix('.json.tmp')
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    temporary.replace(STATE_FILE)


def fetch_json(url):
    r = requests.get(url, timeout=25, headers=HEADERS)
    r.raise_for_status()
    return r.json()


def fetch_text(url):
    r = requests.get(url, timeout=25, headers=HEADERS)
    r.raise_for_status()
    return r.text


def fetch_official_fpl(fresh=False):
    url = FPL_BOOTSTRAP_URL
    if fresh:
        url = f"{FPL_BOOTSTRAP_URL}?_={int(time.time())}"
    return fetch_json(url)


def official_price_predictions(bootstrap):
    """Use the official site's status, not a locally chosen percent threshold.

    Verified against the supplied official frontend snapshot (2026-09-12):
    projections[0].likelihood 5/4 => rise; -4/-5 => drop; others => white.
    Locked/calibrating players are displayed neutral and must not alert.
    """
    teams = {int(t.get("id", 0)): str(t.get("name") or t.get("short_name") or "").strip()
             for t in bootstrap.get("teams", [])}
    rows = []
    for p in bootstrap.get("elements", []):
        if p.get('price_change_locked_until') or p.get('price_change_calibrating'):
            continue
        projections = p.get('price_change_projections') or []
        likelihood = projections[0].get('likelihood') if projections else None
        if likelihood not in (4, 5, -4, -5):
            continue
        try:
            pct = float(projections[0].get('projected_percent') or 0)
        except (TypeError, ValueError):
            pct = 0
        rows.append({
            "id": str(p.get("id")),
            "name": str(p.get("web_name") or p.get("second_name") or "Player"),
            "prediction": pct,
            "likelihood": likelihood,
            "ownership": float(p.get("selected_by_percent") or 0),
            "team": teams.get(int(p.get("team") or 0), ""),
        })
    return rows

def _prediction_row(p):
    pct = abs(float(p["prediction"]))
    return f"{p['name']} — {pct:.0f}%"


def format_daily_prediction_post(risers, fallers):
    """One concise prediction tweet per day, never a stream of alerts."""
    risers = list(risers)
    fallers = list(fallers)
    lines = ["🚨 توقعات تغيّر الأسعار"]

    if risers:
        lines += ["", "⬆️ احتمال ارتفاع:"]
        lines += [_prediction_row(p) for p in risers]
    if fallers:
        lines += ["", "⬇️ احتمال انخفاض:"]
        lines += [_prediction_row(p) for p in fallers]

    lines += ["", "#FPL", "#فانتزي_البريميرليغ"]
    return "\n".join(lines)


def fit_single_prediction_post(risers, fallers):
    """Fit both directions into ONE 280-char post.

    Keep the highest official percentages first. If the X character limit forces
    omissions, show a compact +N note instead of silently hiding them.
    """
    risers = list(risers)
    fallers = list(fallers)
    selected_r, selected_f = [], []

    # Guarantee representation from both directions when both exist.
    if risers:
        selected_r.append(risers[0])
    if fallers:
        selected_f.append(fallers[0])

    rest = []
    for p in risers[1:]:
        rest.append((abs(float(p["prediction"])), "rise", p))
    for p in fallers[1:]:
        rest.append((abs(float(p["prediction"])), "fall", p))
    rest.sort(key=lambda x: x[0], reverse=True)

    rejected = []
    for _, direction, player in rest:
        nr = selected_r + ([player] if direction == "rise" else [])
        nf = selected_f + ([player] if direction == "fall" else [])
        if len(format_daily_prediction_post(nr, nf)) <= 280:
            selected_r, selected_f = nr, nf
        else:
            rejected.append((direction, player))

    if not rejected:
        return format_daily_prediction_post(selected_r, selected_f)

    # Make room for a transparent omission count if needed. Remove the lowest
    # priority included rows, while retaining at least one row from each side.
    total_omitted = len(rejected)
    while True:
        post = format_daily_prediction_post(selected_r, selected_f)
        hashtags = "\n\n#FPL\n#فانتزي_البريميرليغ"
        note = f"\n\n+{total_omitted} لاعبين آخرين فوق {PREDICTION_THRESHOLD:.0f}%"
        base = post[:-len(hashtags)] if post.endswith(hashtags) else post
        candidate = base + note + hashtags
        if len(candidate) <= 280:
            return candidate

        removable = []
        if len(selected_r) > 1:
            removable.append((abs(float(selected_r[-1]["prediction"])), "rise"))
        if len(selected_f) > 1:
            removable.append((abs(float(selected_f[-1]["prediction"])), "fall"))
        if not removable:
            return post[:280]
        _, side = min(removable, key=lambda x: x[0])
        if side == "rise":
            selected_r.pop()
        else:
            selected_f.pop()
        total_omitted += 1


def current_official_prices(bootstrap):
    return {
        str(p["id"]): {
            "name": p["web_name"],
            "price": p["now_cost"] / 10.0
        }
        for p in bootstrap.get("elements", [])
    }


def find_confirmed_changes(old_prices, new_prices):
    rises, falls = [], []

    for pid, current in new_prices.items():
        if pid not in old_prices:
            continue

        old_price = float(old_prices[pid]["price"])
        new_price = float(current["price"])

        if new_price > old_price:
            rises.append((current["name"], old_price, new_price))
        elif new_price < old_price:
            falls.append((current["name"], old_price, new_price))

    return rises, falls


def format_confirmed_post(rises, falls):
    lines = ["✅ تغييرات أسعار مؤكدة", ""]
    for name, old, new in rises:
        lines.append(f"⬆️ {name}: £{old:.1f}m → £{new:.1f}m")
    for name, old, new in falls:
        lines.append(f"⬇️ {name}: £{old:.1f}m → £{new:.1f}m")
    lines += ["", "#FPL", "#فانتزي_البريميرليغ"]
    return "\n".join(lines)


def confirmed_price_chunks(rises, falls):
    rows = [("rise", row) for row in rises] + [("fall", row) for row in falls]
    chunks = []
    current_rises, current_falls = [], []
    for kind, row in rows:
        next_rises = current_rises + ([row] if kind == "rise" else [])
        next_falls = current_falls + ([row] if kind == "fall" else [])
        if (current_rises or current_falls) and len(format_confirmed_post(next_rises, next_falls)) > 280:
            chunks.append((current_rises, current_falls))
            current_rises = [row] if kind == "rise" else []
            current_falls = [row] if kind == "fall" else []
        else:
            current_rises, current_falls = next_rises, next_falls
    if current_rises or current_falls:
        chunks.append((current_rises, current_falls))
    return chunks

def get_next_gameweek_deadline(bootstrap, now_utc=None):
    now_utc = now_utc or datetime.now(timezone.utc)
    for event in bootstrap.get("events", []):
        raw = event.get("deadline_time")
        if not raw:
            continue
        deadline = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if deadline >= now_utc:
            return {"id": event["id"], "deadline": deadline}
    return None


def format_deadline_one_hour_post(deadline_utc):
    from zoneinfo import ZoneInfo

    local_deadline = deadline_utc.astimezone(ZoneInfo("Asia/Riyadh"))
    hour = local_deadline.strftime("%I").lstrip("0") or "12"
    minute = local_deadline.strftime("%M")
    am_pm = "ص" if local_deadline.strftime("%p") == "AM" else "م"
    time_text = f"{hour}:{minute} {am_pm}"

    return "\n".join([
        "🚨 باقي ساعة على إغلاق الجولة!",
        "",
        f"🕒 موعد الإغلاق: {time_text} بتوقيت السعودية 🇸🇦",
        "",
        "تأكد من الكابتن والتشكيلة وترتيب البنش",
        "وش محتار فيه؟👀",
        "",
        "#FPL",
        "#فانتزي_البريميرليغ",
    ])


def format_deadline_closed_post():
    return "\n".join([
        "🔒 خلاااص.. حان الوقت!",
        "",
        "وش سويت بالجولة؟ من أكثر لاعب متخوف منه؟ 👀",
        "",
        "#FPL",
        "#فانتزي_البريميرليغ",
    ])


def maybe_post_deadline_alerts(bootstrap, state, now_utc=None):
    now_utc = now_utc or datetime.now(timezone.utc)
    deadline_state = state.setdefault("deadline_alerts", {})

    event = get_next_gameweek_deadline(bootstrap, now_utc)

    if event:
        gw_key = str(event["id"])
        flags = deadline_state.setdefault(gw_key, {})
        one_hour_at = event["deadline"] - timedelta(hours=1)
        seconds_after = (now_utc - one_hour_at).total_seconds()

        if 0 <= seconds_after < 15 * 60 and not flags.get("one_hour"):
            post_to_x(format_deadline_one_hour_post(event["deadline"]))
            flags["one_hour"] = True

    past_events = []
    for e in bootstrap.get("events", []):
        raw = e.get("deadline_time")
        if not raw:
            continue
        d = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if d <= now_utc:
            past_events.append((e, d))

    if past_events:
        e, d = max(past_events, key=lambda item: item[1])
        seconds_after = (now_utc - d).total_seconds()
        gw_key = str(e["id"])
        flags = deadline_state.setdefault(gw_key, {})

        if 0 <= seconds_after < 15 * 60 and not flags.get("closed"):
            post_to_x(format_deadline_closed_post())
            flags["closed"] = True


class LinkTextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self._href = None
        self._anchor_parts = []
        self.text_parts = []
        self.title_parts = []
        self._in_title = False
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ("script", "style", "noscript"):
            self._skip_depth += 1
        if tag == "a":
            self._href = attrs.get("href")
            self._anchor_parts = []
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript") and self._skip_depth:
            self._skip_depth -= 1
        if tag == "a" and self._href:
            label = " ".join(self._anchor_parts).strip()
            self.links.append((self._href, re.sub(r"\s+", " ", label)))
            self._href = None
            self._anchor_parts = []
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._skip_depth:
            return
        clean = re.sub(r"\s+", " ", data).strip()
        if not clean:
            return
        self.text_parts.append(clean)
        if self._href is not None:
            self._anchor_parts.append(clean)
        if self._in_title:
            self.title_parts.append(clean)


def parse_html(html):
    parser = LinkTextParser()
    parser.feed(html)
    title = " ".join(parser.title_parts).strip()
    text = " ".join(parser.text_parts)
    text = re.sub(r"\s+", " ", text).strip()
    return title, text, parser.links


def normalize_url(url):
    parsed = urlparse(url)
    clean = parsed._replace(fragment="").geturl()
    return clean.rstrip("/")


def discover_html_links(source):
    html = fetch_text(source["url"])
    _, _, links = parse_html(html)

    base_host = urlparse(source["url"]).netloc.lower().replace("www.", "")
    out = []
    seen = set()

    for href, label in links:
        if not href:
            continue
        full = normalize_url(urljoin(source["url"], href))
        parsed = urlparse(full)
        host = parsed.netloc.lower().replace("www.", "")

        # Keep same-site links. Premier League and UEFA may use language/subpaths
        # on the same official host.
        if host != base_host:
            continue

        path = parsed.path.lower()
        hints = source.get("path_hints") or []
        if hints and not any(h.lower() in path for h in hints):
            continue

        # Avoid listing/index pages themselves.
        if normalize_url(full) == normalize_url(source["url"]):
            continue

        if full in seen:
            continue
        seen.add(full)

        if len(label) < 8:
            label = ""
        out.append({"url": full, "title": label})

    return out[:40]


def discover_rss_links(source):
    xml_text = fetch_text(source["url"])
    root = ET.fromstring(xml_text)
    out = []

    for item in root.findall(".//item")[:40]:
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if link:
            out.append({"url": normalize_url(link), "title": title})

    return out


def _same_official_host(url, source_url):
    host = urlparse(url).netloc.lower().replace("www.", "")
    base = urlparse(source_url).netloc.lower().replace("www.", "")
    return host == base


def _looks_like_article_url(url, source):
    path = urlparse(url).path.lower()

    hints = [h.lower() for h in (source.get("path_hints") or [])]
    if hints and any(h in path for h in hints):
        return True

    generic = (
        "/news/", "/article/", "/articles/", "/story/", "/stories/",
        "/team-news/", "/press-conference/", "/pressconference/"
    )
    return any(x in path for x in generic)


def _sitemap_candidates(source):
    parsed = urlparse(source["url"])
    root = f"{parsed.scheme}://{parsed.netloc}"
    candidates = []

    # robots.txt is often the most reliable place to discover the real sitemap.
    try:
        robots = fetch_text(root + "/robots.txt")
        for line in robots.splitlines():
            if line.lower().startswith("sitemap:"):
                u = line.split(":", 1)[1].strip()
                if u:
                    candidates.append(u)
    except Exception:
        pass

    candidates += [
        root + "/sitemap.xml",
        root + "/sitemap_index.xml",
        root + "/sitemap-index.xml",
        root + "/news-sitemap.xml",
    ]

    result = []
    seen = set()
    for u in candidates:
        u = normalize_url(u)
        if u not in seen:
            seen.add(u)
            result.append(u)
    return result


def _read_sitemap(url, source, depth=0):
    if depth > 1:
        return []

    r = requests.get(url, timeout=25, headers=HEADERS)
    r.raise_for_status()
    raw = r.content

    # Some sitemap endpoints return gzip directly.
    if url.lower().endswith(".gz") or raw[:2] == b"\\x1f\\x8b":
        import gzip
        raw = gzip.decompress(raw)

    root = ET.fromstring(raw)
    tag = root.tag.lower()
    found = []

    if tag.endswith("sitemapindex"):
        children = []
        for node in root.iter():
            if node.tag.lower().endswith("sitemap"):
                loc = None
                lastmod = ""
                for child in node:
                    if child.tag.lower().endswith("loc"):
                        loc = (child.text or "").strip()
                    elif child.tag.lower().endswith("lastmod"):
                        lastmod = (child.text or "").strip()
                if loc:
                    children.append((lastmod, loc))

        # Prefer the most recently updated sitemap files.
        children.sort(reverse=True)
        for _, child_url in children[:12]:
            try:
                found.extend(_read_sitemap(child_url, source, depth + 1))
            except Exception:
                continue
        return found

    # urlset
    for node in root.iter():
        if not node.tag.lower().endswith("url"):
            continue

        loc = None
        lastmod = ""
        for child in node:
            if child.tag.lower().endswith("loc"):
                loc = (child.text or "").strip()
            elif child.tag.lower().endswith("lastmod"):
                lastmod = (child.text or "").strip()

        if not loc:
            continue
        loc = normalize_url(loc)

        if not _same_official_host(loc, source["url"]):
            continue
        if not _looks_like_article_url(loc, source):
            continue

        found.append({
            "url": loc,
            "title": "",
            "_lastmod": lastmod,
        })

    return found


def discover_sitemap_links(source):
    all_items = []

    for sitemap_url in _sitemap_candidates(source):
        try:
            all_items.extend(_read_sitemap(sitemap_url, source))
        except Exception:
            continue

    # Most recent first when lastmod is available.
    all_items.sort(key=lambda x: x.get("_lastmod", ""), reverse=True)

    out = []
    seen = set()
    for item in all_items:
        u = item["url"]
        if u in seen:
            continue
        seen.add(u)
        item.pop("_lastmod", None)
        out.append(item)
        if len(out) >= 50:
            break

    return out


def discover_source_links(source):
    if source["type"] == "rss":
        return discover_rss_links(source)

    html_items = []
    try:
        html_items = discover_html_links(source)
    except Exception as exc:
        print(f"Direct news page failed [{source['name']}]: {exc}")

    # Many club sites render links with JavaScript or block server requests.
    # Use the site's own robots/sitemaps as a no-cost official fallback.
    sitemap_items = []
    if len(html_items) < 3:
        try:
            sitemap_items = discover_sitemap_links(source)
        except Exception as exc:
            print(f"Sitemap fallback failed [{source['name']}]: {exc}")

    merged = []
    seen = set()
    for item in html_items + sitemap_items:
        if item["url"] in seen:
            continue
        seen.add(item["url"])
        merged.append(item)

    return merged[:50]


def build_player_index(bootstrap):
    teams = {
        int(t["id"]): t["name"]
        for t in bootstrap.get("teams", [])
    }

    players = []
    for p in bootstrap.get("elements", []):
        web_name = str(p.get("web_name") or "").strip()
        first = str(p.get("first_name") or "").strip()
        second = str(p.get("second_name") or "").strip()
        team_name = teams.get(int(p.get("team", 0)), "")

        aliases = {web_name, second, f"{first} {second}".strip()}
        aliases = {
            a for a in aliases
            if len(a) >= 4 and not a.isdigit()
        }

        players.append({
            "id": str(p.get("id")),
            "web_name": web_name,
            "team": team_name,
            "ownership": float(p.get("selected_by_percent") or 0),
            "aliases": aliases,
        })
    return players


def match_current_players(text, players):
    low = text.casefold()
    matched = []

    for p in players:
        for alias in p["aliases"]:
            pattern = r"(?<!\w)" + re.escape(alias.casefold()) + r"(?!\w)"
            if re.search(pattern, low):
                matched.append(p)
                break

    # Unique by web_name+team.
    unique = {}
    for p in matched:
        unique[(p["web_name"], p["team"])] = p
    return list(unique.values())[:25]


def looks_fpl_relevant(text):
    low = text.casefold()
    return any(k in low for k in FPL_NEWS_KEYWORDS)



class HeadingSectionParser(HTMLParser):
    """Extract H2/H3 sections while preserving quote/body text."""
    def __init__(self):
        super().__init__()
        self._skip_depth = 0
        self._heading_tag = None
        self._heading_parts = []
        self._current_heading = None
        self._current_parts = []
        self.sections = []
        self.title_parts = []
        self._in_title = False

    def _flush(self):
        if self._current_heading:
            text = re.sub(r"\s+", " ", " ".join(self._current_parts)).strip()
            self.sections.append((self._current_heading, text))
        self._current_heading = None
        self._current_parts = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in ("script", "style", "noscript"):
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "title":
            self._in_title = True
        if tag in ("h2", "h3"):
            self._flush()
            self._heading_tag = tag
            self._heading_parts = []

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in ("script", "style", "noscript"):
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if tag == "title":
            self._in_title = False
        if self._heading_tag == tag:
            heading = re.sub(r"\s+", " ", " ".join(self._heading_parts)).strip()
            self._current_heading = heading
            self._current_parts = []
            self._heading_tag = None
            self._heading_parts = []

    def handle_data(self, data):
        if self._skip_depth:
            return
        clean = re.sub(r"\s+", " ", data).strip()
        if not clean:
            return
        if self._in_title:
            self.title_parts.append(clean)
        if self._heading_tag:
            self._heading_parts.append(clean)
        elif self._current_heading:
            self._current_parts.append(clean)

    def close(self):
        super().close()
        self._flush()


def _ffscout_url_date(url):
    m = re.search(r"/(20\d{2})/(\d{2})/(\d{2})/", url)
    if not m:
        return None
    try:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=ZoneInfo("Europe/London")).date()
    except ValueError:
        return None


def _ffscout_is_live_team_news(label, url, gw_number=None):
    hay = f"{label} {url}".casefold().replace("’", "'")
    if "team news" not in hay:
        return False
    if not any(k in hay for k in ("live injury updates", "live updates", "press conference", "press conferences")):
        return False
    if gw_number and f"gameweek-{gw_number}" not in hay and f"gameweek {gw_number}" not in hay:
        # Some archive/category links omit the Gameweek from the visible label.
        if "/20" not in url:
            return False
    return urlparse(url).netloc.lower().replace("www.", "") == "fantasyfootballscout.co.uk"


def discover_ffscout_live_articles(gw_number=None, now_utc=None):
    """Find today's FFScout live press-conference/team-news article(s)."""
    now_utc = now_utc or datetime.now(timezone.utc)
    london_date = now_utc.astimezone(ZoneInfo("Europe/London")).date()
    out = []
    seen = set()

    for discovery_url in FFSCOUT_DISCOVERY_URLS:
        try:
            html = fetch_text(discovery_url)
            _, _, links = parse_html(html)
        except Exception as exc:
            print(f"FFScout discovery failed [{discovery_url}]: {exc}")
            continue

        for href, label in links:
            if not href:
                continue
            full = normalize_url(urljoin(discovery_url, href))
            if full in seen:
                continue
            if not _ffscout_is_live_team_news(label, full, gw_number):
                continue
            page_date = _ffscout_url_date(full)
            # Conference live pages are only useful on the same UK calendar day.
            if page_date != london_date:
                continue
            seen.add(full)
            out.append({"url": full, "title": label})

    return out[:4]


def _heading_key(text):
    text = str(text or "").casefold().replace("&", "and")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def resolve_ffscout_team_heading(heading, bootstrap):
    """Map an FFScout club section heading to the exact current FPL team name."""
    key = _heading_key(heading)
    if not key:
        return None

    aliases = {
        "afc bournemouth": "Bournemouth",
        "bournemouth": "Bournemouth",
        "arsenal": "Arsenal",
        "aston villa": "Aston Villa",
        "brentford": "Brentford",
        "brighton": "Brighton",
        "brighton and hove albion": "Brighton",
        "chelsea": "Chelsea",
        "coventry": "Coventry",
        "coventry city": "Coventry",
        "crystal palace": "Crystal Palace",
        "everton": "Everton",
        "fulham": "Fulham",
        "hull": "Hull",
        "hull city": "Hull",
        "ipswich": "Ipswich",
        "ipswich town": "Ipswich",
        "leeds": "Leeds",
        "leeds united": "Leeds",
        "liverpool": "Liverpool",
        "manchester city": "Man City",
        "man city": "Man City",
        "manchester united": "Man Utd",
        "man united": "Man Utd",
        "newcastle": "Newcastle",
        "newcastle united": "Newcastle",
        "nottingham forest": "Nott'm Forest",
        "nott m forest": "Nott'm Forest",
        "sunderland": "Sunderland",
        "tottenham": "Spurs",
        "tottenham hotspur": "Spurs",
        "spurs": "Spurs",
    }
    target = aliases.get(key)

    current = {str(t.get("name")) for t in bootstrap.get("teams", [])}
    if target in current:
        return target

    # Fallback to normalized exact matching against the current FPL team list.
    for team in current:
        if _heading_key(team) == key:
            return team
    return None


def parse_ffscout_team_sections(html, bootstrap):
    parser = HeadingSectionParser()
    parser.feed(html)
    parser.close()
    title = re.sub(r"\s+", " ", " ".join(parser.title_parts)).strip()
    teams = {}
    for heading, text in parser.sections:
        team = resolve_ffscout_team_heading(heading, bootstrap)
        if not team:
            continue
        clean = re.sub(r"\s+", " ", text).strip()
        if len(clean) < 30:
            continue
        teams[team] = clean[:18000]
    return title, teams


def ffscout_section_hash(text):
    normalized = re.sub(r"\s+", " ", str(text or "")).strip().casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def analyze_ffscout_section_with_openai(team_name, current_text, previous_text, roster):
    """Extract only NEW FPL availability news from a current FFScout presser section."""
    from openai import OpenAI
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    roster_text = ", ".join(sorted({p.get("web") for p in roster if p.get("web")}))

    prompt = f"""
You are the Arabic editor for an FPL account on X.

Source: Fantasy Football Scout live Premier League press-conference updates.
Verified current FPL club: {team_name}
Current FPL roster (web names only): {roster_text}

PREVIOUS version of this club section (may be empty):
{previous_text[:12000] if previous_text else '[NONE]'}

CURRENT version of this club section:
{current_text[:16000]}

Your job is ONLY to extract NEW material FPL availability information that appears in CURRENT and was not already present in PREVIOUS.

STRICT RULES:
1) Treat this as a pre-match press-conference update only if the CURRENT section clearly contains manager/head-coach quotes or clearly reports what the coach said in today's presser.
2) Include only: confirmed absence, injury/fitness status, doubt/assessment, return to training, confirmed availability/readiness, suspension, or explicit "no fresh injuries" information.
3) Do NOT include tactics, predicted line-ups, expected starts, rotation opinions, minutes, goals, assists, praise, transfers, general match comments or performance.
4) Preserve certainty exactly. "Could return" != available. "Back in training" != confirmed available. "Assess tomorrow" != out.
5) If CURRENT contains no NEW material availability information beyond PREVIOUS, return IGNORE.
6) Never invent or infer information.
7) player_names must contain the English FPL web_name for every player named in summary_ar. Use only supplied roster names. If you cannot map a named player exactly, IGNORE.
8) availability_evidence must be an exact short excerpt copied from CURRENT proving the new availability update, max 30 words.
9) summary_ar must be concise natural Arabic, <=180 characters, using ✅ ⚠️ ❌ where useful. Do not include source, URL, manager, Gameweek or opponent.

Return JSON only:
{{"kind":"PRE_MATCH_CONFERENCE|IGNORE","summary_ar":"...","player_names":["..."],"availability_evidence":"..."}}
"""
    response = client.responses.create(
        model="gpt-5.6-terra",
        input=prompt,
        reasoning={"effort": "low"},
        max_output_tokens=500,
        text={"verbosity": "low"},
    )
    data = parse_json_object(response.output_text)
    return data if isinstance(data, dict) else {"kind": "IGNORE", "summary_ar": ""}


def cleanup_ffscout_state(state, now_utc):
    root = state.setdefault("ffscout_sections", {})
    keep = {}
    cutoff = now_utc - timedelta(days=2)
    for url, teams in root.items():
        page_date = _ffscout_url_date(url)
        if not page_date:
            continue
        page_dt = datetime.combine(page_date, datetime.min.time(), tzinfo=ZoneInfo("Europe/London")).astimezone(timezone.utc)
        if page_dt < cutoff:
            continue
        if isinstance(teams, dict):
            keep[url] = teams
    state["ffscout_sections"] = keep


def source_team_name(source):
    raw = source.get("club_name") or source["name"].replace(" Press Conferences", "").replace(" Press Conference", "")
    return SOURCE_TO_FPL_TEAM.get(raw, raw)


def parse_iso_datetime(value):
    if not value:
        return None
    value = str(value).strip()
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("Europe/London"))
    return dt.astimezone(timezone.utc)


def extract_article_published_utc(html):
    """Extract a real publication timestamp from official article metadata.

    Fail closed: if the official page does not expose a publication time, the
    article is not eligible for automatic conference posting.
    """
    candidates = []
    patterns = [
        r'<meta[^>]+(?:property|name|itemprop)=["\'](?:article:published_time|datePublished|datepublished|publishdate|pubdate)["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name|itemprop)=["\'](?:article:published_time|datePublished|datepublished|publishdate|pubdate)["\']',
        r'"datePublished"\s*:\s*"([^"]+)"',
        r'"datepublished"\s*:\s*"([^"]+)"',
    ]
    for pattern in patterns:
        for raw in re.findall(pattern, html, flags=re.I):
            dt = parse_iso_datetime(raw)
            if dt:
                candidates.append(dt)
    if not candidates:
        return None
    return min(candidates)


def article_text(url):
    html = fetch_text(url)
    title, text, _ = parse_html(html)
    published_utc = extract_article_published_utc(html)
    return title, text[:24000], published_utc


def normalized_excerpt(text):
    return re.sub(r"\s+", " ", str(text or "")).strip().casefold()


def evidence_is_in_source(evidence, source_text, max_words=30):
    evidence = str(evidence or "").strip()
    if not evidence:
        return False
    if len(evidence.split()) > max_words:
        return False
    return normalized_excerpt(evidence) in normalized_excerpt(source_text)


def has_clock_token(text):
    text = str(text or "")
    return bool(re.search(r"\b(?:[01]?\d|2[0-3])(?::[0-5]\d)?\s*(?:am|pm|a\.m\.|p\.m\.)?\b", text, flags=re.I))


def article_is_fresh(published_utc, now_utc):
    if published_utc is None:
        return False
    age = now_utc - published_utc
    return timedelta(minutes=-5) <= age <= timedelta(hours=MAX_CONFERENCE_ARTICLE_AGE_HOURS)


def cleanup_conference_schedule(state, now_utc):
    schedules = state.setdefault("conference_schedule", {})
    cleaned = {}
    for team, rows in schedules.items():
        keep = []
        for row in rows if isinstance(rows, list) else []:
            start = parse_iso_datetime(row.get("start_utc"))
            if not start:
                continue
            if now_utc - timedelta(hours=CONFERENCE_SCHEDULE_TTL_HOURS) <= start <= now_utc + timedelta(days=3):
                keep.append(row)
        if keep:
            cleaned[team] = keep[-6:]
    state["conference_schedule"] = cleaned


def add_conference_schedule(state, team_name, start_utc, source_url, evidence, now_utc):
    rows = state.setdefault("conference_schedule", {}).setdefault(team_name, [])
    iso = start_utc.astimezone(timezone.utc).isoformat()
    key = (iso, normalize_url(source_url))
    if not any((r.get("start_utc"), r.get("source_url")) == key for r in rows):
        rows.append({
            "start_utc": iso,
            "source_url": normalize_url(source_url),
            "time_evidence": evidence,
            "captured_at": now_utc.isoformat(),
        })
    rows.sort(key=lambda r: r.get("start_utc", ""))
    del rows[:-6]


def latest_started_schedule(state, team_name, now_utc):
    rows = state.setdefault("conference_schedule", {}).get(team_name, [])
    candidates = []
    for row in rows:
        start = parse_iso_datetime(row.get("start_utc"))
        if not start:
            continue
        if timedelta(hours=-18) <= now_utc - start <= timedelta(hours=CONFERENCE_SCHEDULE_TTL_HOURS):
            candidates.append((start, row))
    if not candidates:
        return None
    return max(candidates, key=lambda x: x[0])[1]


def team_ids(bootstrap):
    return {str(t.get("name")): int(t.get("id")) for t in bootstrap.get("teams", [])}


def team_roster_names(bootstrap, team_name):
    ids = team_ids(bootstrap)
    tid = ids.get(team_name)
    if not tid:
        return []
    out = []
    for p in bootstrap.get("elements", []):
        if int(p.get("team", 0)) != tid:
            continue
        web = str(p.get("web_name") or "").strip()
        full = f"{str(p.get('first_name') or '').strip()} {str(p.get('second_name') or '').strip()}".strip()
        second = str(p.get("second_name") or "").strip()
        out.append({"web": web, "full": full, "second": second})
    return out


def validate_player_names(player_names, roster):
    if not player_names:
        return True
    allowed = set()
    for p in roster:
        for v in (p.get("web"), p.get("full"), p.get("second")):
            if v:
                allowed.add(normalized_excerpt(v))
    return all(normalized_excerpt(name) in allowed for name in player_names)


def latest_team_fixture_context(fixtures, bootstrap, team_name, now_utc):
    ids = team_ids(bootstrap)
    tid = ids.get(team_name)
    if not tid:
        return None
    relevant = []
    for f in fixtures or []:
        if int(f.get("team_h") or 0) != tid and int(f.get("team_a") or 0) != tid:
            continue
        kickoff = parse_iso_datetime(f.get("kickoff_time"))
        if kickoff:
            relevant.append((kickoff, f))
    if not relevant:
        return None
    past = [(k, f) for k, f in relevant if k <= now_utc]
    future = [(k, f) for k, f in relevant if k > now_utc]
    latest_past = max(past, default=(None, None), key=lambda x: x[0] or datetime.min.replace(tzinfo=timezone.utc))
    next_future = min(future, default=(None, None), key=lambda x: x[0] or datetime.max.replace(tzinfo=timezone.utc))
    return {"latest_past": latest_past, "next_future": next_future}


def post_match_time_gate(fixtures, bootstrap, team_name, published_utc, now_utc):
    ctx = latest_team_fixture_context(fixtures, bootstrap, team_name, now_utc)
    if not ctx or not ctx["latest_past"][0]:
        return False
    kickoff = ctx["latest_past"][0]
    # A post-match conference article should not predate roughly full time and
    # should arrive while the match is still current news.
    return kickoff + timedelta(minutes=95) <= published_utc <= kickoff + timedelta(hours=10)


def pre_match_time_gate(state, team_name, published_utc, now_utc):
    schedule = latest_started_schedule(state, team_name, now_utc)
    if not schedule:
        return False
    start = parse_iso_datetime(schedule.get("start_utc"))
    if not start:
        return False
    # Never attribute an article to a press conference if it was published
    # before the verified official start time.
    return published_utc >= start - timedelta(minutes=5) and now_utc >= start


def reject_empty_update(summary_ar):
    low = normalized_excerpt(summary_ar)
    bad = (
        "لا توجد معلومات", "دون معلومات", "لا جديد", "لا توجد تحديثات",
        "لم يقدم تحديث", "لا معلومات جديدة", "حتى الآن دون",
    )
    return any(x in low for x in bad)


def team_ar(team_name):
    return TEAM_AR.get(team_name, team_name)


def parse_json_object(raw):
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
    raw = re.sub(r"\s*```$", "", raw)
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("Model did not return JSON")
    return json.loads(raw[start:end + 1])


def current_gameweek_number(bootstrap):
    for event in bootstrap.get("events", []):
        if event.get("is_current"):
            return int(event["id"])
    for event in bootstrap.get("events", []):
        if event.get("is_next"):
            return int(event["id"])
    return None


def extract_schedule_with_openai(source, title, text, published_utc):
    """Extract an exact announced press-conference start time from an official club page.

    This is only for building a verified schedule gate. It never posts anything.
    """
    from openai import OpenAI
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    prompt = f"""
You are verifying an OFFICIAL Premier League club page.

Source: {source['name']}
Published UTC: {published_utc.isoformat() if published_utc else 'unknown'}
Title: {title}
Text: {text}

Return a schedule ONLY if this exact page explicitly gives BOTH the calendar date and clock time of a FIRST-TEAM MEN'S manager/head-coach press conference.
Do not infer the time from a fixture, publication time, usual routine, or another page.
Do not use player interviews, generic media, match reports, old/historical conferences, academy or women's content.
If the exact date or exact clock time is absent, return is_schedule=false.

If present:
- conference_datetime_iso must be the announced local UK time with the correct UTC offset for that date.
- time_evidence must be an exact short excerpt copied from the page that contains the date/time evidence, max 25 words.

Return JSON only:
{{"is_schedule":true|false,"conference_datetime_iso":"...","time_evidence":"..."}}
"""
    response = client.responses.create(
        model="gpt-5.6-terra",
        input=prompt,
        reasoning={"effort": "low"},
        max_output_tokens=350,
        text={"verbosity": "low"},
    )
    data = parse_json_object(response.output_text)
    return data if isinstance(data, dict) else {"is_schedule": False}


def analyze_news_with_openai(source, title, text, team_name, roster, pre_schedule, published_utc):
    """Classify only a conference that has already happened and contains real FPL availability news."""
    from openai import OpenAI
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    roster_text = ", ".join(sorted({p.get("web") for p in roster if p.get("web")}))
    schedule_text = pre_schedule.get("start_utc") if pre_schedule else "NONE"

    prompt = f"""
You are the Arabic editor for an FPL account on X.

Official club source: {source['name']}
Verified FPL club: {team_name}
Official article publication UTC: {published_utc.isoformat()}
Verified pre-match conference start UTC from an official club page: {schedule_text}
Current FPL roster (web names only): {roster_text}

Article title:
{title}

Article text:
{text}

Your job is NOT to decide dates, teams, Gameweeks, managers or fixtures. Those are controlled by code.
Your ONLY job is to identify whether THIS article is reporting a FIRST-TEAM MEN'S manager/head-coach press conference that has ALREADY HAPPENED, and whether it contains material FPL availability news.

STRICT RULES:
1) PRE_MATCH_CONFERENCE only if the article reports a press/media conference that already happened before a match. If the page merely says the conference WILL happen, is DUE to happen, invites users to WATCH LIVE later, or previews a future media event: IGNORE.
2) POST_MATCH_CONFERENCE only if the article reports a manager/head-coach press/media conference after a match that already finished.
3) IGNORE generic interviews, player interviews, match reports, team-news articles not explicitly sourced from the conference, training stories, quote roundups, transfers, academy/women's content, historical content, and anything not clearly the actual press conference output.
4) Include only FPL availability: confirmed absences, doubts/assessment, return to training, confirmed availability, suspension, injury/fitness update.
5) If the conference contains no material FPL availability update, IGNORE. "No new information" is IGNORE.
6) Preserve certainty exactly. Back in training != available. Will be assessed != out.
7) Do not include minutes, starts, substitutions, goals, assists, tactics, praise, predicted lineups or performance.
8) Never output a manager name, Gameweek, opponent, source or URL.
9) player_names must contain the English FPL web_name for every player mentioned in summary_ar. Use ONLY names from the supplied current roster. If you cannot map a player exactly, IGNORE.
10) conference_evidence must be an exact short excerpt from the article proving the manager/head coach actually spoke at the press conference, max 20 words.
11) availability_evidence must be an exact short excerpt proving the FPL availability update, max 25 words.
12) summary_ar: concise natural Arabic bullet lines using ✅ ⚠️ ❌ when useful, <=180 characters.

Return JSON only:
{{"kind":"PRE_MATCH_CONFERENCE|POST_MATCH_CONFERENCE|IGNORE","summary_ar":"...","player_names":["..."],"conference_evidence":"...","availability_evidence":"..."}}
"""
    response = client.responses.create(
        model="gpt-5.6-terra",
        input=prompt,
        reasoning={"effort": "low"},
        max_output_tokens=600,
        text={"verbosity": "low"},
    )
    data = parse_json_object(response.output_text)
    return data if isinstance(data, dict) else {"kind": "IGNORE", "summary_ar": ""}


def format_press_conference_post(team_name, conference_kind, summary_ar):
    label = "مؤتمر قبل المباراة" if conference_kind == "PRE_MATCH_CONFERENCE" else "مؤتمر بعد المباراة"
    return "\n".join([
        f"🚨⚽️ {label} | {team_ar(team_name)}",
        "",
        summary_ar.strip(),
        "",
        "#FPL",
        "#فانتزي_البريميرليغ",
    ])


def format_news_post(team_name, summary_ar):
    return "\n".join([
        f"🚨 تحديث | {team_ar(team_name)}",
        "",
        summary_ar.strip(),
        "",
        "#FPL",
        "#فانتزي_البريميرليغ",
    ])

SUSPENSION_REASON_AR = {
    "DIRECT_RED": "بطاقة حمراء مباشرة",
    "SECOND_YELLOW": "بطاقتان صفراوان في المباراة",
    "YELLOW_ACCUMULATION": "تراكم البطاقات الصفراء",
}


def format_suspension_post(team_name, player, reason):
    reason_ar = SUSPENSION_REASON_AR.get(reason)
    if not reason_ar:
        return None
    return "\n".join([
        f"🚨 إيقاف | {team_ar(team_name)}",
        "",
        f"{player} موقوف. ⛔️",
        f"السبب: {reason_ar}.",
        "",
        "#FPL",
        "#فانتزي_البريميرليغ",
    ])

def recent_update_key(player, status):
    return f"{player}|{status}"


def is_recent_duplicate(state, player, status, now_utc):
    recent = state.setdefault("recent_news_updates", {})
    key = recent_update_key(player, status)
    raw = recent.get(key)
    if not raw:
        return False

    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return now_utc - dt < timedelta(hours=2)
    except Exception:
        return False


def mark_recent_update(state, player, status, now_utc):
    state.setdefault("recent_news_updates", {})[
        recent_update_key(player, status)
    ] = now_utc.isoformat()


def cleanup_recent_updates(state, now_utc):
    recent = state.setdefault("recent_news_updates", {})
    keep = {}

    for key, raw in recent.items():
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if now_utc - dt < timedelta(days=3):
                keep[key] = raw
        except Exception:
            pass

    state["recent_news_updates"] = keep


def candidate_priority(source, item):
    title = (item.get("title") or "").casefold()
    if any(k in title for k in ("press conference", "press-conference", "team news", "fitness", "injury", "suspension")):
        return 0
    if any(k in title for k in ("match report", "report", "reaction", "full-time", "full time")):
        return 1
    if source.get("official") and "press conference" in source.get("name", "").casefold():
        return 0
    if source.get("official"):
        return 2
    return 3


def safe_post(text, label="post"):
    if len(text) > 280:
        print(f"Skipped {label}: {len(text)} characters (>280)")
        return False
    post_to_x(text)
    return True


def scan_news(bootstrap, fixtures, state):
    """Scan FFScout's same-day live presser article and post only verified NEW FPL availability updates.

    This is deliberately fail-closed: no same-day live article, no club section,
    no current-roster match, or no exact source evidence => no tweet.
    """
    if "OPENAI_API_KEY" not in os.environ:
        print("Conference scan skipped: OPENAI_API_KEY is missing")
        return

    now_utc = datetime.now(timezone.utc)
    cleanup_ffscout_state(state, now_utc)

    previous_logic_version = int(state.get("news_logic_version", 0) or 0)
    if previous_logic_version != NEWS_LOGIC_VERSION:
        # We intentionally do NOT baseline today's FFScout live sections here.
        # That lets the upgraded bot catch today's already-published pressers once.
        state["ffscout_sections"] = {}
        state["news_logic_version"] = NEWS_LOGIC_VERSION
        print("Conference logic upgraded: FFScout live source enabled")

    gw = current_gameweek_number(bootstrap)
    articles = discover_ffscout_live_articles(gw_number=gw, now_utc=now_utc)
    if not articles:
        print("FFScout conference scan: no same-day live team-news article found")
        return

    root_state = state.setdefault("ffscout_sections", {})
    ai_calls = 0
    tweets = 0

    for article in articles:
        if ai_calls >= MAX_NEWS_AI_CALLS_PER_RUN or tweets >= MAX_NEWS_TWEETS_PER_RUN:
            break
        url = article["url"]
        try:
            html = fetch_text(url)
            title, sections = parse_ffscout_team_sections(html, bootstrap)
        except Exception as exc:
            print(f"FFScout article fetch/parse failed [{url}]: {exc}")
            continue

        if not sections:
            print(f"FFScout live page has no club sections yet: {url}")
            continue

        article_state = root_state.setdefault(url, {})

        for team_name, current_text in sections.items():
            if ai_calls >= MAX_NEWS_AI_CALLS_PER_RUN or tweets >= MAX_NEWS_TWEETS_PER_RUN:
                break

            digest = ffscout_section_hash(current_text)
            previous = article_state.get(team_name) if isinstance(article_state.get(team_name), dict) else {}
            if previous.get("hash") == digest:
                continue

            previous_text = str(previous.get("text") or "")
            roster = team_roster_names(bootstrap, team_name)
            if not roster:
                print(f"FFScout section rejected: no current FPL roster mapping [{team_name}]")
                article_state[team_name] = {
                    "hash": digest,
                    "text": current_text[:18000],
                    "checked_at": now_utc.isoformat(),
                }
                continue

            try:
                analysis = analyze_ffscout_section_with_openai(
                    team_name=team_name,
                    current_text=current_text,
                    previous_text=previous_text,
                    roster=roster,
                )
                ai_calls += 1
            except Exception as exc:
                print(f"FFScout analysis failed [{team_name}] [{url}]: {exc}")
                continue

            kind = str(analysis.get("kind") or "IGNORE").strip().upper()
            summary_ar = str(analysis.get("summary_ar") or "").strip()
            player_names = analysis.get("player_names") or []
            evidence = str(analysis.get("availability_evidence") or "").strip()

            valid = kind == "PRE_MATCH_CONFERENCE" and bool(summary_ar)
            valid = valid and not reject_empty_update(summary_ar)
            valid = valid and validate_player_names(player_names, roster)
            valid = valid and evidence_is_in_source(evidence, current_text, max_words=30)

            if valid:
                post = format_press_conference_post(team_name, "PRE_MATCH_CONFERENCE", summary_ar)
                if safe_post(post, "FFScout conference"):
                    tweets += 1
            else:
                print(f"FFScout section ignored/rejected: {team_name}")

            # Mark this exact version as processed whether it produced a tweet or not.
            # If FFScout later adds genuinely new information, the section hash changes
            # and PREVIOUS is supplied to the model so old details are not repeated.
            article_state[team_name] = {
                "hash": digest,
                "text": current_text[:18000],
                "checked_at": now_utc.isoformat(),
                "source_title": title or article.get("title", ""),
            }

    print(
        f"FFScout conference scan complete: articles={len(articles)}, "
        f"AI calls={ai_calls}, tweets={tweets}"
    )


def _prediction_player_label(p, direction):
    """Format a qualifying player without exposing the predictor percentage.

    Low-owned players (<5%) include their club in parentheses for context.
    """
    arrow = "⬆️" if direction == "rise" else "⬇️"
    name = p["name"]
    ownership = float(p.get("ownership") or 0)
    team = str(p.get("team") or "").strip()
    if ownership < 5 and team:
        return f"{arrow} {name} ({team_ar(team)})"
    return f"{arrow} {name}"


def _prediction_direction_post(players, direction):
    """Build one grouped prediction post for ONE direction only."""
    if direction == "rise":
        lines = ["🚨 توقعات تغيّر الأسعار", "", "⚠️ احتمال ارتفاع:"]
    else:
        lines = ["🚨 توقعات تغيّر الأسعار", "", "⚠️ احتمال انخفاض:"]

    lines += [_prediction_player_label(p, direction) for p in players]
    lines += ["", "#FPL", "#فانتزي_البريميرليغ"]
    return "\n".join(lines)


def _prediction_direction_chunks(players, direction):
    """Fit all newly qualifying players for one direction into grouped X posts.

    Rise and fall alerts are never mixed. Normally one direction produces one
    post; an extra post is created only if X's 280-character limit requires it.
    No player is silently omitted.
    """
    chunks = []
    current = []

    for player in players:
        candidate = current + [player]
        if current and len(_prediction_direction_post(candidate, direction)) > 280:
            chunks.append(current)
            current = [player]
        else:
            current = candidate

    if current:
        chunks.append(current)
    return chunks


def maybe_post_prediction_crossings(bootstrap, state):
    """Post newly qualifying official FPL textual statuses.

    Newly qualifying risers are grouped together in rise-only posts, and newly
    qualifying fallers are grouped together in fall-only posts. A player is
    posted only once in the current official price-change cycle. Alerts are
    re-armed only after THAT player's actual price changes.
    """
    rows = official_price_predictions(bootstrap)
    if not rows:
        print("No eligible official price statuses (neutral, locked, calibrating or absent)")
        return

    alerted_rise = state.setdefault("alerted_rise", {})
    alerted_fall = state.setdefault("alerted_fall", {})

    risers = sorted(
        [p for p in rows if p['likelihood'] in (4, 5) and str(p["id"]) not in alerted_rise],
        key=lambda p: p['likelihood'], reverse=True,
    )
    fallers = sorted(
        [p for p in rows if p['likelihood'] in (-4, -5) and str(p["id"]) not in alerted_fall],
        key=lambda p: p['likelihood'],
    )

    if not risers and not fallers:
        return

    now_iso = datetime.now(timezone.utc).isoformat()

    # One or more RISE-only posts, only when there are new risers.
    for chunk in _prediction_direction_chunks(risers, "rise"):
        post = _prediction_direction_post(chunk, "rise")
        if not safe_post(post, "price prediction rise batch"):
            continue
        for p in chunk:
            alerted_rise[str(p["id"])] = {
                "name": p["name"],
                "prediction": float(p["prediction"]),
                "alerted_at": now_iso,
            }

    # One or more FALL-only posts, only when there are new fallers.
    for chunk in _prediction_direction_chunks(fallers, "fall"):
        post = _prediction_direction_post(chunk, "fall")
        if not safe_post(post, "price prediction fall batch"):
            continue
        for p in chunk:
            alerted_fall[str(p["id"])] = {
                "name": p["name"],
                "prediction": float(p["prediction"]),
                "alerted_at": now_iso,
            }

def check_and_post_confirmed_prices(bootstrap, state):
    new_prices = current_official_prices(bootstrap)
    old_prices = state.get("last_prices", {})
    changed = False
    if old_prices:
        rises, falls = find_confirmed_changes(old_prices, new_prices)
        if rises or falls:
            for r_chunk, f_chunk in confirmed_price_chunks(rises, falls):
                post_to_x(format_confirmed_post(r_chunk, f_chunk))
            changed = True
            # Other players changing price must NOT re-arm this player's alert.
            for pid, price in new_prices.items():
                if pid in old_prices and old_prices[pid]['price'] != price['price']:
                    state.setdefault('alerted_rise', {}).pop(pid, None)
                    state.setdefault('alerted_fall', {}).pop(pid, None)
    state["last_prices"] = new_prices
    return changed


def london_fast_window(now_utc=None):
    now_utc = now_utc or datetime.now(timezone.utc)
    local = now_utc.astimezone(ZoneInfo("Europe/London"))
    midnight_today = local.replace(hour=0, minute=0, second=0, microsecond=0)
    target = midnight_today + timedelta(days=1) if local.hour == 23 else midnight_today
    delta_minutes = (local - target).total_seconds() / 60
    return -FAST_PRICE_START_MINUTES_BEFORE <= delta_minutes <= FAST_PRICE_END_MINUTES_AFTER, target


def fast_price_watch(state):
    in_window, target = london_fast_window()
    if not in_window:
        return False

    end_local = target + timedelta(minutes=FAST_PRICE_END_MINUTES_AFTER)
    print(f"Fast official-price watch active until {end_local.isoformat()}")
    while True:
        bootstrap = fetch_official_fpl(fresh=True)
        if check_and_post_confirmed_prices(bootstrap, state):
            save_state(state)
            print("Confirmed price changes detected and posted in fast mode")
            return True

        save_state(state)
        now_local = datetime.now(timezone.utc).astimezone(ZoneInfo("Europe/London"))
        if now_local >= end_local:
            print("Fast price window ended with no confirmed changes")
            return True
        time.sleep(FAST_PRICE_POLL_SECONDS)

def post_to_x(text):
    global LAST_X_POST_AT
    if DRY_RUN:
        print("\n--- DRY RUN / لن يتم النشر ---")
        print(text)
        print("--- END ---\n")
        return

    # One lock covers the match loop and the background news scan, so they
    # cannot create an API-posting burst together.
    with X_POST_LOCK:
        if LAST_X_POST_AT is not None:
            wait = X_POST_MIN_INTERVAL_SECONDS - (time.monotonic() - LAST_X_POST_AT)
            if wait > 0:
                print(f'X post pacing: waiting {wait:.0f}s before the next post')
                time.sleep(wait)

        api_key = os.environ["X_API_KEY"]
        api_secret = os.environ["X_API_SECRET"]
        access_token = os.environ["X_ACCESS_TOKEN"]
        access_secret = os.environ["X_ACCESS_TOKEN_SECRET"]
        auth = OAuth1(api_key, api_secret, access_token, access_secret)

        r = requests.post(
            X_POST_URL,
            auth=auth,
            json={"text": text},
            timeout=20
        )
        if not r.ok:
            # X returns the actionable reason in its response body (for example,
            # duplicate content or an account/API restriction). Never log secrets.
            detail = (r.text or '').replace('\n', ' ').strip()[:1200]
            raise RuntimeError(f'X rejected post ({r.status_code}): {detail or "no response body"}')
        LAST_X_POST_AT = time.monotonic()
        print("Posted:", r.json())


LIVE_POLL_SECONDS = max(15, int(os.getenv('LIVE_POLL_SECONDS', '15')))
ASSIST_WAIT_SECONDS = max(0, int(os.getenv('ASSIST_WAIT_SECONDS', '60')))
LIVE_PRESTART_MINUTES = 20
LIVE_POSTMATCH_SECONDS = 300
LIVE_MAX_SECONDS = 300 * 60
STOP_REQUESTED = False
PLAYER_AR = {
    'Saka': 'ساكا', 'Ødegaard': 'أوديغارد', 'Haaland': 'هالاند',
    'Salah': 'صلاح', 'Palmer': 'بالمر', 'Isak': 'إيزاك',
    'Wirtz': 'فيرتز', 'M.Salah': 'صلاح', 'Rice': 'رايس',
    'Gabriel': 'غابرييل', 'Saliba': 'ساليبا', 'Calafiori': 'كالافيوري',
    'Eze': 'إيزي', 'Madueke': 'مادويكي', 'Martinelli': 'مارتينيلي',
    'Semenyo': 'سيمينيو', 'Watkins': 'واتكينز', 'Rogers': 'روجرز',
    'Mbeumo': 'مبيومو', 'B.Fernandes': 'برونو فرنانديز',
    'Bruno': 'برونو', 'João Pedro': 'جواو بيدرو', 'Neto': 'نيتو',
    'Enzo': 'إنزو', 'Caicedo': 'كايسيدو', 'Gordon': 'غوردون',
    'Bowen': 'بوين', 'Cunha': 'كونيا', 'Solanke': 'سولانكي',
    'Richarlison': 'ريتشارليسون', 'Johnson': 'جونسون',
    'Kulusevski': 'كولوسيفسكي', 'Maddison': 'ماديسون',
    'Grealish': 'غريليش', 'Ndiaye': 'ندياي', 'Beto': 'بيتو',
    'Foden': 'فودين', 'Doku': 'دوكو', 'Cherki': 'شرقي',
    'Gakpo': 'غاكبو', 'Mac Allister': 'ماك أليستر',
    'Szoboszlai': 'سوبوسلاي', 'Frimpong': 'فريمبونغ',
    'Robertson': 'روبرتسون', 'Van Dijk': 'فان دايك',
    'Wissa': 'ويسا', 'Schade': 'شادي', 'Igor Thiago': 'إيغور تياغو',
}


def checkpoint(state):
    """Durable checkpoint; only enabled inside the supplied workflow.

    No resets/force pushes. A push conflict is rebased, not overwritten.
    Failure stops the caller before it sends another match tweet.
    """
    save_state(state)
    if DRY_RUN or os.getenv('BOT_GIT_CHECKPOINT') != 'true':
        return
    def git(*args):
        return subprocess.run(['git', *args], check=True, capture_output=True,
                              text=True, timeout=45)
    branch = os.environ.get('BOT_STATE_BRANCH', 'main')
    git('add', '--', str(STATE_FILE))
    changed = subprocess.run(['git', 'diff', '--cached', '--quiet', '--', str(STATE_FILE)])
    if changed.returncode == 1:
        git('commit', '-m', 'Checkpoint bot state [skip ci]')
    elif changed.returncode != 0:
        raise RuntimeError('Cannot inspect staged state')
    for attempt in range(3):
        try:
            git('push', 'origin', f'HEAD:{branch}')
            return
        except subprocess.CalledProcessError:
            if attempt == 2:
                raise RuntimeError('State push failed; stopping before further match posts')
            git('pull', '--rebase', 'origin', branch)


def match_window(fixtures, state, now):
    """Warm up before kickoff; grace period for late official assist decisions."""
    active = []
    finishes = state.setdefault('match_finished_at', {})
    for f in fixtures:
        kickoff = parse_iso_datetime(f.get('kickoff_time'))
        if not kickoff or not f.get('event'):
            continue
        age = (now - kickoff).total_seconds()
        if age < -LIVE_PRESTART_MINUTES * 60 or age > 8 * 3600:
            continue
        fid = str(f['id'])
        if f.get('finished') or f.get('finished_provisional'):
            if fid not in state.get('match_counts', {}):
                continue  # Never replay a match first discovered after full time.
            finished_at = parse_iso_datetime(finishes.setdefault(fid, now.isoformat()))
            if (now - finished_at).total_seconds() <= LIVE_POSTMATCH_SECONDS:
                active.append(f)
        elif f.get('started') or age < 3 * 3600:
            finishes.pop(fid, None)
            active.append(f)
    return active


def match_counts(live, fixtures, players):
    """Per-fixture explain values avoid mixing two matches in a double GW.

    Missing player/fixture data is not treated as a reversal to zero.
    """
    allowed = {int(f['id']): f for f in fixtures}
    counts = {str(fid): {} for fid in allowed}
    if not isinstance(live, dict) or not isinstance(live.get('elements'), list):
        raise ValueError('Invalid FPL live response')
    for row in live['elements']:
        pid = str(row['id'])
        if pid not in players:
            continue
        for part in row.get('explain', []):
            fid = part.get('fixture')
            if fid not in allowed or not isinstance(part.get('stats'), list):
                continue
            fixture = allowed[fid]
            if players[pid].get('team') not in (fixture['team_h'], fixture['team_a']):
                continue
            values = {
                'goals_scored': 0,
                'assists': 0,
                'red_cards': 0,
            }
            for entry in part['stats']:
                if entry.get('identifier') in values:
                    value = entry.get('value')
                    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                        raise ValueError('Invalid FPL live event count')
                    values[entry['identifier']] = value
            counts[str(fid)][pid] = values
    return counts


def match_post(fixture, changes, players, teams):
    """Format only pairings established from the same official FPL fixture."""
    home = team_ar(teams.get(fixture['team_h'], str(fixture['team_h'])))
    away = team_ar(teams.get(fixture['team_a'], str(fixture['team_a'])))
    # Additional current official team name variants.
    home = {'Hull City': 'هال سيتي', 'Ipswich Town': 'إيبسويتش'}.get(home, home)
    away = {'Hull City': 'هال سيتي', 'Ipswich Town': 'إيبسويتش'}.get(away, away)
    # The fixtures endpoint is FPL's official current match score.
    home_score = fixture.get('team_h_score')
    away_score = fixture.get('team_a_score')
    home_score = home_score if isinstance(home_score, int) else 0
    away_score = away_score if isinstance(away_score, int) else 0
    scoring_teams = {
        players[change['pid']]['team'] for change in changes
        if change['delta'] > 0 and change['stat'] == 'goals_scored'
    }
    home_score_text = f'[{home_score}]' if fixture['team_h'] in scoring_teams else str(home_score)
    away_score_text = f'[{away_score}]' if fixture['team_a'] in scoring_teams else str(away_score)
    lines = [f'🏟️ {home} {home_score_text} - {away_score_text} {away}']
    for change in changes:
        name = players[change['pid']]['web_name']
        name = PLAYER_AR.get(name, name)  # Official spelling if no verified Arabic alias.
        goal = change['stat'] == 'goals_scored'
        if change['delta'] > 0:
            label = {
                'goals_scored': '⚽️ هدف',
                'assists': '🅰️ صناعة',
                'red_cards': '🟥 طرد',
            }[change['stat']]
            suffix = '' if change['delta'] == 1 else f" ×{change['delta']}"
            lines.append(f'{label} | {name}{suffix}')
        else:
            label = {
                'goals_scored': 'الأهداف',
                'assists': 'الصناعة',
                'red_cards': 'حالات الطرد',
            }[change['stat']]
            lines.append(f"⚠️ تصحيح FPL | {name}: {label} المحتسبة {change['new']}")
    return '\n'.join(lines + ['', '#FPL', '#فانتزي_البريميرليغ'])


def _publish_match_batch(fixture, batch, players, teams, previous, state, outbox, now):
    revision = int(state.get('match_revision', 0)) + 1
    key = f"{fixture['id']}:{revision}"
    post = match_post(fixture, batch, players, teams)
    if len(post) > 280:
        raise ValueError('Match post exceeds limit; event retained for review')
    # Reserve BEFORE X call. If the response is lost, do not blindly retry a
    # potentially already-published tweet after a restart.
    state['match_revision'] = revision
    for change in batch:
        previous[change['pid']][change['stat']] = change['new']
    outbox[key] = {'status': 'pending', 'text': post, 'at': now.isoformat()}
    checkpoint(state)
    try:
        post_to_x(post)
    except Exception as exc:
        outbox[key]['status'] = 'uncertain'
        checkpoint(state)
        print(f'Match post {key} requires manual review: {type(exc).__name__}; NOT retried')
        raise
    outbox[key]['status'] = 'sent' if not DRY_RUN else 'dry_run'
    checkpoint(state)


def process_matches(fixtures, live_by_gw, bootstrap, state, now_utc=None):
    now = now_utc or datetime.now(timezone.utc)
    players = {str(p['id']): p for p in bootstrap['elements']}
    teams = {t['id']: t['name'] for t in bootstrap['teams']}
    stored = state.setdefault('match_counts', {})
    outbox = state.setdefault('match_outbox', {})
    pending_by_fixture = state.setdefault('pending_goals', {})
    for fixture in fixtures:
        live = live_by_gw.get(fixture['event'])
        if live is None:
            continue
        fid = str(fixture['id'])
        current = match_counts(live, [fixture], players)[fid]
        if not current:
            continue
        if fid not in stored:
            # First installation during a match: establish baseline, not old goals.
            stored[fid] = current
            checkpoint(state)
            print(f'Match {fid}: baseline saved; no historical events posted')
            continue
        previous = stored[fid]
        pending = pending_by_fixture.setdefault(fid, [])
        changes = []
        for pid, values in current.items():
            if pid not in previous:
                previous[pid] = values
                continue
            for stat, value in values.items():
                old = previous[pid].get(stat, 0)
                if value != old:
                    changes.append({'pid': pid, 'stat': stat, 'old': old,
                                    'new': value, 'delta': value-old})
        batches = []

        # If an unposted goal is removed by FPL, cancel it instead of publishing
        # a goal followed by a correction. Other official corrections stay visible.
        remaining = []
        for change in changes:
            if change['stat'] == 'goals_scored' and change['delta'] < 0:
                held = [p for p in pending if p['change']['pid'] == change['pid']]
                if held:
                    pending[:] = [p for p in pending if p['change']['pid'] != change['pid']]
                    previous[change['pid']]['goals_scored'] = change['new']
                    checkpoint(state)
                    continue
            remaining.append(change)
        changes = remaining

        positive_goals = [c for c in changes if c['stat'] == 'goals_scored' and c['delta'] > 0]
        positive_assists = [c for c in changes if c['stat'] == 'assists' and c['delta'] > 0]
        used = set()

        # Pair any goal number (not just the first) when one +1 goal and one +1
        # assist arrive for the same team in this fixture update.
        for team_id in (fixture['team_h'], fixture['team_a']):
            team_goals = [c for c in positive_goals
                          if c['delta'] == 1 and players[c['pid']]['team'] == team_id]
            team_assists = [c for c in positive_assists
                            if c['delta'] == 1 and players[c['pid']]['team'] == team_id]
            if len(team_goals) == len(team_assists) == 1:
                batches.append([team_goals[0], team_assists[0]])
                used.update((id(team_goals[0]), id(team_assists[0])))

        # FPL sometimes credits the assist after the goal. Join it to exactly one
        # still-held goal from the same team, but never guess when ambiguous.
        for assist in positive_assists:
            if id(assist) in used or assist['delta'] != 1:
                continue
            team_id = players[assist['pid']]['team']
            candidates = []
            for held in pending:
                detected = parse_iso_datetime(held.get('detected_at'))
                age = (now - detected).total_seconds() if detected else ASSIST_WAIT_SECONDS + 1
                goal = held['change']
                if age <= ASSIST_WAIT_SECONDS and players[goal['pid']]['team'] == team_id:
                    candidates.append(held)
            if len(candidates) == 1:
                held = candidates[0]
                pending.remove(held)
                batches.append([held['change'], assist])
                used.add(id(assist))

        # Hold unmatched single goals briefly to allow the official assist value
        # to catch up. Larger jumps are summaries and are posted immediately.
        for goal in positive_goals:
            if id(goal) in used:
                continue
            if goal['delta'] == 1:
                previous[goal['pid']]['goals_scored'] = goal['new']
                pending.append({'change': goal, 'detected_at': now.isoformat()})
                checkpoint(state)
            else:
                batches.append([goal])
                used.add(id(goal))

        # Unpaired assists and official corrections are sent on their own.
        for change in changes:
            if id(change) not in used and change not in positive_goals:
                batches.append([change])
                used.add(id(change))

        for batch in batches:
            _publish_match_batch(fixture, batch, players, teams, previous,
                                 state, outbox, now)

        # Publish a held goal alone once the official assist waiting window ends.
        for held in list(pending):
            detected = parse_iso_datetime(held.get('detected_at'))
            if detected and (now - detected).total_seconds() >= ASSIST_WAIT_SECONDS:
                pending.remove(held)
                _publish_match_batch(fixture, [held['change']], players, teams,
                                     previous, state, outbox, now)

        if not pending:
            pending_by_fixture.pop(fid, None)

        # Keep enough history for review without unbounded state growth.
        if len(outbox) > 500:
            for oldkey in list(outbox):
                if len(outbox) <= 500:
                    break
                if outbox[oldkey]['status'] in ('sent', 'dry_run'):
                    outbox.pop(oldkey)


def regular_prices(bootstrap, state):

    # Official confirmed price changes: compare every run so the update is posted
    # on the first GitHub Actions cycle after the official 02:00 Saudi update.
    try:
        check_and_post_confirmed_prices(bootstrap, state)
    except Exception as exc:
        print("Confirmed price check failed:", exc)

    # Official FPL predictor: textual status only, no percentage threshold.
    try:
        maybe_post_prediction_crossings(bootstrap, state)
    except Exception as exc:
        print("Official predictor failed:", exc)

    checkpoint(state)


def run_once():
    state = load_state()
    bootstrap = fetch_official_fpl()
    began = time.monotonic()
    next_regular = 0
    next_prices = 0
    news_future = None
    news_keys = ('news_seen', 'recent_news_updates', 'news_logic_version',
                 'conference_schedule', 'ffscout_sections')
    executor = ThreadPoolExecutor(max_workers=1)
    failures = 0
    def news_task(b, f, snapshot):
        scan_news(b, f, snapshot)
        return snapshot
    try:
        while not STOP_REQUESTED and time.monotonic() - began < LIVE_MAX_SECONDS:
            cycle = time.monotonic()
            if news_future is not None and news_future.done():
                try:
                    updated = news_future.result()
                    for key in news_keys:
                        if key in updated:
                            state[key] = updated[key]
                    checkpoint(state)
                except Exception as exc:
                    print('Conference scan failed:', type(exc).__name__)
                news_future = None
            try:
                fixtures = fetch_json(FPL_FIXTURES_URL)
                if not isinstance(fixtures, list):
                    raise ValueError('Invalid fixtures response')
                now = datetime.now(timezone.utc)
                active = match_window(fixtures, state, now)
                live = {gw: fetch_json(f'https://fantasy.premierleague.com/api/event/{gw}/live/')
                        for gw in sorted({f['event'] for f in active})}
                failures = 0
            except requests.RequestException as exc:
                failures += 1
                delay = min(300, 15 * (2 ** min(failures, 5)))
                response = getattr(exc, 'response', None)
                if response is not None and response.status_code == 429:
                    try:
                        delay = max(delay, int(response.headers.get('Retry-After', delay)))
                    except ValueError:
                        delay = max(delay, 300)
                print(f'FPL connection failed; backoff {delay}s')
                if failures >= 5:
                    break
                time.sleep(delay)
                continue
            process_matches(active, live, bootstrap, state)
            if time.monotonic() >= next_prices:
                regular_prices(bootstrap, state)
                next_prices = time.monotonic() + 900
            if cycle >= next_regular and news_future is None:
                # Existing conference logic runs in background, not in the
                # 15-second goal/assist polling path. Only its own keys merge.
                news_future = executor.submit(news_task, copy.deepcopy(bootstrap),
                                              copy.deepcopy(fixtures), copy.deepcopy(state))
                next_regular = cycle + 900
            if not active:
                print('IDLE: no live/upcoming matches; return to 15-minute GitHub schedule')
                break
            print('LIVE:', ','.join(str(f['id']) for f in active),
                  f'poll target={LIVE_POLL_SECONDS}s')
            time.sleep(max(0, LIVE_POLL_SECONDS - (time.monotonic()-cycle)))
            if time.monotonic() >= next_prices:
                bootstrap = fetch_official_fpl()
    finally:
        # Allow the existing bounded news scan to finish before final checkpoint.
        executor.shutdown(wait=True)
        if news_future is not None:
            try:
                updated = news_future.result()
                for key in news_keys:
                    if key in updated:
                        state[key] = updated[key]
            except Exception as exc:
                print('Conference scan failed:', type(exc).__name__)
        checkpoint(state)


def request_stop(signum, frame):
    global STOP_REQUESTED
    STOP_REQUESTED = True

if __name__ == "__main__":
    signal.signal(signal.SIGTERM, request_stop)
    run_once()
