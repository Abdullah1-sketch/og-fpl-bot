import os
import json
import re
import hashlib
from pathlib import Path
from datetime import datetime, timezone, timedelta
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
import xml.etree.ElementTree as ET

import requests
from requests_oauthlib import OAuth1
from dotenv import load_dotenv

load_dotenv()

LIVEFPL_PRICES_URL = "https://livefpl.us/api/prices.json"
FPL_BOOTSTRAP_URL = "https://fantasy.premierleague.com/api/bootstrap-static/"
X_POST_URL = "https://api.x.com/2/tweets"
STATE_FILE = Path("state.json")

ALERT_THRESHOLD = float(os.getenv("ALERT_THRESHOLD", "0.90"))
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"

# Keep API use controlled.
MAX_NEWS_AI_CALLS_PER_RUN = int(os.getenv("MAX_NEWS_AI_CALLS_PER_RUN", "6"))
MAX_NEWS_TWEETS_PER_RUN = int(os.getenv("MAX_NEWS_TWEETS_PER_RUN", "4"))

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


def fetch_livefpl_predictions():
    return fetch_json(LIVEFPL_PRICES_URL)


def fetch_official_fpl():
    return fetch_json(FPL_BOOTSTRAP_URL)


def normalize_livefpl(data):
    rows = []
    items = data.items() if isinstance(data, dict) else enumerate(data)

    for key, p in items:
        try:
            player_id = str(p.get("id", key))
            progress = float(p.get("progress", 0) or 0)
            prediction = float(p.get("progress_tonight", 0) or 0)
            per_hour = float(p.get("per_hour", 0) or 0)
            cost = p.get("cost")
            name = p.get("name") or p.get("web_name") or f"Player {player_id}"
            team = p.get("team") or ""
        except Exception:
            continue

        rows.append({
            "id": player_id,
            "name": str(name),
            "team": str(team),
            "cost": cost,
            "progress": progress,
            "prediction": prediction,
            "per_hour": per_hour,
        })

    return rows


def format_prediction_post(direction, players):
    arrow = "⬆️" if direction == "rise" else "⬇️"
    word = "ارتفاع" if direction == "rise" else "انخفاض"

    lines = ["🚨 تنبيه أسعار", f"{arrow} احتمال {word} قوي:"]

    for p in players[:6]:
        pct = abs(p["prediction"]) * 100
        lines.append(f"{arrow} {p['name']} — {pct:.0f}%")

    lines.append("")
    lines.append("#FPL")
    lines.append("#فانتزي_البريميرليغ")
    return "\n".join(lines)


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

    for name, old, new in rises[:10]:
        lines.append(f"⬆️ {name}: £{old:.1f}m → £{new:.1f}m")

    for name, old, new in falls[:10]:
        lines.append(f"⬇️ {name}: £{old:.1f}m → £{new:.1f}m")

    lines.append("")
    lines.append("#FPL")
    lines.append("#فانتزي_البريميرليغ")
    return "\n".join(lines)


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
            "web_name": web_name,
            "team": team_name,
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
    return title, text[:12000]


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


def analyze_news_with_openai(source, title, text, matched_players):
    from openai import OpenAI

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    player_lines = "\n".join(
        f"- {p['web_name']} | {p['team']}"
        for p in matched_players
    )

    source_rule = (
        "This is an official source."
        if source.get("official")
        else (
            "This is a fast FPL news source, not an official club source. "
            "Only use explicit attributed manager/club statements in the article. "
            "Do not treat the writer's inference or a rumour as fact."
        )
    )

    prompt = f"""
You are the Arabic news editor for an FPL account.

Goal: extract ONLY material updates that affect Fantasy Premier League decisions
for CURRENT Premier League men's first-team players.

Source: {source['name']}
Source rule: {source_rule}

Current players detected in the article:
{player_lines}

Article title:
{title}

Article text:
{text}

Rules:
1) Never invent or infer a stronger claim than the source.
2) Preserve uncertainty exactly:
   - "doubt / could / may / assess" must remain uncertain.
   - "back in training" does NOT mean "available".
   - "expected to be available" does NOT mean "will start".
3) Ignore rumours, transfer speculation, opinions, match praise, tactics with no
   FPL availability/minutes impact, women's/academy news, and old/historical news.
4) Only include: availability, confirmed absence, doubt, assessment, return to
   training, injury, suspension, explicit minutes restriction, or explicit
   rotation/rest information.
5) Write natural concise Arabic as a human football-news account would write.
   Do not translate word-for-word.
6) Do NOT include a source line, URL, hashtags, emojis, or the club name in
   summary_ar.
7) Keep summary_ar under 170 Arabic characters.
8) player must EXACTLY match one of the detected web_name values above.
9) status must be one of:
   AVAILABLE, OUT, DOUBT, ASSESSMENT, TRAINING, INJURY, SUSPENDED, MINUTES, ROTATION.
10) Maximum 3 updates. If nothing qualifies, return an empty list.

Return JSON only:
{{"updates":[{{"player":"exact web_name","status":"DOUBT","summary_ar":"..."}}]}}
"""

    response = client.responses.create(
        model="gpt-5.6-terra",
        input=prompt,
        reasoning={"effort": "none"},
        max_output_tokens=500,
        text={"verbosity": "low"},
    )

    data = parse_json_object(response.output_text)
    updates = data.get("updates", [])
    if not isinstance(updates, list):
        return []
    return updates[:3]


def format_news_post(team_name, summary_ar):
    return "\n".join([
        f"🚨 تحديث | {team_ar(team_name)}",
        "",
        summary_ar.strip(),
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


def scan_news(bootstrap, state):
    """
    Poll source pages directly. OpenAI is called ONLY when a new article:
    - mentions a current FPL player, and
    - contains FPL-relevant availability/minutes language.

    The first time a source is seen, it is baselined without posting old news.
    """
    if "OPENAI_API_KEY" not in os.environ:
        print("News skipped: OPENAI_API_KEY is missing")
        return

    now_utc = datetime.now(timezone.utc)
    cleanup_recent_updates(state, now_utc)

    players = build_player_index(bootstrap)
    player_lookup = {
        p["web_name"]: p
        for p in players
    }

    news_seen = state.setdefault("news_seen", {})
    ai_calls = 0
    tweets = 0

    # The first run after enabling sitemap fallback is baseline-only.
    # This prevents newly discoverable older club articles from being tweeted.
    sitemap_migration_baseline = not state.get("sitemap_fallback_baselined", False)

    for source in NEWS_SOURCES:
        if ai_calls >= MAX_NEWS_AI_CALLS_PER_RUN or tweets >= MAX_NEWS_TWEETS_PER_RUN:
            break

        try:
            discovered = discover_source_links(source)
        except Exception as exc:
            print(f"News source failed [{source['name']}]: {exc}")
            continue

        source_key = source["name"]
        old_seen = set(news_seen.get(source_key, []))
        current_urls = [x["url"] for x in discovered]

        # First scan (or first run after the sitemap upgrade): establish a
        # baseline and never post older/current articles.
        if source_key not in news_seen or sitemap_migration_baseline:
            news_seen[source_key] = current_urls[:80]
            print(f"News baseline created: {source_key} ({len(current_urls)} links)")
            continue

        new_items = [x for x in discovered if x["url"] not in old_seen]

        # Process oldest-to-newest within the newly discovered batch.
        for item in reversed(new_items[:10]):
            if ai_calls >= MAX_NEWS_AI_CALLS_PER_RUN or tweets >= MAX_NEWS_TWEETS_PER_RUN:
                break

            url = item["url"]

            try:
                title, text = article_text(url)
            except Exception as exc:
                print(f"Article fetch failed [{url}]: {exc}")
                # Keep it unseen so a temporary failure can retry next run.
                continue

            combined = f"{item.get('title', '')} {title} {text}"
            matched = match_current_players(combined, players)

            # Local filter prevents wasteful model calls.
            if not matched or not looks_fpl_relevant(combined):
                old_seen.add(url)
                continue

            try:
                updates = analyze_news_with_openai(
                    source, title or item.get("title", ""), text, matched
                )
                ai_calls += 1
            except Exception as exc:
                print(f"OpenAI news analysis failed [{url}]: {exc}")
                # Retry later rather than losing a potentially important update.
                continue

            allowed_players = {p["web_name"] for p in matched}

            for update in updates:
                player = str(update.get("player") or "").strip()
                status = str(update.get("status") or "").strip().upper()
                summary_ar = str(update.get("summary_ar") or "").strip()

                if player not in allowed_players:
                    continue
                if status not in ALLOWED_STATUS:
                    continue
                if not summary_ar or len(summary_ar) > 220:
                    continue
                if is_recent_duplicate(state, player, status, now_utc):
                    continue

                p = player_lookup.get(player)
                if not p:
                    continue

                post_to_x(format_news_post(p["team"], summary_ar))
                mark_recent_update(state, player, status, now_utc)
                tweets += 1

                if tweets >= MAX_NEWS_TWEETS_PER_RUN:
                    break

            old_seen.add(url)

        # Keep a bounded history per source.
        merged = current_urls + list(old_seen)
        deduped = []
        seen_local = set()
        for u in merged:
            if u not in seen_local:
                seen_local.add(u)
                deduped.append(u)
        news_seen[source_key] = deduped[:120]

    if sitemap_migration_baseline:
        state["sitemap_fallback_baselined"] = True

    print(f"News scan complete: AI calls={ai_calls}, tweets={tweets}")


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

    # 1) Price predictor alerts
    try:
        live = normalize_livefpl(fetch_livefpl_predictions())

        risers = [
            p for p in live
            if p["prediction"] >= ALERT_THRESHOLD
            and p["id"] not in state.setdefault("alerted_rise", {})
        ]
        fallers = [
            p for p in live
            if p["prediction"] <= -ALERT_THRESHOLD
            and p["id"] not in state.setdefault("alerted_fall", {})
        ]

        risers.sort(key=lambda p: p["prediction"], reverse=True)
        fallers.sort(key=lambda p: p["prediction"])

        if risers:
            post_to_x(format_prediction_post("rise", risers))
            for p in risers:
                state["alerted_rise"][p["id"]] = {
                    "name": p["name"],
                    "prediction": p["prediction"]
                }

        if fallers:
            post_to_x(format_prediction_post("fall", fallers))
            for p in fallers:
                state["alerted_fall"][p["id"]] = {
                    "name": p["name"],
                    "prediction": p["prediction"]
                }
    except Exception as exc:
        print("Price predictor failed:", exc)

    # Official FPL data is also the source for current players/teams/deadlines.
    bootstrap = fetch_official_fpl()

    # 2) Deadline alerts
    try:
        maybe_post_deadline_alerts(bootstrap, state)
    except Exception as exc:
        print("Deadline alerts failed:", exc)

    # 3) Confirm actual price changes
    try:
        new_prices = current_official_prices(bootstrap)
        old_prices = state.get("last_prices", {})

        if old_prices:
            rises, falls = find_confirmed_changes(old_prices, new_prices)
            if rises or falls:
                post_to_x(format_confirmed_post(rises, falls))

                changed_names = {x[0] for x in rises + falls}
                state["alerted_rise"] = {
                    pid: v for pid, v in state.setdefault("alerted_rise", {}).items()
                    if v.get("name") not in changed_names
                }
                state["alerted_fall"] = {
                    pid: v for pid, v in state.setdefault("alerted_fall", {}).items()
                    if v.get("name") not in changed_names
                }

        state["last_prices"] = new_prices
    except Exception as exc:
        print("Confirmed prices failed:", exc)

    # 4) FPL-relevant news + careful Arabic rewriting
    try:
        scan_news(bootstrap, state)
    except Exception as exc:
        # News issues must never stop the price/deadline bot.
        print("News scan failed:", exc)

    save_state(state)


if __name__ == "__main__":
    run_once()
