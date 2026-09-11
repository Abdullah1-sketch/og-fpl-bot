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
X_POST_URL = "https://api.x.com/2/tweets"
STATE_FILE = Path("state.json")

# Official FPL predictor settings.
PREDICTION_THRESHOLD = float(os.getenv("PREDICTION_THRESHOLD", "85"))
PREDICTION_POST_HOUR = int(os.getenv("PREDICTION_POST_HOUR", "23"))
PREDICTION_POST_MINUTE = int(os.getenv("PREDICTION_POST_MINUTE", "30"))
MINUTES_OWNERSHIP_THRESHOLD = float(os.getenv("MINUTES_OWNERSHIP_THRESHOLD", "5"))

# Fast official-price watch around 00:00 Europe/London.
FAST_PRICE_POLL_SECONDS = int(os.getenv("FAST_PRICE_POLL_SECONDS", "15"))
FAST_PRICE_START_MINUTES_BEFORE = int(os.getenv("FAST_PRICE_START_MINUTES_BEFORE", "5"))
FAST_PRICE_END_MINUTES_AFTER = int(os.getenv("FAST_PRICE_END_MINUTES_AFTER", "20"))

NEWS_LOGIC_VERSION = 4
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"

# Keep API use controlled.
MAX_NEWS_AI_CALLS_PER_RUN = int(os.getenv("MAX_NEWS_AI_CALLS_PER_RUN", "10"))
MAX_NEWS_TWEETS_PER_RUN = int(os.getenv("MAX_NEWS_TWEETS_PER_RUN", "6"))

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
    {
        "name": "Fantasy Football Scout",
        "url": "https://www.fantasyfootballscout.co.uk/feed/",
        "type": "rss",
        "official": False,
    },
    {
        "name": "Premier League",
        "url": "https://www.premierleague.com/en/news",
        "type": "html",
        "official": True,
        "path_hints": ["/en/news/"],
    },
    {
        "name": "UEFA Champions League",
        "url": "https://www.uefa.com/uefachampionsleague/news/",
        "type": "html",
        "official": True,
        "path_hints": ["/uefachampionsleague/news/"],
    },
    {
        "name": "UEFA Europa League",
        "url": "https://www.uefa.com/uefaeuropaleague/news/",
        "type": "html",
        "official": True,
        "path_hints": ["/uefaeuropaleague/news/"],
    },
    {
        "name": "UEFA Conference League",
        "url": "https://www.uefa.com/uefaconferenceleague/news/",
        "type": "html",
        "official": True,
        "path_hints": ["/uefaconferenceleague/news/"],
    },

    # 2026/27 Premier League clubs
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

FPL_NEWS_KEYWORDS = [
    "injury", "injured", "fitness", "fit", "available", "unavailable",
    "doubt", "doubtful", "ruled out", "training", "trained", "return",
    "returns", "recover", "recovery", "illness", "sick", "knock",
    "hamstring", "ankle", "knee", "muscle", "minutes", "start",
    "starting", "rotation", "rest", "rested", "suspended", "suspension",
    "ban", "team news", "press conference", "press-conference",
    "update", "assessment", "assess", "came off", "substituted",
    "substitution", "replaced", "came on", "full 90", "90 minutes",
    "match report", "report", "reaction", "withdrawn", "availability",
    "squad", "medical", "scan",
]

ALLOWED_STATUS = {
    "AVAILABLE", "OUT", "DOUBT", "ASSESSMENT", "TRAINING",
    "INJURY", "SUSPENDED", "MINUTES", "ROTATION"
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


def article_text(url):
    html = fetch_text(url)
    title, text, _ = parse_html(html)

    # Cap the model input. Keep the beginning, where team-news articles normally
    # contain the key update.
    return title, text[:24000]


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


def analyze_news_with_openai(source, title, text, matched_players, gw_number=None):
    from openai import OpenAI

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    player_lines = "\n".join(
        f"- {p['web_name']} | {p['team']} | ownership={p['ownership']:.1f}%"
        for p in matched_players
    )

    source_rule = (
        "This is an official source. Use only claims supported by the supplied article."
        if source.get("official")
        else (
            "This is a secondary FPL source. Use only explicit attributed manager/club statements. "
            "Ignore rumours and writer inference."
        )
    )

    prompt = f"""
You are the Arabic news editor for an FPL account on X.

Source: {source['name']}
Source rule: {source_rule}
Current FPL gameweek: {gw_number or 'unknown'}

Current Premier League players detected in the article:
{player_lines}

Article title:
{title}

Article text:
{text}

Classify the item and extract ONLY useful, source-supported FPL information.

STRICT RULES:
1) Never invent facts and never strengthen certainty.
2) doubt/could/may/assess/wait-and-see must remain uncertain.
3) back in training does NOT mean available.
4) available does NOT mean the player will start.
5) a substitution does NOT mean injury unless the source says so.
6) ignore transfers, rumours, generic praise, tactics with no FPL impact, academy/women's news and historical content.
7) Suspension: only output it when the cause is explicit in the supplied source. Set suspension_reason to exactly one of DIRECT_RED, SECOND_YELLOW, YELLOW_ACCUMULATION. If the source only says "suspended" without the cause, do not output a suspension item.
8) Match minutes: ONLY include players whose ownership shown above is >= {MINUTES_OWNERSHIP_THRESHOLD:.1f}%. Minutes must be explicit in the source OR exactly derivable from a complete starting XI plus a complete substitution record. Never guess. Set minutes_basis to EXPLICIT, SUBSTITUTION, or FULL_MATCH_DERIVED.
9) Press conference: output ONE complete conference summary, not separate player tweets. Include EVERY material availability/fitness/suspension update mentioned. Do not stop after two or three players. Combine players with the same status to save space. If an article is a multi-club roundup, do NOT classify the whole article as one press conference.
10) Press-conference summary_ar must fit an X post: target <=165 Arabic characters, using short bullet lines with ✅ ⚠️ ❌ while preserving every material update.
11) Natural concise Arabic football-account wording, not literal translation.
12) No source line, URL or hashtags in generated Arabic text.

Return JSON only:
{{
  "kind": "PRESS_CONFERENCE|MATCH_REPORT|PLAYER_UPDATE|IGNORE",
  "speaker_ar": "Arabic name of manager/speaker if clearly identified, else empty",
  "summary_ar": "conference bullet summary only; otherwise empty",
  "updates": [
    {{"player":"exact web_name", "status":"AVAILABLE|OUT|DOUBT|ASSESSMENT|TRAINING|INJURY|SUSPENDED|ROTATION", "summary_ar":"concise Arabic update", "suspension_reason":"DIRECT_RED|SECOND_YELLOW|YELLOW_ACCUMULATION|"}}
  ],
  "minutes": [
    {{"player":"exact web_name", "minutes":77, "minutes_basis":"EXPLICIT|SUBSTITUTION|FULL_MATCH_DERIVED"}}
  ]
}}
"""

    response = client.responses.create(
        model="gpt-5.6-terra",
        input=prompt,
        reasoning={"effort": "none"},
        max_output_tokens=900,
        text={"verbosity": "low"},
    )
    data = parse_json_object(response.output_text)
    if not isinstance(data, dict):
        return {"kind": "IGNORE", "updates": [], "minutes": []}
    return data


def format_press_conference_post(team_name, speaker_ar, gw_number, summary_ar):
    speaker = speaker_ar.strip() or "مؤتمر المدرب"
    gw = f" — GW{gw_number}" if gw_number else ""
    return "\n".join([
        f"🚨⚽️| {speaker} [{team_ar(team_name)}]{gw}:",
        "",
        summary_ar.strip(),
        "",
        "#FPL",
        "#فانتزي_البريميرليغ",
    ])


def format_match_minutes_post(team_name, minutes_rows):
    lines = [f"⏱️ دقائق لاعبي الفانتزي | {team_ar(team_name)}", ""]
    for row in minutes_rows:
        lines.append(f"{row['player']} — {int(row['minutes'])} دقيقة")
    lines += ["", "#FPL", "#فانتزي_البريميرليغ"]
    return "\n".join(lines)


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


def scan_news(bootstrap, state):
    """Discover all sources first, then process highest-value FPL items first."""
    if "OPENAI_API_KEY" not in os.environ:
        print("News skipped: OPENAI_API_KEY is missing")
        return

    now_utc = datetime.now(timezone.utc)
    cleanup_recent_updates(state, now_utc)
    players = build_player_index(bootstrap)
    player_lookup = {p["web_name"]: p for p in players}
    gw_number = current_gameweek_number(bootstrap)

    previous_logic_version = int(state.get("news_logic_version", 0) or 0)
    if previous_logic_version != NEWS_LOGIC_VERSION:
        # New extraction logic: baseline current links again so old articles are not
        # reposted with the new formatter. Only future articles will be processed.
        state["news_seen"] = {}
        state["recent_news_updates"] = {}
        state["news_logic_version"] = NEWS_LOGIC_VERSION

    news_seen = state.setdefault("news_seen", {})
    ai_calls = 0
    tweets = 0
    source_cache = {}
    candidates = []

    # Discover all sources before spending AI calls. This prevents FFS or early
    # clubs from using the whole budget before a later press conference is seen.
    for source_order, source in enumerate(NEWS_SOURCES):
        try:
            discovered = discover_source_links(source)
        except Exception as exc:
            print(f"News source failed [{source['name']}]: {exc}")
            continue

        source_key = source["name"]
        old_seen = set(news_seen.get(source_key, []))
        current_urls = [x["url"] for x in discovered]
        source_cache[source_key] = {
            "source": source,
            "old_seen": old_seen,
            "current_urls": current_urls,
        }

        if source_key not in news_seen:
            news_seen[source_key] = current_urls[:100]
            print(f"News baseline created: {source_key} ({len(current_urls)} links)")
            continue

        new_items = [x for x in discovered if x["url"] not in old_seen]
        for item_order, item in enumerate(new_items[:15]):
            candidates.append((candidate_priority(source, item), item_order, source_order, source, item))

    candidates.sort(key=lambda x: (x[0], x[1], x[2]))
    processed_urls = set()

    for _, _, _, source, item in candidates:
        if ai_calls >= MAX_NEWS_AI_CALLS_PER_RUN or tweets >= MAX_NEWS_TWEETS_PER_RUN:
            break

        source_key = source["name"]
        cache = source_cache[source_key]
        old_seen = cache["old_seen"]
        url = item["url"]
        if url in processed_urls:
            old_seen.add(url)
            continue

        try:
            title, text = article_text(url)
        except Exception as exc:
            print(f"Article fetch failed [{url}]: {exc}")
            continue

        combined = f"{item.get('title', '')} {title} {text}"
        matched = match_current_players(combined, players)
        title_low = f"{item.get('title', '')} {title}".casefold()
        press_hint = "press conference" in title_low or "press-conference" in title_low

        if not matched or (not looks_fpl_relevant(combined) and not press_hint):
            old_seen.add(url)
            processed_urls.add(url)
            continue

        try:
            analysis = analyze_news_with_openai(
                source, title or item.get("title", ""), text, matched, gw_number
            )
            ai_calls += 1
        except Exception as exc:
            print(f"OpenAI news analysis failed [{url}]: {exc}")
            continue

        kind = str(analysis.get("kind") or "IGNORE").strip().upper()
        allowed_players = {p["web_name"] for p in matched}
        team_name = source.get("club_name") or (matched[0]["team"] if matched else source["name"])

        # One standalone tweet for the whole conference.
        if kind == "PRESS_CONFERENCE":
            summary_ar = str(analysis.get("summary_ar") or "").strip()
            speaker_ar = str(analysis.get("speaker_ar") or "").strip()
            if summary_ar:
                post = format_press_conference_post(team_name, speaker_ar, gw_number, summary_ar)
                if safe_post(post, "press conference"):
                    tweets += 1

        # Post-match minutes: ownership >=5% only, one grouped tweet per match report.
        if kind == "MATCH_REPORT" and tweets < MAX_NEWS_TWEETS_PER_RUN:
            valid_minutes = []
            seen_players = set()
            for row in analysis.get("minutes") or []:
                player = str(row.get("player") or "").strip()
                if player not in allowed_players or player in seen_players:
                    continue
                p = player_lookup.get(player)
                if not p or p.get("ownership", 0) < MINUTES_OWNERSHIP_THRESHOLD:
                    continue
                basis = str(row.get("minutes_basis") or "").strip().upper()
                if basis not in {"EXPLICIT", "SUBSTITUTION", "FULL_MATCH_DERIVED"}:
                    continue
                try:
                    mins = int(row.get("minutes"))
                except Exception:
                    continue
                if not (0 <= mins <= 130):
                    continue
                valid_minutes.append({"player": player, "minutes": mins})
                seen_players.add(player)

            # If unusually long, trim only the lowest-ownership rows until the X post fits.
            valid_minutes.sort(key=lambda r: player_lookup[r["player"]]["ownership"], reverse=True)
            while valid_minutes and len(format_match_minutes_post(team_name, valid_minutes)) > 280:
                valid_minutes.pop()
            if valid_minutes and safe_post(format_match_minutes_post(team_name, valid_minutes), "match minutes"):
                tweets += 1

        # Non-conference availability/injury/suspension items.
        if kind != "PRESS_CONFERENCE":
            for update in analysis.get("updates") or []:
                if tweets >= MAX_NEWS_TWEETS_PER_RUN:
                    break
                player = str(update.get("player") or "").strip()
                status = str(update.get("status") or "").strip().upper()
                summary_ar = str(update.get("summary_ar") or "").strip()
                if player not in allowed_players:
                    continue
                if status not in ALLOWED_STATUS or status == "MINUTES":
                    continue
                if not summary_ar or len(summary_ar) > 220:
                    continue
                if is_recent_duplicate(state, player, status, now_utc):
                    continue
                p = player_lookup.get(player)
                if not p:
                    continue

                if status == "SUSPENDED":
                    reason = str(update.get("suspension_reason") or "").strip().upper()
                    post = format_suspension_post(p["team"], player, reason)
                    if not post:
                        continue
                else:
                    post = format_news_post(p["team"], summary_ar)

                if safe_post(post, "news update"):
                    mark_recent_update(state, player, status, now_utc)
                    tweets += 1

        old_seen.add(url)
        processed_urls.add(url)

    # Only URLs actually processed/filtered are marked seen. Candidates held back by
    # the AI/tweet cap remain unseen and are retried next run.
    for source_key, cache in source_cache.items():
        current_urls = cache["current_urls"]
        old_seen = cache["old_seen"]
        old_seen.update(u for u in current_urls if u in processed_urls)
        ordered = [u for u in current_urls if u in old_seen]
        already = set(ordered)
        ordered += [u for u in old_seen if u not in already]
        news_seen[source_key] = ordered[:160]

    print(f"News scan complete: candidates={len(candidates)}, AI calls={ai_calls}, tweets={tweets}")

def riyadh_now(now_utc=None):
    return (now_utc or datetime.now(timezone.utc)).astimezone(ZoneInfo("Asia/Riyadh"))


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

    # Dedicated daily triggers start the fast official-price watcher 5 minutes
    # before UK midnight. One trigger covers BST, the other GMT. Regular 15-minute
    # runs never enter the long polling loop.
    trigger = os.getenv("RUN_TRIGGER", "").strip()
    special_fast_triggers = {"55 22 * * *", "55 23 * * *"}
    if trigger in special_fast_triggers:
        in_fast_window, _ = london_fast_window()
        if not in_fast_window:
            print("Special price-watch trigger is outside the current UK midnight window; exiting.")
            return
        fast_price_watch(state)
        save_state(state)
        return

    bootstrap = fetch_official_fpl()

    # Confirmed changes first so stale predictions can never be posted after release.
    try:
        check_and_post_confirmed_prices(bootstrap, state)
    except Exception as exc:
        print("Confirmed prices failed:", exc)

    # Daily 23:30 Saudi prediction, official FPL data, >=85%.
    try:
        maybe_post_daily_predictions(bootstrap, state)
    except Exception as exc:
        print("Official predictor failed:", exc)

    try:
        maybe_post_deadline_alerts(bootstrap, state)
    except Exception as exc:
        print("Deadline alerts failed:", exc)

    try:
        scan_news(bootstrap, state)
    except Exception as exc:
        print("News scan failed:", exc)

    save_state(state)

if __name__ == "__main__":
    run_once()
