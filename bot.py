import os
import json
import re
import hashlib
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo
import xml.etree.ElementTree as ET

import requests
from requests_oauthlib import OAuth1
from dotenv import load_dotenv

load_dotenv()

FPL_BOOTSTRAP_URL = "https://fantasy.premierleague.com/api/bootstrap-static/"
FPL_PRICE_CHANGES_PAGE = "https://fantasy.premierleague.com/en/price-changes"
X_POST_URL = "https://api.x.com/2/tweets"
STATE_FILE = Path("state.json")

# OG FPL v3 settings
PRICE_PREDICTION_THRESHOLD_PCT = float(os.getenv("PRICE_PREDICTION_THRESHOLD_PCT", "85"))
PRICE_PREDICTION_HOUR_KSA = int(os.getenv("PRICE_PREDICTION_HOUR_KSA", "23"))
PRICE_PREDICTION_MINUTE_KSA = int(os.getenv("PRICE_PREDICTION_MINUTE_KSA", "30"))
MINUTES_OWNERSHIP_THRESHOLD = float(os.getenv("MINUTES_OWNERSHIP_THRESHOLD", "5"))
X_MAX_CHARS = 280
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"

# Keep API use controlled while giving press conferences priority.
MAX_NEWS_AI_CALLS_PER_RUN = int(os.getenv("MAX_NEWS_AI_CALLS_PER_RUN", "10"))
MAX_NEWS_TWEETS_PER_RUN = int(os.getenv("MAX_NEWS_TWEETS_PER_RUN", "6"))

HEADERS = {
    "User-Agent": "OG-FPL-Bot/1.0 (+FPL news monitor)"
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
    {"name": "Brighton", "url": "https://www.brightonandhovealbion.com/media-article/news", "type": "html", "official": True, "path_hints": ["/media-article/"]},
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
    {"name": "Manchester United", "url": "https://www.manutd.com/en/news", "type": "html", "official": True, "path_hints": ["/en/news/"]},
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
    "Man Utd": "مانشستر يونايتد",
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
    "withdrawn", "availability", "squad", "medical", "scan",
]

ALLOWED_STATUS = {
    "AVAILABLE", "OUT", "DOUBT", "ASSESSMENT", "TRAINING",
    "INJURY", "SUSPENDED", "MINUTES", "ROTATION"
}

SUSPENSION_REASONS = {
    "RED_CARD", "SECOND_YELLOW", "YELLOW_ACCUMULATION", "OTHER_CONFIRMED", "UNKNOWN"
}


def load_state():
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            # Backwards-compatible defaults when upgrading an existing bot.
            state.setdefault("alerted_rise", {})
            state.setdefault("alerted_fall", {})
            state.setdefault("last_prices", {})
            state.setdefault("deadline_alerts", {})
            state.setdefault("news_seen", {})
            state.setdefault("recent_news_updates", {})
            state.setdefault("daily_prediction_date", "")
            state.setdefault("last_confirmed_price_date_uk", "")
            state.setdefault("news_logic_version", 2)
            return state
        except Exception:
            pass
    return {
        "alerted_rise": {},
        "alerted_fall": {},
        "last_prices": {},
        "deadline_alerts": {},
        "news_seen": {},
        "recent_news_updates": {},
        "daily_prediction_date": "",
        "last_confirmed_price_date_uk": "",
        "news_logic_version": 3,
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


def fetch_official_fpl():
    return fetch_json(FPL_BOOTSTRAP_URL)


def _walk_dicts(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _to_pct(value):
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    # APIs commonly expose either fractions (0.95) or percentages (95).
    if abs(n) <= 3:
        n *= 100.0
    return n


def _prediction_direction(record, pct):
    joined = " ".join(
        str(record.get(k, ""))
        for k in ("status", "direction", "trend", "change", "likelihood", "label")
    ).casefold()
    if any(x in joined for x in ("fall", "drop", "decrease", "down")):
        return "fall"
    if any(x in joined for x in ("rise", "increase", "up")):
        return "rise"
    if pct is not None:
        return "fall" if pct < 0 else "rise"
    return None


def normalize_official_predictor_payload(payload, bootstrap):
    """
    The 2026/27 predictor is official but its web-facing schema is undocumented.
    This parser deliberately accepts several likely field names without inventing
    values. If no official predictor values are found, the bot SKIPS the prediction
    tweet rather than falling back to an inaccurate third-party forecast.
    """
    by_id = {str(p["id"]): p for p in bootstrap.get("elements", [])}
    rows = []
    seen = set()

    for d in _walk_dicts(payload):
        metric = None
        metric_key = None

        # Prefer fields explicitly describing the predicted/projected progress.
        for k, v in d.items():
            lk = str(k).casefold().replace("-", "_")
            if (
                ("predict" in lk and "progress" in lk)
                or ("project" in lk and "progress" in lk)
                or lk in {"prediction", "projected", "predicted_progress", "projected_progress"}
            ):
                metric = _to_pct(v)
                metric_key = k
                if metric is not None:
                    break

        if metric is None:
            continue

        pid = None
        for k in ("element", "element_id", "player_id", "id"):
            if k in d and str(d.get(k)).isdigit():
                pid = str(d.get(k))
                break

        p = by_id.get(pid) if pid else None
        name = (
            (p or {}).get("web_name")
            or d.get("web_name")
            or d.get("player_name")
            or d.get("name")
        )
        if not name:
            continue

        direction = _prediction_direction(d, metric)
        if not direction:
            continue

        # Make fallers negative internally so sorting/formatting is unambiguous.
        signed = abs(metric) if direction == "rise" else -abs(metric)
        key = (str(pid or name), round(signed, 2))
        if key in seen:
            continue
        seen.add(key)

        rows.append({
            "id": str(pid or name),
            "name": str(name),
            "prediction_pct": signed,
            "source_field": str(metric_key),
        })

    return rows


def _extract_json_scripts(html):
    payloads = []
    # Standard application/json script payloads (Next.js and similar apps).
    for raw in re.findall(
        r'<script[^>]+type=["\']application/json["\'][^>]*>(.*?)</script>',
        html,
        flags=re.I | re.S,
    ):
        raw = raw.strip()
        if not raw:
            continue
        try:
            payloads.append(json.loads(raw))
        except Exception:
            continue

    # __NEXT_DATA__ is also plain JSON when present.
    m = re.search(
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        html,
        flags=re.I | re.S,
    )
    if m:
        try:
            payloads.append(json.loads(m.group(1)))
        except Exception:
            pass
    return payloads


def fetch_official_price_predictions(bootstrap):
    """
    Official-first only.

    Optional env FPL_PRICE_PREDICTOR_URL can point at the actual public JSON feed
    used by the official page if/when its undocumented endpoint is known.
    Otherwise we inspect JSON embedded in the official Price Changes page.

    Crucially: there is NO LiveFPL fallback here. Wrong/late predictions are worse
    than skipping one prediction tweet.
    """
    custom_url = os.getenv("FPL_PRICE_PREDICTOR_URL", "").strip()
    if custom_url:
        payload = fetch_json(custom_url)
        rows = normalize_official_predictor_payload(payload, bootstrap)
        if rows:
            return rows
        raise RuntimeError("Official predictor URL returned no recognised prediction fields")

    html = fetch_text(FPL_PRICE_CHANGES_PAGE)
    for payload in _extract_json_scripts(html):
        rows = normalize_official_predictor_payload(payload, bootstrap)
        if rows:
            return rows

    raise RuntimeError(
        "Official FPL predictor data was not exposed in a readable JSON payload; "
        "prediction tweet skipped (no third-party fallback)."
    )


def _fit_lines(header_lines, candidate_lines, footer_lines, max_chars=X_MAX_CHARS):
    lines = list(header_lines)
    for line in candidate_lines:
        trial = "\n".join(lines + [line] + footer_lines)
        if len(trial) <= max_chars:
            lines.append(line)
        else:
            break
    return "\n".join(lines + footer_lines)


def format_daily_prediction_post(predictions):
    risers = sorted(
        [p for p in predictions if p["prediction_pct"] >= PRICE_PREDICTION_THRESHOLD_PCT],
        key=lambda p: p["prediction_pct"],
        reverse=True,
    )
    fallers = sorted(
        [p for p in predictions if p["prediction_pct"] <= -PRICE_PREDICTION_THRESHOLD_PCT],
        key=lambda p: p["prediction_pct"],
    )

    # One compact tweet only. Keep both directions visible whenever both exist.
    r_count = min(5, len(risers))
    f_count = min(5, len(fallers))

    def build(rc, fc):
        lines = ["🚨 توقعات تغيّر الأسعار", ""]
        if rc:
            txt = " • ".join(
                f"{p['name']} {abs(p['prediction_pct']):.0f}%"
                for p in risers[:rc]
            )
            lines.append(f"⬆️ احتمال ارتفاع: {txt}")
        if fc:
            txt = " • ".join(
                f"{p['name']} {abs(p['prediction_pct']):.0f}%"
                for p in fallers[:fc]
            )
            lines.append(f"⬇️ احتمال انخفاض: {txt}")
        lines += ["", "#FPL", "#فانتزي_البريميرليغ"]
        return "\n".join(lines)

    post = build(r_count, f_count)
    while len(post) > X_MAX_CHARS and (r_count > 1 or f_count > 1):
        if r_count >= f_count and r_count > 1:
            r_count -= 1
        elif f_count > 1:
            f_count -= 1
        post = build(r_count, f_count)

    return post[:X_MAX_CHARS]

def maybe_post_daily_price_prediction(bootstrap, state, now_utc=None):
    now_utc = now_utc or datetime.now(timezone.utc)
    now_ksa = now_utc.astimezone(ZoneInfo("Asia/Riyadh"))
    date_key = now_ksa.date().isoformat()

    # One scheduled prediction post per Saudi day, during the 15-minute run window.
    if not (
        now_ksa.hour == PRICE_PREDICTION_HOUR_KSA
        and PRICE_PREDICTION_MINUTE_KSA <= now_ksa.minute < PRICE_PREDICTION_MINUTE_KSA + 15
    ):
        return

    if state.get("daily_prediction_date") == date_key:
        return

    predictions = fetch_official_price_predictions(bootstrap)
    qualified = [
        p for p in predictions
        if abs(p["prediction_pct"]) >= PRICE_PREDICTION_THRESHOLD_PCT
    ]

    if qualified:
        post_to_x(format_daily_prediction_post(qualified))
    else:
        print("Official predictor: no players at/above threshold")

    # Mark the window handled even if nobody qualified, preventing repeated retries.
    state["daily_prediction_date"] = date_key

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


def format_confirmed_posts(rises, falls):
    entries = []
    for name, old, new in rises:
        entries.append(f"⬆️ {name}: £{old:.1f}m → £{new:.1f}m")
    for name, old, new in falls:
        entries.append(f"⬇️ {name}: £{old:.1f}m → £{new:.1f}m")

    posts = []
    remaining = entries[:]
    while remaining:
        body = []
        for line in remaining:
            trial = "\n".join(
                ["✅ تغييرات أسعار مؤكدة", ""] + body + [line, "", "#FPL", "#فانتزي_البريميرليغ"]
            )
            if len(trial) <= X_MAX_CHARS:
                body.append(line)
            else:
                break

        if not body:
            # Defensive fallback for an unexpectedly long player name.
            body = [remaining[0][:120]]

        posts.append("\n".join(
            ["✅ تغييرات أسعار مؤكدة", ""] + body + ["", "#FPL", "#فانتزي_البريميرليغ"]
        ))
        remaining = remaining[len(body):]

    return posts


def check_and_post_confirmed_prices(bootstrap, state):
    new_prices = current_official_prices(bootstrap)
    old_prices = state.get("last_prices", {})

    changed = False
    if old_prices:
        rises, falls = find_confirmed_changes(old_prices, new_prices)
        if rises or falls:
            for post in format_confirmed_posts(rises, falls):
                post_to_x(post)

            # A real official update closes the previous predictor cycle completely.
            state["alerted_rise"] = {}
            state["alerted_fall"] = {}
            uk_date = datetime.now(timezone.utc).astimezone(
                ZoneInfo("Europe/London")
            ).date().isoformat()
            state["last_confirmed_price_date_uk"] = uk_date
            changed = True

    state["last_prices"] = new_prices
    return changed


def fast_poll_official_price_change(state, initial_bootstrap, now_utc=None):
    """
    Competitive-edge window: at midnight UK, poll the OFFICIAL bootstrap briefly
    inside the same GitHub Action run instead of waiting for the next 15-minute job.

    We only do this around 00:00 UK and stop immediately when a change appears.
    """
    now_utc = now_utc or datetime.now(timezone.utc)
    uk = now_utc.astimezone(ZoneInfo("Europe/London"))

    # The normal fetch already handles delayed GitHub runs. Fast polling is only
    # useful if this job actually starts close to midnight UK.
    if not (uk.hour == 0 and uk.minute <= 4):
        return initial_bootstrap

    uk_date = uk.date().isoformat()
    if state.get("last_confirmed_price_date_uk") == uk_date:
        return initial_bootstrap

    if check_and_post_confirmed_prices(initial_bootstrap, state):
        return initial_bootstrap

    # Up to ~3 minutes, every 15 seconds. This is only once per day.
    end_at = time.time() + 180
    latest = initial_bootstrap
    while time.time() < end_at:
        time.sleep(15)
        try:
            latest = fetch_official_fpl()
            if check_and_post_confirmed_prices(latest, state):
                print("Official price changes detected during fast poll")
                break
        except Exception as exc:
            print("Fast official price poll failed:", exc)

    return latest

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

        try:
            ownership = float(p.get("selected_by_percent") or 0)
        except Exception:
            ownership = 0.0

        aliases = {web_name, second, f"{first} {second}".strip()}
        aliases = {
            a for a in aliases
            if len(a) >= 4 and not a.isdigit()
        }

        players.append({
            "id": str(p.get("id")),
            "web_name": web_name,
            "team": team_name,
            "ownership": ownership,
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
    return title, text[:20000]


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


def current_target_gameweek(bootstrap):
    now_utc = datetime.now(timezone.utc)
    event = get_next_gameweek_deadline(bootstrap, now_utc)
    if event:
        return event["id"]
    finished = [e.get("id") for e in bootstrap.get("events", []) if e.get("finished")]
    return max(finished) if finished else ""


def analyze_news_with_openai(source, title, text, matched_players, gw_id):
    from openai import OpenAI

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    player_lines = "\n".join(
        f"- {p['web_name']} | {p['team']} | ownership {p['ownership']:.1f}%"
        for p in matched_players
    ) or "(No named current FPL player was detected locally.)"

    source_rule = (
        "This is an official source."
        if source.get("official")
        else (
            "This is a secondary FPL news source. Use only explicit attributed "
            "manager/club/competition statements. Never upgrade inference or rumour to fact."
        )
    )

    prompt = f"""
You are the Arabic news editor for OG FPL.

Your job is NOT to write generic football news. Extract only material information that
changes an FPL decision, and preserve the source's certainty exactly.

Source: {source['name']}
Source rule: {source_rule}
Target gameweek: GW{gw_id}

Current FPL players detected in the article:
{player_lines}

Article title:
{title}

Article text:
{text}

STRICT RULES:
1) Never invent. Never strengthen a claim.
2) "back in training" != "available". "will be assessed" != "out".
3) Ignore rumours, transfers, tactical opinions, praise and predicted line-ups.
4) Relevant statuses only: AVAILABLE, OUT, DOUBT, ASSESSMENT, TRAINING, INJURY,
   SUSPENDED, MINUTES, ROTATION.
5) For SUSPENDED, classify the reason ONLY if explicitly supported:
   RED_CARD, SECOND_YELLOW, YELLOW_ACCUMULATION, OTHER_CONFIRMED, UNKNOWN.
6) MINUTES:
   - Only for players with ownership >= {MINUTES_OWNERSHIP_THRESHOLD:.1f}%.
   - Only if exact minutes are explicit OR can be calculated exactly from a starting XI
     plus an explicit substitution minute/final whistle.
   - Never guess minutes.
7) PRESS CONFERENCE:
   - If this is a manager press conference / pre-match media briefing, return ONE compact
     conference_summary_ar covering EVERY material squad/fitness update in the article.
   - Do not cherry-pick only 2 or 3 players.
   - Compress wording instead of dropping relevant items.
   - Each line starts with ❌ ⚠️ ✅ or 🔄.
   - Max 6 lines and conference_summary_ar <= 185 characters total.
8) For non-conference updates, summary_ar must be concise natural Arabic, not literal.
9) No URLs, source line or hashtags in Arabic text.
10) manager_name should only be filled when clearly stated.
11) player must exactly match a detected web_name when one is available.

Return JSON only:
{{
  "article_type": "PRESS_CONFERENCE|MATCH_REPORT|TEAM_NEWS|OTHER",
  "manager_name": "",
  "conference_summary_ar": "",
  "updates": [
    {{
      "player": "exact web_name",
      "status": "INJURY",
      "summary_ar": "...",
      "suspension_reason": "UNKNOWN",
      "minutes": null
    }}
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
        return {}
    return data


def _source_team_name(source):
    name = source.get("name", "")
    aliases = {
        "AFC Bournemouth": "Bournemouth",
        "Manchester City": "Man City",
        "Manchester United": "Man Utd",
        "Newcastle United": "Newcastle",
        "Nottingham Forest": "Nott'm Forest",
        "Tottenham Hotspur": "Spurs",
        "Coventry City": "Coventry",
        "Hull City": "Hull",
        "Ipswich Town": "Ipswich",
        "Leeds United": "Leeds",
    }
    return aliases.get(name, name)


def format_conference_post(source, manager_name, summary_ar, gw_id):
    team_name = team_ar(_source_team_name(source))
    manager = manager_name.strip()
    if manager:
        header = f"🚨⚽️ | {manager} [{team_name}] — GW{gw_id}:"
    else:
        header = f"🚨⚽️ | {team_name} — GW{gw_id}:"

    post = "\n".join([
        header,
        "",
        summary_ar.strip(),
        "",
        "#FPL",
        "#فانتزي_البريميرليغ",
    ])
    if len(post) > X_MAX_CHARS:
        # The model is instructed to stay short; this is a final safety guard.
        allowed = X_MAX_CHARS - len(header) - len("\n\n\n#FPL\n#فانتزي_البريميرليغ")
        compact = summary_ar.strip()[:max(80, allowed - 1)].rstrip() + "…"
        post = "\n".join([header, "", compact, "", "#FPL", "#فانتزي_البريميرليغ"])
    return post


def format_news_post(team_name, summary_ar):
    post = "\n".join([
        f"🚨 تحديث | {team_ar(team_name)}",
        "",
        summary_ar.strip(),
        "",
        "#FPL",
        "#فانتزي_البريميرليغ",
    ])
    return post[:X_MAX_CHARS]


def format_minutes_post(player, minutes):
    if int(minutes) == 90:
        body = f"⏱️ {player} أكمل 90 دقيقة اليوم. ✅"
    else:
        body = f"⏱️ {player} لعب {int(minutes)} دقيقة اليوم."
    return "\n".join([
        body,
        "",
        "#FPL",
        "#فانتزي_البريميرليغ",
    ])


def format_suspension_post(player, reason):
    reason_text = {
        "RED_CARD": "بطاقة حمراء مباشرة 🟥",
        "SECOND_YELLOW": "بطاقتين صفراوين في المباراة 🟨🟨",
        "YELLOW_ACCUMULATION": "تراكم البطاقات الصفراء 🟨",
        "OTHER_CONFIRMED": "سبب إيقاف مؤكد",
    }.get(reason)

    if not reason_text:
        return None

    return "\n".join([
        f"🚨 {player} سيغيب عن المباراة القادمة بسبب الإيقاف.",
        f"السبب: {reason_text}.",
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


def _candidate_priority(source, item):
    t = f"{item.get('title', '')} {item.get('url', '')}".casefold()
    score = 0

    if source.get("official"):
        score += 20
    if source["name"] not in {
        "Premier League", "UEFA Champions League",
        "UEFA Europa League", "UEFA Conference League"
    } and source.get("official"):
        score += 25  # Official club source.

    if any(x in t for x in ("press conference", "press-conference", "pressconference")):
        score += 100
    if any(x in t for x in ("team news", "fitness", "injury", "availability")):
        score += 80
    if any(x in t for x in ("match report", "report", "line-up", "lineup")):
        score += 50
    if source["name"] == "Fantasy Football Scout":
        score += 10

    return score


def _is_press_conference_hint(title, url):
    t = f"{title} {url}".casefold()
    return any(x in t for x in (
        "press conference", "press-conference", "pressconference",
        "media briefing", "pre-match press", "fitness update", "team news"
    ))


def _is_match_report_hint(title, url):
    t = f"{title} {url}".casefold()
    return any(x in t for x in (
        "match report", "match-report", "/report", "full-time",
        "full time", "match centre", "match-center"
    ))


def scan_news(bootstrap, state):
    """
    v3 news engine:
    - discovers all sources first;
    - prioritises official club press conferences/team news;
    - one press conference => one complete compact tweet;
    - minutes only for >=5% owned players;
    - suspension posts name the confirmed reason.
    """
    if "OPENAI_API_KEY" not in os.environ:
        print("News skipped: OPENAI_API_KEY is missing")
        return

    now_utc = datetime.now(timezone.utc)
    cleanup_recent_updates(state, now_utc)

    players = build_player_index(bootstrap)
    player_lookup = {p["web_name"]: p for p in players}
    gw_id = current_target_gameweek(bootstrap)

    news_seen = state.setdefault("news_seen", {})
    candidates = []
    discovered_by_source = {}
    upgrading_news_logic = int(state.get("news_logic_version", 2) or 2) < 3

    # Discover EVERY source before spending any AI calls. This prevents early sources
    # from consuming the entire run and starving a later Man Utd/Leeds conference.
    for source in NEWS_SOURCES:
        try:
            discovered = discover_source_links(source)
        except Exception as exc:
            print(f"News source failed [{source['name']}]: {exc}")
            continue

        source_key = source["name"]
        old_seen = set(news_seen.get(source_key, []))
        current_urls = [x["url"] for x in discovered]
        discovered_by_source[source_key] = (current_urls, old_seen)

        if source_key not in news_seen:
            news_seen[source_key] = current_urls[:100]
            print(f"News baseline created: {source_key} ({len(current_urls)} links)")
            continue

        new_items = [x for x in discovered if x["url"] not in old_seen]

        # One-time v3 recovery for the two sources that exposed the old logic bug.
        # Re-check only the very latest links; the AI still rejects old/irrelevant news.
        if upgrading_news_logic and source_key in {"Manchester United", "Leeds United"}:
            recovery = discovered[:4]
            by_url = {x["url"]: x for x in recovery + new_items}
            new_items = list(by_url.values())

        for item in new_items[:10]:
            candidates.append((
                _candidate_priority(source, item),
                source,
                item,
            ))

    candidates.sort(key=lambda x: x[0], reverse=True)

    ai_calls = 0
    tweets = 0
    processed_urls = set()

    for _, source, item in candidates:
        if ai_calls >= MAX_NEWS_AI_CALLS_PER_RUN or tweets >= MAX_NEWS_TWEETS_PER_RUN:
            break

        url = item["url"]
        source_key = source["name"]
        old_seen = discovered_by_source[source_key][1]

        try:
            title, article = article_text(url)
        except Exception as exc:
            print(f"Article fetch failed [{url}]: {exc}")
            continue

        combined = f"{item.get('title', '')} {title} {article}"
        matched = match_current_players(combined, players)
        hint_title = title or item.get("title", "")
        press_hint = _is_press_conference_hint(hint_title, url)
        match_hint = _is_match_report_hint(hint_title, url)

        # Conferences and match reports are analysed even when generic keyword
        # filtering would miss them. Match reports are needed for exact minutes.
        if not (press_hint or match_hint) and (not matched or not looks_fpl_relevant(combined)):
            old_seen.add(url)
            processed_urls.add(url)
            continue

        try:
            result = analyze_news_with_openai(
                source,
                title or item.get("title", ""),
                article,
                matched,
                gw_id,
            )
            ai_calls += 1
        except Exception as exc:
            print(f"OpenAI news analysis failed [{url}]: {exc}")
            continue

        article_type = str(result.get("article_type") or "").strip().upper()
        manager_name = str(result.get("manager_name") or "").strip()
        conference_summary = str(result.get("conference_summary_ar") or "").strip()

        # ONE conference article => ONE conference summary tweet.
        if article_type == "PRESS_CONFERENCE" and conference_summary:
            conference_key = f"CONF|{normalize_url(url)}"
            if not is_recent_duplicate(state, conference_key, "PRESS_CONFERENCE", now_utc):
                post_to_x(format_conference_post(
                    source, manager_name, conference_summary, gw_id
                ))
                mark_recent_update(
                    state, conference_key, "PRESS_CONFERENCE", now_utc
                )
                tweets += 1

        updates = result.get("updates", [])
        if not isinstance(updates, list):
            updates = []

        allowed_players = {p["web_name"] for p in matched}

        # For a press conference the complete summary is the public output. Do not
        # immediately repeat each bullet as separate news tweets.
        if article_type != "PRESS_CONFERENCE":
            for update in updates[:8]:
                if tweets >= MAX_NEWS_TWEETS_PER_RUN:
                    break

                player = str(update.get("player") or "").strip()
                status = str(update.get("status") or "").strip().upper()
                summary_ar = str(update.get("summary_ar") or "").strip()
                suspension_reason = str(
                    update.get("suspension_reason") or "UNKNOWN"
                ).strip().upper()
                minutes = update.get("minutes")

                if player not in allowed_players:
                    continue
                if status not in ALLOWED_STATUS:
                    continue

                p = player_lookup.get(player)
                if not p:
                    continue

                if is_recent_duplicate(state, player, status, now_utc):
                    continue

                post = None

                if status == "MINUTES":
                    if p["ownership"] < MINUTES_OWNERSHIP_THRESHOLD:
                        continue
                    try:
                        minute_value = int(minutes)
                    except Exception:
                        continue
                    if not (1 <= minute_value <= 120):
                        continue
                    post = format_minutes_post(player, minute_value)

                elif status == "SUSPENDED":
                    if suspension_reason not in SUSPENSION_REASONS:
                        suspension_reason = "UNKNOWN"
                    # User wants the cause stated. If the article doesn't prove it,
                    # skip rather than publish a vague suspension tweet.
                    if suspension_reason == "UNKNOWN":
                        continue
                    post = format_suspension_post(player, suspension_reason)

                else:
                    if not summary_ar or len(summary_ar) > 220:
                        continue
                    post = format_news_post(p["team"], summary_ar)

                if post:
                    post_to_x(post)
                    mark_recent_update(state, player, status, now_utc)
                    tweets += 1

        old_seen.add(url)
        processed_urls.add(url)

    # Persist bounded URL history for every source discovered this run.
    for source in NEWS_SOURCES:
        source_key = source["name"]
        if source_key not in discovered_by_source:
            continue
        current_urls, old_seen = discovered_by_source[source_key]
        merged = current_urls + list(old_seen)
        deduped = []
        seen_local = set()
        for u in merged:
            if u not in seen_local:
                seen_local.add(u)
                deduped.append(u)
        news_seen[source_key] = deduped[:140]

    state["news_logic_version"] = 3
    print(
        f"News scan complete: candidates={len(candidates)}, "
        f"AI calls={ai_calls}, tweets={tweets}"
    )

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
    now_utc = datetime.now(timezone.utc)

    # Official FPL is canonical for players, ownership, deadlines and actual prices.
    bootstrap = fetch_official_fpl()

    # 1) Actual official price changes first. Around midnight UK, briefly fast-poll
    # the official feed so the account does not wait for the next 15-minute job.
    try:
        bootstrap = fast_poll_official_price_change(
            state, bootstrap, now_utc=now_utc
        )
        # Outside the fast window, this normal comparison is enough.
        check_and_post_confirmed_prices(bootstrap, state)
    except Exception as exc:
        print("Confirmed prices failed:", exc)

    # 2) One daily predictor tweet at 23:30 KSA, official predictor only.
    try:
        maybe_post_daily_price_prediction(
            bootstrap, state, now_utc=now_utc
        )
    except Exception as exc:
        print("Official price predictor skipped:", exc)

    # 3) Deadline alerts
    try:
        maybe_post_deadline_alerts(bootstrap, state, now_utc=now_utc)
    except Exception as exc:
        print("Deadline alerts failed:", exc)

    # 4) FPL-relevant news, complete conference summaries, minutes and suspensions.
    try:
        scan_news(bootstrap, state)
    except Exception as exc:
        # News issues must never stop the price/deadline bot.
        print("News scan failed:", exc)

    save_state(state)


if __name__ == "__main__":
    run_once()
