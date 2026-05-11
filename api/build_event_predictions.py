from __future__ import annotations

import csv
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Dict, Any
from urllib.request import Request, urlopen
from datetime import datetime, date

ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "data" / "nearest_event_fights.csv"
OUTPUT_PATH = ROOT / "data" / "nearest_event_predictions.json"

import sys

API_DIR = ROOT / "api"
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from predict import predict_fight  # noqa: E402


def fetch_html(url: str) -> str:
    if not url:
        return ""
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        },
    )
    with urlopen(request, timeout=20) as response:  # nosec B310 - trusted UFC stats URL
        return response.read().decode("utf-8", errors="ignore")


def parse_record(text: str) -> tuple[int, int, int]:
    match = re.search(r"Record:\s*(\d+)-(\d+)-(\d+)", text, flags=re.IGNORECASE)
    if not match:
        return 0, 0, 0
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def parse_reach_cm(text: str) -> float:
    match = re.search(r"Reach:\s*(\d+(?:\.\d+)?)\s*\"", text)
    if not match:
        return 0.0
    return float(match.group(1)) * 2.54


def parse_dob(text: str) -> date | None:
    match = re.search(r"DOB:\s*([A-Za-z]{3,9}\s+\d{1,2},\s+\d{4})", text, flags=re.IGNORECASE)
    if not match:
        return None

    value = match.group(1)
    for fmt in ("%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def parse_fighter_profile(url: str, cache: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    if not url:
        return {}
    if url in cache:
        return cache[url]

    try:
        html = fetch_html(url)
    except Exception:
        cache[url] = {}
        return cache[url]

    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text).strip()

    wins, losses, draws = parse_record(text)
    reach_cm = parse_reach_cm(text)
    dob = parse_dob(text)

    profile = {
        "wins": wins,
        "losses": losses,
        "draws": draws,
        "reach_cm": reach_cm,
        "dob": dob,
    }
    cache[url] = profile
    return profile


def fighter_score(profile: Dict[str, Any]) -> float:
    wins = int(profile.get("wins", 0) or 0)
    losses = int(profile.get("losses", 0) or 0)
    draws = int(profile.get("draws", 0) or 0)
    total = wins + losses + draws
    win_rate = (wins / total) if total else 0.5

    reach_cm = float(profile.get("reach_cm", 0.0) or 0.0)
    reach_bonus = (reach_cm - 177.8) / 100.0 if reach_cm else 0.0

    age_bonus = 0.0
    dob = profile.get("dob")
    if isinstance(dob, date):
        age = (date.today() - dob).days / 365.25
        age_bonus = -abs(age - 30.0) / 40.0

    experience_bonus = math.log1p(total) / 10.0
    return (win_rate - 0.5) + reach_bonus + age_bonus + experience_bonus


def sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-value))


def build_fight_key(fighter_a: str, fighter_b: str) -> str:
    return hashlib.md5(f"{fighter_a}|{fighter_b}".encode("utf-8")).hexdigest()


def main() -> None:
    if not DATA_PATH.exists():
        raise FileNotFoundError(f"Missing input file: {DATA_PATH}")

    with DATA_PATH.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "fighterA" not in reader.fieldnames or "fighterB" not in reader.fieldnames:
            raise ValueError("Input CSV must include fighterA and fighterB columns")

        rows = list(reader)

    predictions: Dict[str, Any] = {}
    profile_cache: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        fighter_a = str(row.get("fighterA", "")).strip()
        fighter_b = str(row.get("fighterB", "")).strip()
        if not fighter_a or not fighter_b:
            continue

        fighter_a_url = str(row.get("fighterA_url", "")).strip()
        fighter_b_url = str(row.get("fighterB_url", "")).strip()

        payload = {
            "fighterA": fighter_a,
            "fighterB": fighter_b,
        }
        winner, probabilities = predict_fight(payload)

        prob_model_a = float(probabilities.get(fighter_a, 0.5))
        profile_a = parse_fighter_profile(fighter_a_url, profile_cache)
        profile_b = parse_fighter_profile(fighter_b_url, profile_cache)

        if profile_a and profile_b:
            score_diff = fighter_score(profile_a) - fighter_score(profile_b)
            prob_profile_a = sigmoid(score_diff * 2.5)
            prob_a = (0.35 * prob_model_a) + (0.65 * prob_profile_a)
        else:
            prob_a = prob_model_a

        prob_a = max(0.01, min(0.99, prob_a))
        prob_b = 1.0 - prob_a
        winner = fighter_a if prob_a >= prob_b else fighter_b

        predictions[build_fight_key(fighter_a, fighter_b)] = {
            "winner": winner,
            "probabilities": {
                fighter_a: prob_a,
                fighter_b: prob_b,
            },
        }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(predictions, indent=2), encoding="utf-8")

    print(f"Wrote {len(predictions)} predictions to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
