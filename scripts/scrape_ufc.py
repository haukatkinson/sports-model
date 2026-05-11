from __future__ import annotations

import argparse
import os
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
import requests
from bs4 import BeautifulSoup

try:
    import mysql.connector
    from mysql.connector.connection import MySQLConnection
except Exception:  # pragma: no cover
    mysql = None
    MySQLConnection = None

ROOT = Path(__file__).resolve().parents[1]
RAW_PATH = ROOT / "data" / "raw_fights.csv"
NEAREST_EVENT_STATS_PATH = ROOT / "data" / "nearest_event_fights.csv"
UFC_EVENTS_URL = "http://ufcstats.com/statistics/events/completed?page=all"


@dataclass
class FighterState:
    fights: int = 0
    wins: int = 0
    losses: int = 0
    draws: int = 0
    win_streak: int = 0
    finish_wins: int = 0
    sig_landed: int = 0
    sig_attempted: int = 0
    td_landed: int = 0
    td_attempted: int = 0


def parse_of_value(value: str) -> Tuple[int, int]:
    if not value:
        return 0, 0
    match = re.search(r"(\d+)\s*of\s*(\d+)", value)
    if not match:
        return 0, 0
    return int(match.group(1)), int(match.group(2))


def parse_ctrl_to_seconds(value: str) -> int:
    text = value.strip()
    if not text or text == "--":
        return 0
    if ":" not in text:
        return 0
    minutes, seconds = text.split(":", 1)
    return int(minutes) * 60 + int(seconds)


def parse_reach_cm(value: str) -> float:
    text = value.strip()
    match = re.search(r"(\d+(?:\.\d+)?)\s*\"", text)
    if not match:
        return 0.0
    inches = float(match.group(1))
    return round(inches * 2.54, 2)


def parse_record_triplet(record_text: str) -> Tuple[int, int, int]:
    match = re.search(r"(\d+)-(\d+)-(\d+)", record_text)
    if not match:
        return 0, 0, 0
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def normalize_weight_class_name(raw_title: str) -> str:
    text = (raw_title or "").strip()
    text = re.sub(r"\s+Title\s+Bout", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+Interim\s+", " ", text, flags=re.IGNORECASE)
    text = text.replace("Bout", "").strip()

    aliases = {
        "women's strawweight": "Women's Strawweight",
        "women's flyweight": "Women's Flyweight",
        "women's bantamweight": "Women's Bantamweight",
        "women's featherweight": "Women's Featherweight",
        "strawweight": "Strawweight",
        "flyweight": "Flyweight",
        "bantamweight": "Bantamweight",
        "featherweight": "Featherweight",
        "lightweight": "Lightweight",
        "welterweight": "Welterweight",
        "middleweight": "Middleweight",
        "light heavyweight": "Light Heavyweight",
        "heavyweight": "Heavyweight",
        "catchweight": "Catchweight",
        "openweight": "Openweight",
    }
    lowered = text.lower()
    for key, value in aliases.items():
        if key in lowered:
            return value
    return "Catchweight"


def parse_date(text: str) -> Optional[date]:
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text.strip(), fmt).date()
        except ValueError:
            continue
    return None


def fetch_html(session: requests.Session, url: str) -> str:
    response = session.get(url, timeout=20)
    response.raise_for_status()
    return response.text


def get_completed_event_links(session: requests.Session) -> List[str]:
    html = fetch_html(session, UFC_EVENTS_URL)
    links = re.findall(r'href="(http://ufcstats.com/event-details/[^"]+)"', html)
    deduped = []
    seen = set()
    for link in links:
        if link in seen:
            continue
        seen.add(link)
        deduped.append(link)
    return deduped


def parse_event_details(session: requests.Session, event_url: str) -> Dict:
    html = fetch_html(session, event_url)
    soup = BeautifulSoup(html, "html.parser")

    title = soup.select_one("h2.b-content__title span")
    event_name = title.get_text(" ", strip=True) if title else "Unknown Event"

    event_date = None
    event_location = None
    for item in soup.select("li.b-list__box-list-item"):
        text = item.get_text(" ", strip=True)
        if text.startswith("Date:"):
            event_date = parse_date(text.replace("Date:", "", 1).strip())
        elif text.startswith("Location:"):
            event_location = text.replace("Location:", "", 1).strip()

    fight_links = []
    for row in soup.select("tr.b-fight-details__table-row"):
        link = row.get("data-link")
        if link and "fight-details" in link:
            fight_links.append(link)

    return {
        "event_name": event_name,
        "event_date": event_date,
        "event_location": event_location,
        "fight_links": fight_links,
    }


def parse_fighter_profile(session: requests.Session, fighter_url: str) -> Dict:
    html = fetch_html(session, fighter_url)
    soup = BeautifulSoup(html, "html.parser")

    result = {
        "stance": "",
        "reach_cm": 0.0,
        "date_of_birth": None,
        "record": (0, 0, 0),
    }

    for item in soup.select("li.b-list__box-list-item"):
        text = item.get_text(" ", strip=True)
        if text.startswith("Reach:"):
            result["reach_cm"] = parse_reach_cm(text.replace("Reach:", "", 1).strip())
        elif text.startswith("STANCE:"):
            result["stance"] = text.replace("STANCE:", "", 1).strip()
        elif text.startswith("DOB:"):
            result["date_of_birth"] = parse_date(text.replace("DOB:", "", 1).strip())
        elif text.startswith("Record:"):
            result["record"] = parse_record_triplet(text)

    return result


def parse_fight_details(session: requests.Session, fight_url: str) -> Optional[Dict]:
    html = fetch_html(session, fight_url)
    soup = BeautifulSoup(html, "html.parser")

    person_name_links = soup.select("a.b-link.b-fight-details__person-link")
    status_tags = soup.select("i.b-fight-details__person-status")
    if len(person_name_links) < 2:
        return None

    fighter_a_name = person_name_links[0].get_text(" ", strip=True)
    fighter_b_name = person_name_links[1].get_text(" ", strip=True)
    fighter_a_url = person_name_links[0].get("href", "")
    fighter_b_url = person_name_links[1].get("href", "")

    winner_name = None
    if len(status_tags) >= 2:
        s0 = status_tags[0].get_text(strip=True).upper()
        s1 = status_tags[1].get_text(strip=True).upper()
        if s0 == "W":
            winner_name = fighter_a_name
        elif s1 == "W":
            winner_name = fighter_b_name

    method = ""
    round_num = 0
    time_in_round = ""
    fight_title_node = soup.select_one("i.b-fight-details__fight-title")
    fight_title = fight_title_node.get_text(" ", strip=True) if fight_title_node else ""
    weight_class_name = normalize_weight_class_name(fight_title)
    is_title_fight = bool(re.search(r"title", fight_title, flags=re.IGNORECASE))
    for item in soup.select("i.b-fight-details__text-item"):
        text = item.get_text(" ", strip=True)
        if text.startswith("Method:"):
            method = text.replace("Method:", "", 1).strip()
        elif text.startswith("Round:"):
            round_text = text.replace("Round:", "", 1).strip()
            round_num = int(round_text) if round_text.isdigit() else 0
        elif text.startswith("Time:"):
            time_in_round = text.replace("Time:", "", 1).strip()

    rows = [row for row in soup.select("tr.b-fight-details__table-row") if len(row.select("td.b-fight-details__table-col")) >= 10]
    if not rows:
        return None
    stat_row = rows[0]

    stats = defaultdict(dict)
    td_cells = stat_row.select("td.b-fight-details__table-col")
    if len(td_cells) >= 10:
        kd_vals = [p.get_text(" ", strip=True) for p in td_cells[1].select("p")]
        sig_vals = [p.get_text(" ", strip=True) for p in td_cells[3].select("p")]
        td_vals = [p.get_text(" ", strip=True) for p in td_cells[5].select("p")]
        sub_vals = [p.get_text(" ", strip=True) for p in td_cells[7].select("p")]
        ctrl_vals = [p.get_text(" ", strip=True) for p in td_cells[9].select("p")]

        for idx, name in enumerate([fighter_a_name, fighter_b_name]):
            sig_land, sig_att = parse_of_value(sig_vals[idx] if idx < len(sig_vals) else "")
            td_land, td_att = parse_of_value(td_vals[idx] if idx < len(td_vals) else "")
            kd = int(kd_vals[idx]) if idx < len(kd_vals) and kd_vals[idx].isdigit() else 0
            sub = int(sub_vals[idx]) if idx < len(sub_vals) and sub_vals[idx].isdigit() else 0
            ctrl = parse_ctrl_to_seconds(ctrl_vals[idx] if idx < len(ctrl_vals) else "")
            stats[name] = {
                "sig_strikes_landed": sig_land,
                "sig_strikes_attempted": sig_att,
                "takedowns_landed": td_land,
                "takedowns_attempted": td_att,
                "submission_attempts": sub,
                "knockdowns": kd,
                "control_time_seconds": ctrl,
            }

    return {
        "fighterA": fighter_a_name,
        "fighterB": fighter_b_name,
        "fighterA_url": fighter_a_url,
        "fighterB_url": fighter_b_url,
        "winner": winner_name,
        "method": method,
        "round_num": round_num,
        "time_in_round": time_in_round,
        "weight_class_name": weight_class_name,
        "is_title_fight": is_title_fight,
        "stats": stats,
    }


def compute_age_on_date(born: Optional[date], when: Optional[date]) -> float:
    if not born or not when:
        return 0.0
    return round((when - born).days / 365.25, 2)


def make_feature_rows(fights: List[Dict], fighter_profiles: Dict[str, Dict]) -> pd.DataFrame:
    states: Dict[str, FighterState] = defaultdict(FighterState)
    rows = []

    sorted_fights = sorted(fights, key=lambda row: row.get("event_date") or date.min)
    for fight in sorted_fights:
        fighter_a = fight["fighterA"]
        fighter_b = fight["fighterB"]
        winner = fight.get("winner")
        if winner not in {fighter_a, fighter_b}:
            continue

        state_a = states[fighter_a]
        state_b = states[fighter_b]
        profile_a = fighter_profiles.get(fighter_a, {})
        profile_b = fighter_profiles.get(fighter_b, {})

        acc_a = (state_a.sig_landed / state_a.sig_attempted) if state_a.sig_attempted else 0.0
        acc_b = (state_b.sig_landed / state_b.sig_attempted) if state_b.sig_attempted else 0.0
        td_a = (state_a.td_landed / state_a.td_attempted) if state_a.td_attempted else 0.0
        td_b = (state_b.td_landed / state_b.td_attempted) if state_b.td_attempted else 0.0
        finish_rate_a = (state_a.finish_wins / state_a.wins) if state_a.wins else 0.0
        finish_rate_b = (state_b.finish_wins / state_b.wins) if state_b.wins else 0.0

        rows.append(
            {
                "fighterA": fighter_a,
                "fighterB": fighter_b,
                "strike_diff": round(acc_a - acc_b, 6),
                "takedown_diff": round(td_a - td_b, 6),
                "reach_diff": round(float(profile_a.get("reach_cm", 0.0)) - float(profile_b.get("reach_cm", 0.0)), 6),
                "win_streak_diff": state_a.win_streak - state_b.win_streak,
                "age_diff": round(
                    compute_age_on_date(profile_a.get("date_of_birth"), fight.get("event_date"))
                    - compute_age_on_date(profile_b.get("date_of_birth"), fight.get("event_date")),
                    6,
                ),
                "experience_diff": state_a.fights - state_b.fights,
                "finish_rate_diff": round(finish_rate_a - finish_rate_b, 6),
                "winner": winner,
            }
        )

        stats_a = fight.get("stats", {}).get(fighter_a, {})
        stats_b = fight.get("stats", {}).get(fighter_b, {})
        for name, state, stats in (
            (fighter_a, state_a, stats_a),
            (fighter_b, state_b, stats_b),
        ):
            state.fights += 1
            state.sig_landed += int(stats.get("sig_strikes_landed", 0))
            state.sig_attempted += int(stats.get("sig_strikes_attempted", 0))
            state.td_landed += int(stats.get("takedowns_landed", 0))
            state.td_attempted += int(stats.get("takedowns_attempted", 0))

            if winner == name:
                state.wins += 1
                state.win_streak += 1
                if re.search(r"(KO|TKO|Submission)", fight.get("method", ""), flags=re.IGNORECASE):
                    state.finish_wins += 1
            elif winner:
                state.losses += 1
                state.win_streak = 0
            else:
                state.draws += 1

    return pd.DataFrame(rows)


def get_mysql_connection() -> Optional[MySQLConnection]:
    if mysql is None:
        return None
    host = os.getenv("DB_HOST", "localhost")
    user = os.getenv("DB_USER", "user")
    password = os.getenv("DB_PASS", "pass")
    database = os.getenv("DB_NAME", "ufc_db")
    try:
        return mysql.connector.connect(
            host=host,
            user=user,
            password=password,
            database=database,
        )
    except Exception:
        return None


def upsert_fighter(cursor, name: str, profile: Dict) -> int:
    wins, losses, draws = profile.get("record", (0, 0, 0))
    cursor.execute(
        """
        INSERT INTO fighters (name, stance, reach_cm, date_of_birth, wins, losses, draws)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            stance = VALUES(stance),
            reach_cm = VALUES(reach_cm),
            date_of_birth = VALUES(date_of_birth),
            wins = VALUES(wins),
            losses = VALUES(losses),
            draws = VALUES(draws)
        """,
        (
            name,
            profile.get("stance") or None,
            profile.get("reach_cm") or None,
            profile.get("date_of_birth"),
            wins,
            losses,
            draws,
        ),
    )
    cursor.execute("SELECT id FROM fighters WHERE name = %s", (name,))
    row = cursor.fetchone()
    return int(row[0])


def upsert_weight_class(cursor, name: Optional[str]) -> Optional[int]:
    if not name:
        return None
    cursor.execute(
        """
        INSERT INTO weight_classes (name)
        VALUES (%s)
        ON DUPLICATE KEY UPDATE name = VALUES(name)
        """,
        (name,),
    )
    cursor.execute("SELECT id FROM weight_classes WHERE name = %s", (name,))
    row = cursor.fetchone()
    return int(row[0]) if row else None


def upsert_event(cursor, name: str, event_date: date, location: Optional[str]) -> int:
    cursor.execute(
        """
        INSERT INTO events (name, event_date, location)
        VALUES (%s, %s, %s)
        ON DUPLICATE KEY UPDATE location = VALUES(location)
        """,
        (name, event_date, location),
    )
    cursor.execute(
        "SELECT id FROM events WHERE name = %s AND event_date = %s LIMIT 1",
        (name, event_date),
    )
    row = cursor.fetchone()
    return int(row[0])


def assign_fighter_weight_class(cursor, fighter_id: int, weight_class_id: Optional[int]) -> None:
    if not weight_class_id:
        return
    cursor.execute(
        "UPDATE fighters SET weight_class_id = %s WHERE id = %s",
        (weight_class_id, fighter_id),
    )


def get_or_create_fight(
    cursor,
    fight: Dict,
    event_id: int,
    weight_class_id: Optional[int],
    fighter_a_id: int,
    fighter_b_id: int,
    winner_id: Optional[int],
) -> int:
    cursor.execute(
        """
        SELECT id FROM fights
        WHERE event_name = %s AND event_date = %s AND fighter_a_id = %s AND fighter_b_id = %s
        LIMIT 1
        """,
        (fight["event_name"], fight["event_date"], fighter_a_id, fighter_b_id),
    )
    existing = cursor.fetchone()
    if existing:
        fight_id = int(existing[0])
        cursor.execute(
            """
            UPDATE fights
            SET event_id = %s, weight_class_id = %s, winner_id = %s, method = %s, round_num = %s, time_in_round = %s, is_title_fight = %s
            WHERE id = %s
            """,
            (
                event_id,
                weight_class_id,
                winner_id,
                fight.get("method"),
                fight.get("round_num"),
                fight.get("time_in_round"),
                1 if fight.get("is_title_fight") else 0,
                fight_id,
            ),
        )
        return fight_id

    cursor.execute(
        """
        INSERT INTO fights (
            event_id, event_name, event_date, weight_class_id,
            fighter_a_id, fighter_b_id, winner_id, method, round_num, time_in_round, is_title_fight
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            event_id,
            fight["event_name"],
            fight["event_date"],
            weight_class_id,
            fighter_a_id,
            fighter_b_id,
            winner_id,
            fight.get("method"),
            fight.get("round_num"),
            fight.get("time_in_round"),
            1 if fight.get("is_title_fight") else 0,
        ),
    )
    return int(cursor.lastrowid)


def upsert_fighter_stats(cursor, fight_id: int, fighter_id: int, stats: Dict) -> None:
    cursor.execute(
        """
        INSERT INTO fighter_stats (
            fighter_id, fight_id, sig_strikes_landed, sig_strikes_attempted,
            takedowns_landed, takedowns_attempted, submission_attempts,
            knockdowns, control_time_seconds
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            sig_strikes_landed = VALUES(sig_strikes_landed),
            sig_strikes_attempted = VALUES(sig_strikes_attempted),
            takedowns_landed = VALUES(takedowns_landed),
            takedowns_attempted = VALUES(takedowns_attempted),
            submission_attempts = VALUES(submission_attempts),
            knockdowns = VALUES(knockdowns),
            control_time_seconds = VALUES(control_time_seconds)
        """,
        (
            fighter_id,
            fight_id,
            int(stats.get("sig_strikes_landed", 0)),
            int(stats.get("sig_strikes_attempted", 0)),
            int(stats.get("takedowns_landed", 0)),
            int(stats.get("takedowns_attempted", 0)),
            int(stats.get("submission_attempts", 0)),
            int(stats.get("knockdowns", 0)),
            int(stats.get("control_time_seconds", 0)),
        ),
    )


def save_to_database(fights: List[Dict], fighter_profiles: Dict[str, Dict]) -> bool:
    conn = get_mysql_connection()
    if conn is None:
        print("MySQL not available or credentials invalid. Skipping DB write.")
        return False

    cursor = conn.cursor()
    try:
        for fight in fights:
            fighter_a = fight["fighterA"]
            fighter_b = fight["fighterB"]
            profile_a = fighter_profiles.get(fighter_a, {})
            profile_b = fighter_profiles.get(fighter_b, {})

            event_id = upsert_event(
                cursor,
                fight["event_name"],
                fight["event_date"],
                fight.get("event_location"),
            )
            weight_class_id = upsert_weight_class(cursor, fight.get("weight_class_name"))

            fighter_a_id = upsert_fighter(cursor, fighter_a, profile_a)
            fighter_b_id = upsert_fighter(cursor, fighter_b, profile_b)
            assign_fighter_weight_class(cursor, fighter_a_id, weight_class_id)
            assign_fighter_weight_class(cursor, fighter_b_id, weight_class_id)
            winner_name = fight.get("winner")
            winner_id = None
            if winner_name == fighter_a:
                winner_id = fighter_a_id
            elif winner_name == fighter_b:
                winner_id = fighter_b_id

            fight_id = get_or_create_fight(
                cursor,
                fight,
                event_id,
                weight_class_id,
                fighter_a_id,
                fighter_b_id,
                winner_id,
            )

            stats = fight.get("stats", {})
            upsert_fighter_stats(cursor, fight_id, fighter_a_id, stats.get(fighter_a, {}))
            upsert_fighter_stats(cursor, fight_id, fighter_b_id, stats.get(fighter_b, {}))

        conn.commit()
        return True
    finally:
        cursor.close()
        conn.close()


def normalize_fights(df: pd.DataFrame) -> pd.DataFrame:
    required_columns = [
        "fighterA",
        "fighterB",
        "strike_diff",
        "takedown_diff",
        "reach_diff",
        "win_streak_diff",
        "age_diff",
        "experience_diff",
        "finish_rate_diff",
        "winner",
    ]

    missing = [col for col in required_columns if col not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    normalized = df[required_columns].copy()
    normalized = normalized.dropna(subset=["fighterA", "fighterB", "winner"])
    return normalized


def get_nearest_completed_event(session: requests.Session) -> Optional[Dict]:
    today = date.today()
    for event_link in get_completed_event_links(session):
        event_data = parse_event_details(session, event_link)
        event_date = event_data.get("event_date")
        fight_links = event_data.get("fight_links") or []
        if event_date and event_date <= today and fight_links:
            event_data["event_link"] = event_link
            return event_data
    return None


def scrape_ufc() -> Tuple[List[Dict], Dict[str, Dict], Dict]:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        }
    )

    nearest_event = get_nearest_completed_event(session)
    if not nearest_event:
        return [], {}, {}

    fighter_profile_cache: Dict[str, Dict] = {}
    fights: List[Dict] = []

    event_name = nearest_event["event_name"]
    event_date = nearest_event["event_date"]
    event_location = nearest_event.get("event_location")
    for fight_link in nearest_event["fight_links"]:
        try:
            fight = parse_fight_details(session, fight_link)
        except Exception:
            continue
        if not fight:
            continue

        fight["event_name"] = event_name
        fight["event_date"] = event_date
        fight["event_location"] = event_location

        for fighter_name, fighter_url in (
            (fight["fighterA"], fight.get("fighterA_url", "")),
            (fight["fighterB"], fight.get("fighterB_url", "")),
        ):
            if fighter_name in fighter_profile_cache:
                continue
            if fighter_url:
                try:
                    fighter_profile_cache[fighter_name] = parse_fighter_profile(session, fighter_url)
                except Exception:
                    fighter_profile_cache[fighter_name] = {}
            else:
                fighter_profile_cache[fighter_name] = {}

        fights.append(fight)

    return fights, fighter_profile_cache, nearest_event


def build_nearest_event_stats_rows(fights: List[Dict]) -> pd.DataFrame:
    rows = []
    for fight in fights:
        fighter_a = fight["fighterA"]
        fighter_b = fight["fighterB"]
        stats = fight.get("stats", {})
        stats_a = stats.get(fighter_a, {})
        stats_b = stats.get(fighter_b, {})

        rows.append(
            {
                "event_name": fight.get("event_name"),
                "event_date": fight.get("event_date"),
                "fighterA": fighter_a,
                "fighterB": fighter_b,
                "winner": fight.get("winner"),
                "method": fight.get("method"),
                "round_num": fight.get("round_num"),
                "time_in_round": fight.get("time_in_round"),
                "fighterA_sig_strikes_landed": int(stats_a.get("sig_strikes_landed", 0)),
                "fighterA_sig_strikes_attempted": int(stats_a.get("sig_strikes_attempted", 0)),
                "fighterA_takedowns_landed": int(stats_a.get("takedowns_landed", 0)),
                "fighterA_takedowns_attempted": int(stats_a.get("takedowns_attempted", 0)),
                "fighterA_submission_attempts": int(stats_a.get("submission_attempts", 0)),
                "fighterA_knockdowns": int(stats_a.get("knockdowns", 0)),
                "fighterA_control_time_seconds": int(stats_a.get("control_time_seconds", 0)),
                "fighterB_sig_strikes_landed": int(stats_b.get("sig_strikes_landed", 0)),
                "fighterB_sig_strikes_attempted": int(stats_b.get("sig_strikes_attempted", 0)),
                "fighterB_takedowns_landed": int(stats_b.get("takedowns_landed", 0)),
                "fighterB_takedowns_attempted": int(stats_b.get("takedowns_attempted", 0)),
                "fighterB_submission_attempts": int(stats_b.get("submission_attempts", 0)),
                "fighterB_knockdowns": int(stats_b.get("knockdowns", 0)),
                "fighterB_control_time_seconds": int(stats_b.get("control_time_seconds", 0)),
            }
        )
    return pd.DataFrame(rows)

def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape nearest completed UFC event, collect fighter/fight stats, and persist to DB.")
    parser.parse_args()

    fights, fighter_profiles, nearest_event = scrape_ufc()
    if not fights:
        raise RuntimeError("No fights were scraped from nearest completed UFC event.")

    df = make_feature_rows(fights, fighter_profiles)
    cleaned = normalize_fights(df) if not df.empty else pd.DataFrame()
    nearest_event_stats = build_nearest_event_stats_rows(fights)
    RAW_PATH.parent.mkdir(parents=True, exist_ok=True)
    if cleaned.empty:
        pd.DataFrame(
            columns=[
                "fighterA",
                "fighterB",
                "strike_diff",
                "takedown_diff",
                "reach_diff",
                "win_streak_diff",
                "age_diff",
                "experience_diff",
                "finish_rate_diff",
                "winner",
            ]
        ).to_csv(RAW_PATH, index=False)
    else:
        cleaned.to_csv(RAW_PATH, index=False)
    nearest_event_stats.to_csv(NEAREST_EVENT_STATS_PATH, index=False)
    wrote_db = save_to_database(fights, fighter_profiles)

    print(f"Nearest completed event: {nearest_event.get('event_name')} ({nearest_event.get('event_date')})")
    print(f"Saved {len(cleaned)} feature rows to {RAW_PATH}")
    print(f"Saved {len(nearest_event_stats)} nearest-event fight rows to {NEAREST_EVENT_STATS_PATH}")
    if wrote_db:
        print(f"Inserted/updated {len(fights)} fights into MySQL database")


if __name__ == "__main__":
    main()
