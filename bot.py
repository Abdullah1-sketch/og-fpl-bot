import os
import json
import time
from pathlib import Path
from datetime import datetime, timezone

import requests
from requests_oauthlib import OAuth1
from dotenv import load_dotenv
load_dotenv()
LIVEFPL_PRICES_URL = "https://livefpl.us/api/prices.json"
FPL_BOOTSTRAP_URL = "https://fantasy.premierleague.com/api/bootstrap-static/"
X_POST_URL = "https://api.x.com/2/tweets"

STATE_FILE = Path("state.json")

# We alert once when a player crosses this predicted threshold.
# 0.90 = 90% of the predicted price-change threshold.
ALERT_THRESHOLD = float(os.getenv("ALERT_THRESHOLD", "0.90"))

# Keep posting disabled while testing.
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {
        "alerted_rise": {},
        "alerted_fall": {},
        "last_prices": {}
    }


def save_state(state):
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def fetch_livefpl_predictions():
    r = requests.get(
        LIVEFPL_PRICES_URL,
        timeout=20,
        headers={"User-Agent": "OG-FPL-Bot/1.0"}
    )
    r.raise_for_status()
    return r.json()


def fetch_official_fpl():
    r = requests.get(
        FPL_BOOTSTRAP_URL,
        timeout=20,
        headers={"User-Agent": "OG-FPL-Bot/1.0"}
    )
    r.raise_for_status()
    return r.json()


def normalize_livefpl(data):
    """
    Returns a list of:
    {
      id, name, team, cost,
      progress, prediction, per_hour
    }
    """
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
    lines.append("النسب توقعية وليست تغييرًا مؤكدًا.")
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

        old = old_prices[pid]
        old_price = float(old["price"])
        new_price = float(current["price"])

        if new_price > old_price:
            rises.append((current["name"], old_price, new_price))
        elif new_price < old_price:
            falls.append((current["name"], old_price, new_price))

    return rises, falls


def format_confirmed_post(rises, falls):
    lines = ["✅ تغييرات أسعار مؤكدة #FPL\n#فانتزي_البريميرليغ #فانتزي_البريميرليغ #فانتزي_البريميرليغ"]

    for name, old, new in rises[:10]:
        lines.append(f"⬆️ {name}: £{old:.1f}m → £{new:.1f}m")

    for name, old, new in falls[:10]:
        lines.append(f"⬇️ {name}: £{old:.1f}m → £{new:.1f}m")

    return "\n".join(lines)


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

    # 1) Predictor alerts
    live = normalize_livefpl(fetch_livefpl_predictions())

    risers = [
        p for p in live
        if p["prediction"] >= ALERT_THRESHOLD
        and p["id"] not in state["alerted_rise"]
    ]
    fallers = [
        p for p in live
        if p["prediction"] <= -ALERT_THRESHOLD
        and p["id"] not in state["alerted_fall"]
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

    # 2) Confirm actual price changes using official FPL data
    bootstrap = fetch_official_fpl()
    new_prices = current_official_prices(bootstrap)
    old_prices = state.get("last_prices", {})

    if old_prices:
        rises, falls = find_confirmed_changes(old_prices, new_prices)
        if rises or falls:
            post_to_x(format_confirmed_post(rises, falls))

            # Reset alerts for players whose price actually changed
            changed_names = {x[0] for x in rises + falls}
            state["alerted_rise"] = {
                pid: v for pid, v in state["alerted_rise"].items()
                if v.get("name") not in changed_names
            }
            state["alerted_fall"] = {
                pid: v for pid, v in state["alerted_fall"].items()
                if v.get("name") not in changed_names
            }

    state["last_prices"] = new_prices
    save_state(state)


if __name__ == "__main__":
    run_once()
