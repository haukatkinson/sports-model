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


def parse_float_stat(text: str, label: str) -> float:
    pattern = rf"{re.escape(label)}\s*:\s*(\d+(?:\.\d+)?)"
    match = re.search(pattern, text, flags=re.IGNORECASE)
    return float(match.group(1)) if match else 0.0


def parse_percent_stat(text: str, label: str) -> float:
    pattern = rf"{re.escape(label)}\s*:\s*(\d+(?:\.\d+)?)%"
    match = re.search(pattern, text, flags=re.IGNORECASE)
    return float(match.group(1)) if match else 0.0


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
        "slpm": parse_float_stat(text, "SLpM"),
        "sapm": parse_float_stat(text, "SApM"),
        "td_avg": parse_float_stat(text, "TD Avg."),
        "sub_avg": parse_float_stat(text, "Sub. Avg."),
        "str_def": parse_percent_stat(text, "Str. Def"),
        "td_def": parse_percent_stat(text, "TD Def."),
    }
    cache[url] = profile
    return profile


def is_heavy_division(weight_class_name: str) -> bool:
    lowered = (weight_class_name or "").lower()
    return "heavyweight" in lowered


def fighter_base_score(profile: Dict[str, Any]) -> float:
    wins = int(profile.get("wins", 0) or 0)
    losses = int(profile.get("losses", 0) or 0)
    draws = int(profile.get("draws", 0) or 0)
    total = wins + losses + draws
    win_rate = (wins / total) if total else 0.5
    experience_bonus = math.log1p(total) / 10.0
    return (win_rate - 0.5) + experience_bonus


def fighter_age(profile: Dict[str, Any]) -> float | None:
    dob = profile.get("dob")
    if not isinstance(dob, date):
        return None
    return (date.today() - dob).days / 365.25


def profile_metrics(profile: Dict[str, Any]) -> Dict[str, float]:
    wins = int(profile.get("wins", 0) or 0)
    losses = int(profile.get("losses", 0) or 0)
    draws = int(profile.get("draws", 0) or 0)
    total = wins + losses + draws
    win_rate = (wins / total) if total else 0.5
    reach_cm = float(profile.get("reach_cm", 0.0) or 0.0)
    age_years = fighter_age(profile)
    return {
        "wins": float(wins),
        "losses": float(losses),
        "draws": float(draws),
        "total_fights": float(total),
        "win_rate": float(win_rate),
        "reach_cm": float(reach_cm),
        "age_years": float(age_years) if age_years is not None else float("nan"),
    }


def matchup_score(profile_a: Dict[str, Any], profile_b: Dict[str, Any], weight_class_name: str) -> tuple[float, float]:
    slpm_a = float(profile_a.get("slpm", 0.0) or 0.0)
    slpm_b = float(profile_b.get("slpm", 0.0) or 0.0)
    sapm_a = float(profile_a.get("sapm", 0.0) or 0.0)
    sapm_b = float(profile_b.get("sapm", 0.0) or 0.0)
    str_def_a = float(profile_a.get("str_def", 0.0) or 0.0)
    str_def_b = float(profile_b.get("str_def", 0.0) or 0.0)

    striking_edge = (
        (slpm_a - slpm_b) * 0.9
        + (sapm_b - sapm_a) * 0.5
        + ((str_def_a - str_def_b) / 100.0) * 0.7
    )

    reach_diff_cm = float(profile_a.get("reach_cm", 0.0) or 0.0) - float(profile_b.get("reach_cm", 0.0) or 0.0)
    reach_combo_bonus = (reach_diff_cm / 20.0) * max(0.0, striking_edge + 0.15)

    td_avg_a = float(profile_a.get("td_avg", 0.0) or 0.0)
    td_avg_b = float(profile_b.get("td_avg", 0.0) or 0.0)
    sub_avg_a = float(profile_a.get("sub_avg", 0.0) or 0.0)
    sub_avg_b = float(profile_b.get("sub_avg", 0.0) or 0.0)
    td_def_a = float(profile_a.get("td_def", 0.0) or 0.0)
    td_def_b = float(profile_b.get("td_def", 0.0) or 0.0)
    grappling_edge = (
        (td_avg_a - td_avg_b) * 0.45
        + (sub_avg_a - sub_avg_b) * 0.55
        + ((td_def_a - td_def_b) / 100.0) * 0.25
    )

    age_adjust = 0.0
    age_a = fighter_age(profile_a)
    age_b = fighter_age(profile_b)
    if age_a is not None and age_b is not None:
        heavier = is_heavy_division(weight_class_name)
        age_gap = age_a - age_b
        if age_a >= 37 and age_b <= 33 and age_gap >= 5:
            age_adjust -= 0.22 if heavier else 0.34
        elif age_b >= 37 and age_a <= 33 and (-age_gap) >= 5:
            age_adjust += 0.22 if heavier else 0.34

    total = (striking_edge * 0.52) + (grappling_edge * 0.33) + (reach_combo_bonus * 0.75) + age_adjust
    return total, striking_edge


def build_explanation(
    fighter_a: str,
    fighter_b: str,
    weight_class_name: str,
    prob_model_a: float,
    prob_profile_a: float | None,
    prob_final_a: float,
    profile_a: Dict[str, Any],
    profile_b: Dict[str, Any],
) -> Dict[str, Any]:
    favored = fighter_a if prob_final_a >= 0.5 else fighter_b
    underdog = fighter_b if favored == fighter_a else fighter_a

    factors: list[tuple[float, str]] = []
    metrics_a = profile_metrics(profile_a) if profile_a else {}
    metrics_b = profile_metrics(profile_b) if profile_b else {}
    is_heavy = is_heavy_division(weight_class_name)

    if metrics_a and metrics_b:
        win_rate_diff = metrics_a["win_rate"] - metrics_b["win_rate"]
        if abs(win_rate_diff) >= 0.04:
            stronger = fighter_a if win_rate_diff > 0 else fighter_b
            factors.append((abs(win_rate_diff), f"{stronger} has the stronger historical win rate"))

        reach_diff = metrics_a["reach_cm"] - metrics_b["reach_cm"]
        strike_a = float(profile_a.get("slpm", 0.0) or 0.0)
        strike_b = float(profile_b.get("slpm", 0.0) or 0.0)
        str_def_a = float(profile_a.get("str_def", 0.0) or 0.0)
        str_def_b = float(profile_b.get("str_def", 0.0) or 0.0)
        strike_edge = (strike_a - strike_b) + ((str_def_a - str_def_b) / 100.0)
        if abs(reach_diff) >= 6.0 and abs(strike_edge) >= 0.25:
            longer = fighter_a if reach_diff > 0 else fighter_b
            factors.append((abs(reach_diff) / 50.0 + abs(strike_edge) / 3.0, f"{longer}'s reach + striking edge creates stronger distance control"))
        elif abs(reach_diff) >= 7.0:
            longer = fighter_a if reach_diff > 0 else fighter_b
            factors.append((abs(reach_diff) / 130.0, f"{longer} has a notable reach advantage"))

        exp_diff = metrics_a["total_fights"] - metrics_b["total_fights"]
        if abs(exp_diff) >= 5:
            experienced = fighter_a if exp_diff > 0 else fighter_b
            factors.append((abs(exp_diff) / 50.0, f"{experienced} has more pro fight experience"))

        age_a = metrics_a.get("age_years")
        age_b = metrics_b.get("age_years")
        if isinstance(age_a, float) and isinstance(age_b, float) and not math.isnan(age_a) and not math.isnan(age_b):
            older_name = fighter_a if age_a > age_b else fighter_b
            younger_name = fighter_b if older_name == fighter_a else fighter_a
            older_age = max(age_a, age_b)
            younger_age = min(age_a, age_b)
            if older_age >= 37 and younger_age <= 33 and (older_age - younger_age) >= 5:
                weight_note = " (lighter division)" if not is_heavy else ""
                factors.append((0.18, f"Age gap is substantial: {older_name} is in late-career range vs {younger_name}{weight_note}"))

    factors.append((abs(prob_final_a - 0.5), f"Model confidence leans toward {favored}"))
    if prob_profile_a is not None:
        factors.append((abs(prob_profile_a - 0.5), "Profile matchup signal supports the lean"))
    factors.append((abs(prob_model_a - 0.5), "Baseline trained model output contributes to the pick"))

    factors_sorted = [item[1] for item in sorted(factors, key=lambda item: item[0], reverse=True)[:3]]

    summary = f"{favored} is favored over {underdog} based on blended model and profile matchup signals."

    return {
        "summary": summary,
        "factors": factors_sorted,
        "source": "blended-model-profile",
        "blended_prob_fighterA": round(prob_final_a, 6),
        "model_prob_fighterA": round(prob_model_a, 6),
        "profile_prob_fighterA": round(prob_profile_a, 6) if prob_profile_a is not None else None,
    }


def choose_predicted_method(winner_profile: Dict[str, Any], loser_profile: Dict[str, Any], confidence: float) -> str:
    winner_slpm = float(winner_profile.get("slpm", 0.0) or 0.0)
    winner_sub_avg = float(winner_profile.get("sub_avg", 0.0) or 0.0)
    winner_td_avg = float(winner_profile.get("td_avg", 0.0) or 0.0)

    loser_sapm = float(loser_profile.get("sapm", 0.0) or 0.0)
    loser_str_def = float(loser_profile.get("str_def", 0.0) or 0.0)
    loser_td_def = float(loser_profile.get("td_def", 0.0) or 0.0)

    ko_signal = (winner_slpm * 0.9) + (loser_sapm * 0.7) + ((100.0 - loser_str_def) / 100.0)
    sub_signal = (winner_sub_avg * 1.25) + (winner_td_avg * 0.5) + ((100.0 - loser_td_def) / 100.0)

    decision_signal = 1.25 + max(0.0, 0.18 - abs(confidence - 0.5)) * 5.0
    decision_signal += max(0.0, 0.55 - max(ko_signal, sub_signal))

    method_scores = {
        "KO/TKO": ko_signal,
        "Submission": sub_signal,
        "Decision": decision_signal,
    }
    return max(method_scores, key=method_scores.get)


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
        weight_class_name = str(row.get("weight_class_name", "")).strip()

        payload = {
            "fighterA": fighter_a,
            "fighterB": fighter_b,
        }
        winner, probabilities = predict_fight(payload)

        prob_model_a = float(probabilities.get(fighter_a, 0.5))
        profile_a = parse_fighter_profile(fighter_a_url, profile_cache)
        profile_b = parse_fighter_profile(fighter_b_url, profile_cache)

        prob_profile_a = None
        if profile_a and profile_b:
            base_diff = fighter_base_score(profile_a) - fighter_base_score(profile_b)
            style_diff, _ = matchup_score(profile_a, profile_b, weight_class_name)
            score_diff = (base_diff * 0.7) + style_diff
            prob_profile_a = sigmoid(score_diff * 2.5)
            prob_a = (0.35 * prob_model_a) + (0.65 * prob_profile_a)
        else:
            prob_a = prob_model_a

        prob_a = max(0.01, min(0.99, prob_a))
        prob_b = 1.0 - prob_a
        winner = fighter_a if prob_a >= prob_b else fighter_b
        winner_profile = profile_a if winner == fighter_a else profile_b
        loser_profile = profile_b if winner == fighter_a else profile_a
        predicted_method = choose_predicted_method(winner_profile or {}, loser_profile or {}, prob_a)

        predictions[build_fight_key(fighter_a, fighter_b)] = {
            "winner": winner,
            "predicted_method": predicted_method,
            "probabilities": {
                fighter_a: prob_a,
                fighter_b: prob_b,
            },
            "explanation": build_explanation(
                fighter_a=fighter_a,
                fighter_b=fighter_b,
                weight_class_name=weight_class_name,
                prob_model_a=prob_model_a,
                prob_profile_a=prob_profile_a,
                prob_final_a=prob_a,
                profile_a=profile_a,
                profile_b=profile_b,
            ),
        }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(predictions, indent=2), encoding="utf-8")

    print(f"Wrote {len(predictions)} predictions to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
