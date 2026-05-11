from __future__ import annotations

import csv
from datetime import date
from pathlib import Path
from typing import Dict, List, Tuple

from scrape_ufc import create_session, get_completed_event_links, parse_event_details, parse_fight_details

ROOT = Path(__file__).resolve().parents[1]
HISTORY_PATH = ROOT / "data" / "prediction_history.csv"


def normalize_name(value: str) -> str:
    return " ".join((value or "").strip().lower().split())


def make_pair_key(event_date: str, fighter_a: str, fighter_b: str) -> Tuple[str, str, str]:
    a = normalize_name(fighter_a)
    b = normalize_name(fighter_b)
    if a <= b:
        return event_date, a, b
    return event_date, b, a


def load_history(path: Path) -> tuple[list[str], list[Dict[str, str]]]:
    if not path.exists():
        return [], []

    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = list(reader.fieldnames or [])
        rows = [dict(row) for row in reader]
    return headers, rows


def save_history(path: Path, headers: list[str], rows: list[Dict[str, str]]) -> None:
    if not headers:
        headers = [
            "fight_key",
            "event_name",
            "event_date",
            "fighterA",
            "fighterB",
            "predicted_winner",
            "actual_winner",
            "tier",
            "confidence",
            "model_prob",
            "predicted_method",
            "created_at",
            "updated_at",
        ]

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in headers})


def fetch_recent_completed_results(max_events: int = 8) -> Dict[Tuple[str, str, str], str]:
    session = create_session()
    results: Dict[Tuple[str, str, str], str] = {}

    event_links = get_completed_event_links(session)[:max_events]
    for event_link in event_links:
        try:
            event = parse_event_details(session, event_link)
        except Exception:
            continue

        event_date_obj = event.get("event_date")
        if not event_date_obj:
            continue

        event_date = event_date_obj.isoformat()
        for fight_link in event.get("fight_links") or []:
            try:
                fight = parse_fight_details(session, fight_link)
            except Exception:
                continue
            if not fight:
                continue

            winner = (fight.get("winner") or "").strip()
            fighter_a = (fight.get("fighterA") or "").strip()
            fighter_b = (fight.get("fighterB") or "").strip()
            if winner == "" or fighter_a == "" or fighter_b == "":
                continue

            pair_key = make_pair_key(event_date, fighter_a, fighter_b)
            results[pair_key] = winner

    return results


def update_actual_winners(rows: list[Dict[str, str]], completed_results: Dict[Tuple[str, str, str], str]) -> int:
    today_iso = date.today().isoformat()
    updated = 0

    for row in rows:
        actual = (row.get("actual_winner") or "").strip()
        if actual:
            continue

        event_date = (row.get("event_date") or "").strip()
        if not event_date or event_date > today_iso:
            continue

        fighter_a = (row.get("fighterA") or "").strip()
        fighter_b = (row.get("fighterB") or "").strip()
        if fighter_a == "" or fighter_b == "":
            continue

        winner = completed_results.get(make_pair_key(event_date, fighter_a, fighter_b))
        if not winner:
            continue

        row["actual_winner"] = winner
        updated += 1

    return updated


def main() -> None:
    headers, rows = load_history(HISTORY_PATH)
    if not rows:
        print(f"No history rows found at {HISTORY_PATH}; nothing to sync.")
        return

    completed_results = fetch_recent_completed_results(max_events=8)
    updated = update_actual_winners(rows, completed_results)
    if updated == 0:
        print("No pending prediction results were updated.")
        return

    save_history(HISTORY_PATH, headers, rows)
    print(f"Updated {updated} prediction result(s) in {HISTORY_PATH}")


if __name__ == "__main__":
    main()
