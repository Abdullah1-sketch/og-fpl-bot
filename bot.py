import os
import json
import re
import hashlib
import time
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

# Official FPL predictor settings.
PREDICTION_THRESHOLD = float(os.getenv("PREDICTION_THRESHOLD", "85"))
PREDICTION_POST_HOUR = int(os.getenv("PREDICTION_POST_HOUR", "23"))
PREDICTION_POST_MINUTE = int(os.getenv("PREDICTION_POST_MINUTE", "30"))

# Fast official-price watch around 00:00 Europe/London.
FAST_PRICE_POLL_SECONDS = int(os.getenv("FAST_PRICE_POLL_SECONDS", "15"))
FAST_PRICE_START_MINUTES_BEFORE = int(os.getenv("FAST_PRICE_START_MINUTES_BEFORE", "5"))
FAST_PRICE_END_MINUTES_AFTER = int(os.getenv("FAST_PRICE_END_MINUTES_AFTER", "20"))

NEWS_LOGIC_VERSION = 7
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"

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
        except Exception:
            pass
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
    }


def save_state(state):
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


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
    """Return official FPL projected price-change percentages.

    2026/27 player objects expose price_change_projections. offset=0 is the
    next price window. price_change_percent is used only as a fallback.
    """
    rows = []
    for p in bootstrap.get("elements", []):
        raw_pct = None
        for proj in p.get("price_change_projections") or []:
            try:
                if int(proj.get("offset", 0)) == 0:
                    raw_pct = proj.get("projected_percent")
                    break
            except Exception:
                continue
        if raw_pct is None:
            raw_pct = p.get("price_change_percent")
        if raw_pct in (None, ""):
            continue
        try:
            pct = float(raw_pct)
        except (TypeError, ValueError):
            continue
        rows.append({
            "id": str(p.get("id")),
            "name": str(p.get("web_name") or p.get("second_name") or "Player"),
            "prediction": pct,
            "ownership": float(p.get("selected_by_percent") or 0),
        })
    return rows

def _prediction_row(p):
    pct = abs(float(p["prediction"]))
    marker = "🔥" if pct >= 95 else "⚠️"
    return f"{marker} {p['name']} — {pct:.0f}%"


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
    """Fail-closed conference scanner.

    PRE-MATCH requires a verified official date+time schedule that has already started.
    POST-MATCH requires the article publication time to fall after a real FPL fixture.
    Both require exact source evidence and current-roster validation before posting.
    """
    if "OPENAI_API_KEY" not in os.environ:
        print("Conference scan skipped: OPENAI_API_KEY is missing")
        return

    now_utc = datetime.now(timezone.utc)
    cleanup_conference_schedule(state, now_utc)

    previous_logic_version = int(state.get("news_logic_version", 0) or 0)
    logic_reset = previous_logic_version != NEWS_LOGIC_VERSION
    if logic_reset:
        state["news_seen"] = {}
        state["recent_news_updates"] = {}
        state["conference_schedule"] = {}
        state["news_logic_version"] = NEWS_LOGIC_VERSION
        print("Conference logic upgraded: baselining current official pages before live posting")

    news_seen = state.setdefault("news_seen", {})
    source_cache = {}
    candidates = []
    schedule_candidates = []
    schedule_ai_calls = 0
    ai_calls = 0
    tweets = 0

    def conference_hint(source, item):
        hay = f"{source.get('name','')} {item.get('title','')} {item.get('url','')}".casefold()
        strong = (
            "press conference", "press-conference", "pressconference",
            "pre-match", "pre match", "post-match", "post match",
            "media conference", "every word", "transcript", "watch live",
        )
        return 0 if any(k in hay for k in strong) else 2

    for source_order, source in enumerate(NEWS_SOURCES):
        try:
            discovered = discover_source_links(source)
        except Exception as exc:
            print(f"Conference source failed [{source['name']}]: {exc}")
            continue

        source_key = source["name"]
        old_seen = set(news_seen.get(source_key, []))
        current_urls = [x["url"] for x in discovered]
        source_cache[source_key] = {"old_seen": old_seen, "current_urls": current_urls}

        # Look at the strongest current official conference pages to learn exact
        # announced times. This pass NEVER posts.
        for item_order, item in enumerate(discovered[:15]):
            if conference_hint(source, item) == 0:
                schedule_candidates.append((item_order, source_order, source, item))

        if source_key not in news_seen:
            news_seen[source_key] = current_urls[:120]
            print(f"Conference baseline created: {source_key} ({len(current_urls)} links)")
            continue

        new_items = [x for x in discovered if x["url"] not in old_seen]
        for item_order, item in enumerate(new_items[:20]):
            candidates.append((conference_hint(source, item), item_order, source_order, source, item))

    # Pass 1: learn exact official press-conference schedules from official pages.
    schedule_candidates.sort(key=lambda x: (x[0], x[1]))
    schedule_seen = set()
    for _, _, source, item in schedule_candidates:
        if schedule_ai_calls >= MAX_SCHEDULE_AI_CALLS_PER_RUN:
            break
        url = item["url"]
        if url in schedule_seen:
            continue
        schedule_seen.add(url)
        try:
            title, text, published_utc = article_text(url)
        except Exception:
            continue
        if published_utc is None:
            continue
        # Schedule notices can be published up to 3 days before the conference.
        if not (-timedelta(minutes=5) <= now_utc - published_utc <= timedelta(days=3)):
            continue
        if not any(k in f"{title} {text[:3000]}".casefold() for k in ("press conference", "press-conference", "media conference", "watch live")):
            continue
        try:
            data = extract_schedule_with_openai(source, title, text[:12000], published_utc)
            schedule_ai_calls += 1
        except Exception as exc:
            print(f"Schedule extraction failed [{url}]: {exc}")
            continue
        if not data.get("is_schedule"):
            continue
        evidence = str(data.get("time_evidence") or "").strip()
        start = parse_iso_datetime(data.get("conference_datetime_iso"))
        if not start or not evidence_is_in_source(evidence, text, max_words=25) or not has_clock_token(evidence):
            continue
        if not (now_utc - timedelta(hours=CONFERENCE_SCHEDULE_TTL_HOURS) <= start <= now_utc + timedelta(days=3)):
            continue
        team_name = source_team_name(source)
        add_conference_schedule(state, team_name, start, url, evidence, now_utc)
        print(f"Verified conference schedule: {team_name} @ {start.isoformat()}")

    # On the first run of this stricter logic we baseline only. This prevents any
    # old/stale article from being reinterpreted and posted during the upgrade.
    if logic_reset:
        print(f"Strict conference baseline complete: schedules={schedule_ai_calls}, tweets=0")
        return

    # Pass 2: only brand-new official pages can become posts.
    candidates.sort(key=lambda x: (x[0], x[1], x[2]))
    processed_urls = set()

    for _, _, _, source, item in candidates:
        if ai_calls >= MAX_NEWS_AI_CALLS_PER_RUN or tweets >= MAX_NEWS_TWEETS_PER_RUN:
            break

        source_key = source["name"]
        old_seen = source_cache[source_key]["old_seen"]
        url = item["url"]
        team_name = source_team_name(source)

        try:
            title, text, published_utc = article_text(url)
        except Exception as exc:
            print(f"Conference article fetch failed [{url}]: {exc}")
            continue

        # Hard freshness gate: no publication timestamp = no automatic post.
        if not article_is_fresh(published_utc, now_utc):
            print(f"Conference rejected by publication-time gate: {url}")
            old_seen.add(url)
            processed_urls.add(url)
            continue

        roster = team_roster_names(bootstrap, team_name)
        if not roster:
            print(f"Conference rejected: could not map source to current FPL club [{team_name}]")
            old_seen.add(url)
            processed_urls.add(url)
            continue

        pre_schedule = latest_started_schedule(state, team_name, now_utc)

        try:
            analysis = analyze_news_with_openai(
                source, title or item.get("title", ""), text,
                team_name, roster, pre_schedule, published_utc,
            )
            ai_calls += 1
        except Exception as exc:
            print(f"Conference analysis failed [{url}]: {exc}")
            continue

        kind = str(analysis.get("kind") or "IGNORE").strip().upper()
        summary_ar = str(analysis.get("summary_ar") or "").strip()
        players = analysis.get("player_names") or []
        conference_evidence = str(analysis.get("conference_evidence") or "").strip()
        availability_evidence = str(analysis.get("availability_evidence") or "").strip()

        valid = kind in {"PRE_MATCH_CONFERENCE", "POST_MATCH_CONFERENCE"} and bool(summary_ar)
        valid = valid and not reject_empty_update(summary_ar)
        valid = valid and evidence_is_in_source(conference_evidence, text, max_words=20)
        valid = valid and evidence_is_in_source(availability_evidence, text, max_words=25)
        valid = valid and validate_player_names(players, roster)

        if valid and kind == "PRE_MATCH_CONFERENCE":
            valid = pre_match_time_gate(state, team_name, published_utc, now_utc)
        elif valid and kind == "POST_MATCH_CONFERENCE":
            valid = post_match_time_gate(fixtures, bootstrap, team_name, published_utc, now_utc)

        if valid:
            post = format_press_conference_post(team_name, kind, summary_ar)
            if safe_post(post, "conference"):
                tweets += 1
        else:
            print(f"Conference rejected by strict verification: {url}")

        old_seen.add(url)
        processed_urls.add(url)

    for source_key, cache in source_cache.items():
        current_urls = cache["current_urls"]
        old_seen = cache["old_seen"]
        old_seen.update(u for u in current_urls if u in processed_urls)
        ordered = [u for u in current_urls if u in old_seen]
        already = set(ordered)
        ordered += [u for u in old_seen if u not in already]
        news_seen[source_key] = ordered[:180]

    print(
        f"Conference scan complete: candidates={len(candidates)}, "
        f"schedule AI calls={schedule_ai_calls}, AI calls={ai_calls}, tweets={tweets}"
    )


def prediction_window_open(now_utc=None):
    local = riyadh_now(now_utc)
    minutes = local.hour * 60 + local.minute
    start = PREDICTION_POST_HOUR * 60 + PREDICTION_POST_MINUTE
    # One-hour allowance so GitHub Actions delay does not miss the daily prediction.
    return start <= minutes < start + 60


def maybe_post_daily_predictions(bootstrap, state, now_utc=None):
    if not prediction_window_open(now_utc):
        return

    local = riyadh_now(now_utc)
    day_key = local.date().isoformat()
    posted = state.setdefault("prediction_posts", {})
    if posted.get(day_key):
        return

    rows = official_price_predictions(bootstrap)
    if not rows:
        print("Official predictor data is not available in bootstrap-static yet")
        return

    risers = sorted(
        [p for p in rows if p["prediction"] >= PREDICTION_THRESHOLD],
        key=lambda p: p["prediction"], reverse=True
    )
    fallers = sorted(
        [p for p in rows if p["prediction"] <= -PREDICTION_THRESHOLD],
        key=lambda p: p["prediction"]
    )

    if not risers and not fallers:
        print(f"No official price predictions at or above {PREDICTION_THRESHOLD:.0f}%")
        posted[day_key] = "none"
        return

    post_to_x(fit_single_prediction_post(risers, fallers))
    posted[day_key] = True


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
            # Official update closes the previous prediction cycle.
            state["alerted_rise"] = {}
            state["alerted_fall"] = {}
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
    if DRY_RUN:
        print("\n--- DRY RUN / لن يتم النشر ---")
        print(text)
        print("--- END ---\n")
        return

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
    r.raise_for_status()
    print("Posted:", r.json())


def run_once():
    state = load_state()
    bootstrap = fetch_official_fpl()
    fixtures = fetch_json(FPL_FIXTURES_URL)

    # Only output 1: one daily official FPL price-prediction post at ~23:30 Saudi.
    try:
        maybe_post_daily_predictions(bootstrap, state)
    except Exception as exc:
        print("Official predictor failed:", exc)

    # Only output 2/3: genuine pre-match or post-match manager conferences.
    try:
        scan_news(bootstrap, fixtures, state)
    except Exception as exc:
        print("Conference scan failed:", exc)

    save_state(state)

if __name__ == "__main__":
    run_once()
