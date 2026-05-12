from __future__ import annotations

import csv
import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Dict, Any
from urllib.request import Request, urlopen
from datetime import datetime, date, timezone

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


def extract_recent_opponent_urls(html: str, fighter_url: str, limit: int = 5) -> list[str]:
    links = re.findall(r'href="(http://ufcstats.com/fighter-details/[a-z0-9]+)"', html)
    seen: set[str] = set()
    opponents: list[str] = []
    for link in links:
        if link == fighter_url:
            continue
        if link in seen:
            continue
        seen.add(link)
        opponents.append(link)
        if len(opponents) >= limit:
            break
    return opponents


def compute_sos_score(opponent_profiles: list[Dict[str, Any]]) -> float:
    weighted_sum = 0.0
    weight_total = 0.0
    for index, opponent in enumerate(opponent_profiles):
        wins = int(opponent.get("wins", 0) or 0)
        losses = int(opponent.get("losses", 0) or 0)
        draws = int(opponent.get("draws", 0) or 0)
        total = wins + losses + draws
        if total == 0:
            continue
        win_rate = wins / total
        recency_weight = float(len(opponent_profiles) - index)
        experience_weight = min(2.0, 1.0 + math.log1p(total) / 4.0)
        weight = recency_weight * experience_weight
        weighted_sum += win_rate * weight
        weight_total += weight

    if weight_total <= 0:
        return 0.5
    return weighted_sum / weight_total


def parse_fighter_profile(url: str, cache: Dict[str, Dict[str, Any]], include_sos: bool = True) -> Dict[str, Any]:
    if not url:
        return {}
    cache_key = url if include_sos else f"{url}#nosos"
    if cache_key in cache:
        return cache[cache_key]
    if include_sos and url in cache:
        return cache[url]

    try:
        html = fetch_html(url)
    except Exception:
        cache[cache_key] = {}
        if include_sos:
            cache[url] = {}
        return cache[cache_key]

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
        "sos_score": 0.5,
    }

    if include_sos:
        opponent_urls = extract_recent_opponent_urls(html, url, limit=5)
        opponent_profiles = [
            parse_fighter_profile(opponent_url, cache, include_sos=False)
            for opponent_url in opponent_urls
        ]
        opponent_profiles = [opponent for opponent in opponent_profiles if opponent]
        profile["sos_score"] = compute_sos_score(opponent_profiles)

    cache[cache_key] = profile
    if include_sos:
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
    sos_score = float(profile.get("sos_score", 0.5) or 0.5)
    sos_bonus = (sos_score - 0.5) * 0.9
    return (win_rate - 0.5) + experience_bonus + sos_bonus


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
        "sos_score": float(profile.get("sos_score", 0.5) or 0.5),
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

    wrestle_pressure_a = (
        max(0.0, td_avg_a - 1.2) * 0.55
        + max(0.0, sub_avg_a - 0.25) * 0.75
        + max(0.0, (55.0 - td_def_b) / 100.0) * 0.55
    )
    wrestle_pressure_b = (
        max(0.0, td_avg_b - 1.2) * 0.55
        + max(0.0, sub_avg_b - 0.25) * 0.75
        + max(0.0, (55.0 - td_def_a) / 100.0) * 0.55
    )

    grappling_edge += (wrestle_pressure_a - wrestle_pressure_b) * 0.5

    wrestle_threat_a = max(0.0, td_avg_a - 1.0) * 0.65 + max(0.0, sub_avg_a - 0.2) * 0.45
    wrestle_threat_b = max(0.0, td_avg_b - 1.0) * 0.65 + max(0.0, sub_avg_b - 0.2) * 0.45

    takedown_vulnerability_a = wrestle_threat_b * max(0.0, (60.0 - td_def_a) / 60.0) * 0.75
    takedown_vulnerability_b = wrestle_threat_a * max(0.0, (60.0 - td_def_b) / 60.0) * 0.75
    takedown_resistance_a = wrestle_threat_b * max(0.0, (td_def_a - 62.0) / 38.0) * 0.20
    takedown_resistance_b = wrestle_threat_a * max(0.0, (td_def_b - 62.0) / 38.0) * 0.20

    grappling_edge += (takedown_vulnerability_b - takedown_vulnerability_a)
    grappling_edge += (takedown_resistance_a - takedown_resistance_b)

    wrestler_style_a = max(0.0, td_avg_a - 1.35)
    wrestler_style_b = max(0.0, td_avg_b - 1.35)
    striking_vulnerability_a = wrestler_style_a * max(0.0, (58.0 - str_def_a) / 58.0) * 0.55
    striking_vulnerability_b = wrestler_style_b * max(0.0, (58.0 - str_def_b) / 58.0) * 0.55

    striking_edge += (striking_vulnerability_b - striking_vulnerability_a)

    if td_avg_a > (td_avg_b + 0.9) and slpm_a < slpm_b:
        striking_edge += 0.12
    if td_avg_b > (td_avg_a + 0.9) and slpm_b < slpm_a:
        striking_edge -= 0.12

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
        td_avg_a = float(profile_a.get("td_avg", 0.0) or 0.0)
        td_avg_b = float(profile_b.get("td_avg", 0.0) or 0.0)
        sub_avg_a = float(profile_a.get("sub_avg", 0.0) or 0.0)
        sub_avg_b = float(profile_b.get("sub_avg", 0.0) or 0.0)
        td_def_a = float(profile_a.get("td_def", 0.0) or 0.0)
        td_def_b = float(profile_b.get("td_def", 0.0) or 0.0)
        grappling_signal = (
            (td_avg_a - td_avg_b) * 0.45
            + (sub_avg_a - sub_avg_b) * 0.55
            + ((td_def_a - td_def_b) / 100.0) * 0.25
        )
        if abs(reach_diff) >= 6.0 and abs(strike_edge) >= 0.25:
            longer = fighter_a if reach_diff > 0 else fighter_b
            factors.append((abs(reach_diff) / 50.0 + abs(strike_edge) / 3.0, f"{longer}'s reach + striking edge creates stronger distance control"))
        elif abs(reach_diff) >= 7.0:
            longer = fighter_a if reach_diff > 0 else fighter_b
            factors.append((abs(reach_diff) / 130.0, f"{longer} has a notable reach advantage"))

        if abs(grappling_signal) >= 0.35:
            grappler = fighter_a if grappling_signal > 0 else fighter_b
            factors.append((abs(grappling_signal), f"{grappler} projects stronger mat control and grappling pressure"))

        wrestle_threat_a = max(0.0, td_avg_a - 1.0) + (max(0.0, sub_avg_a - 0.2) * 0.7)
        wrestle_threat_b = max(0.0, td_avg_b - 1.0) + (max(0.0, sub_avg_b - 0.2) * 0.7)
        if wrestle_threat_b >= 0.8 and td_def_a <= 58.0:
            factors.append((wrestle_threat_b / 2.4, f"{fighter_a} could be vulnerable to {fighter_b}'s takedown pressure due to lower TD defense"))
        if wrestle_threat_a >= 0.8 and td_def_b <= 58.0:
            factors.append((wrestle_threat_a / 2.4, f"{fighter_b} could be vulnerable to {fighter_a}'s takedown pressure due to lower TD defense"))

        if td_avg_a >= 1.6 and str_def_a <= 52.0:
            factors.append((0.22, f"{fighter_a}'s lower striking defense adds risk in stand-up exchanges"))
        if td_avg_b >= 1.6 and str_def_b <= 52.0:
            factors.append((0.22, f"{fighter_b}'s lower striking defense adds risk in stand-up exchanges"))

        exp_diff = metrics_a["total_fights"] - metrics_b["total_fights"]
        if abs(exp_diff) >= 5:
            experienced = fighter_a if exp_diff > 0 else fighter_b
            factors.append((abs(exp_diff) / 50.0, f"{experienced} has more pro fight experience"))

        sos_diff = metrics_a["sos_score"] - metrics_b["sos_score"]
        if abs(sos_diff) >= 0.05:
            tougher = fighter_a if sos_diff > 0 else fighter_b
            factors.append((abs(sos_diff) * 1.9, f"{tougher} has faced tougher recent opposition in the last 5 opponents (SoS)"))

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

    model_edge_name = fighter_a if prob_model_a >= 0.5 else fighter_b
    model_edge_points = abs(prob_model_a - 0.5) * 200.0
    profile_narrative = "Profile signal was unavailable for one or both fighters."
    if prob_profile_a is not None:
        profile_edge_name = fighter_a if prob_profile_a >= 0.5 else fighter_b
        profile_edge_points = abs(prob_profile_a - 0.5) * 200.0
        profile_narrative = f"Matchup profile leans {profile_edge_name} by {profile_edge_points:.1f} points."

    blended_points = abs(prob_final_a - 0.5) * 200.0
    confidence_band = "high" if blended_points >= 24 else ("medium" if blended_points >= 12 else "low")
    favored_pct = prob_final_a * 100.0 if favored == fighter_a else (1.0 - prob_final_a) * 100.0
    detailed_summary = (
        f"{favored} is projected at {favored_pct:.1f}% win probability. "
        f"Baseline model leans {model_edge_name} by {model_edge_points:.1f} points. "
        f"{profile_narrative} "
        f"Final blended edge is {blended_points:.1f} points ({confidence_band} confidence)."
    )

    return {
        "summary": summary,
        "detailed_summary": detailed_summary,
        "factors": factors_sorted,
        "source": "blended-model-profile",
        "blended_prob_fighterA": round(prob_final_a, 6),
        "model_prob_fighterA": round(prob_model_a, 6),
        "profile_prob_fighterA": round(prob_profile_a, 6) if prob_profile_a is not None else None,
    }


def calibrate_probability(probability: float) -> float:
    return max(0.01, min(0.99, ((probability - 0.5) * 0.6) + 0.5))


def apply_matchup_correction(prob_model_a: float, prob_profile_a: float | None) -> tuple[float, float]:
    if prob_profile_a is None:
        return prob_model_a, 0.0

    raw_delta = prob_profile_a - prob_model_a
    model_extremeness = min(1.0, abs(prob_model_a - 0.5) * 2.0)
    correction_scale = 0.18 + (0.06 * model_extremeness)
    correction = math.tanh(raw_delta * 2.25) * correction_scale
    corrected = max(0.01, min(0.99, prob_model_a + correction))
    return corrected, correction


def method_probabilities(winner_profile: Dict[str, Any], loser_profile: Dict[str, Any], confidence: float) -> Dict[str, float]:
    winner_slpm = float(winner_profile.get("slpm", 0.0) or 0.0)
    loser_slpm = float(loser_profile.get("slpm", 0.0) or 0.0)
    winner_sub_avg = float(winner_profile.get("sub_avg", 0.0) or 0.0)
    loser_sub_avg = float(loser_profile.get("sub_avg", 0.0) or 0.0)
    winner_td_avg = float(winner_profile.get("td_avg", 0.0) or 0.0)
    loser_td_avg = float(loser_profile.get("td_avg", 0.0) or 0.0)

    loser_sapm = float(loser_profile.get("sapm", 0.0) or 0.0)
    winner_sapm = float(winner_profile.get("sapm", 0.0) or 0.0)
    winner_str_def = float(winner_profile.get("str_def", 0.0) or 0.0)
    loser_str_def = float(loser_profile.get("str_def", 0.0) or 0.0)
    winner_td_def = float(winner_profile.get("td_def", 0.0) or 0.0)
    loser_td_def = float(loser_profile.get("td_def", 0.0) or 0.0)

    striking_edge = (
        (winner_slpm - loser_slpm) * 0.9
        + (loser_sapm - winner_sapm) * 0.35
        + ((winner_str_def - loser_str_def) / 100.0) * 0.5
    )
    ko_signal = 0.35 + max(0.0, striking_edge) + max(0.0, (100.0 - loser_str_def) / 100.0 - 0.4)

    grappling_edge = (
        (winner_sub_avg - loser_sub_avg) * 1.05
        + (winner_td_avg - loser_td_avg) * 0.5
        + ((winner_td_def - loser_td_def) / 100.0) * 0.25
    )
    sub_signal = 0.32 + max(0.0, grappling_edge) + max(0.0, (100.0 - loser_td_def) / 100.0 - 0.45)

    closeness = max(0.0, 0.62 - abs(confidence - 0.5))
    dec_signal = 0.55 + (closeness * 2.0)
    dec_signal += max(0.0, 0.25 - max(striking_edge, grappling_edge))

    logits = {
        "KO/TKO": max(0.01, ko_signal),
        "Submission": max(0.01, sub_signal),
        "Decision": max(0.01, dec_signal),
    }

    max_logit = max(logits.values())
    exp_values = {key: math.exp(value - max_logit) for key, value in logits.items()}
    total = sum(exp_values.values())
    if total <= 0:
        return {"KO/TKO": 0.33, "Submission": 0.33, "Decision": 0.34}

    return {key: value / total for key, value in exp_values.items()}


def sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-value))


def build_fight_key(fighter_a: str, fighter_b: str) -> str:
    return hashlib.md5(f"{fighter_a}|{fighter_b}".encode("utf-8")).hexdigest()


def build_feature_signature(profile_a: Dict[str, Any], profile_b: Dict[str, Any], weight_class_name: str) -> str:
    keys = ["wins", "losses", "draws", "reach_cm", "slpm", "sapm", "td_avg", "sub_avg", "str_def", "td_def"]
    tuple_a = tuple(float(profile_a.get(key, 0.0) or 0.0) for key in keys)
    tuple_b = tuple(float(profile_b.get(key, 0.0) or 0.0) for key in keys)
    payload = f"{weight_class_name}|{tuple_a}|{tuple_b}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


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
    probability_values: list[float] = []
    method_values: list[str] = []
    feature_signatures: list[str] = []
    missing_profile_fights = 0

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

        prob_model_a_raw = float(probabilities.get(fighter_a, 0.5))
        prob_model_a = calibrate_probability(prob_model_a_raw)
        profile_a = parse_fighter_profile(fighter_a_url, profile_cache)
        profile_b = parse_fighter_profile(fighter_b_url, profile_cache)
        if not profile_a or not profile_b:
            missing_profile_fights += 1

        feature_signatures.append(build_feature_signature(profile_a or {}, profile_b or {}, weight_class_name))

        prob_profile_a = None
        if profile_a and profile_b:
            base_diff = fighter_base_score(profile_a) - fighter_base_score(profile_b)
            style_diff, _ = matchup_score(profile_a, profile_b, weight_class_name)
            score_diff = (base_diff * 0.7) + style_diff
            prob_profile_a = sigmoid(score_diff * 2.5)
            prob_a, matchup_correction = apply_matchup_correction(prob_model_a, prob_profile_a)
        else:
            prob_a = prob_model_a
            matchup_correction = 0.0

        prob_a = max(0.01, min(0.99, prob_a))
        prob_b = 1.0 - prob_a
        probability_values.append(round(prob_a, 6))
        winner = fighter_a if prob_a >= prob_b else fighter_b
        winner_profile = profile_a if winner == fighter_a else profile_b
        loser_profile = profile_b if winner == fighter_a else profile_a
        method_probs = method_probabilities(winner_profile or {}, loser_profile or {}, prob_a)
        predicted_method = max(method_probs, key=method_probs.get)
        method_values.append(predicted_method)

        predictions[build_fight_key(fighter_a, fighter_b)] = {
            "winner": winner,
            "predicted_method": predicted_method,
            "predicted_method_probabilities": {
                "KO/TKO": round(method_probs.get("KO/TKO", 0.0), 6),
                "Submission": round(method_probs.get("Submission", 0.0), 6),
                "Decision": round(method_probs.get("Decision", 0.0), 6),
            },
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
            "calibration": {
                "raw_model_prob_fighterA": round(prob_model_a_raw, 6),
                "calibrated_model_prob_fighterA": round(prob_model_a, 6),
                "matchup_correction": round(matchup_correction, 6),
            },
        }

    total_fights = len(probability_values)
    unique_probs = len(set(probability_values))
    unique_feature_signatures = len(set(feature_signatures))

    if total_fights >= 5 and unique_probs <= 2:
        raise RuntimeError(
            f"Sanity check failed: only {unique_probs} unique probabilities across {total_fights} fights."
        )

    if total_fights >= 5 and unique_feature_signatures <= 2:
        raise RuntimeError(
            f"Sanity check failed: feature signatures collapsed to {unique_feature_signatures} unique values."
        )

    if total_fights > 0 and missing_profile_fights == total_fights:
        raise RuntimeError("All fighter profile fetches failed; refusing to emit constant-like fallback predictions.")

    method_counts = Counter(method_values)
    warnings: list[str] = []
    if total_fights >= 6:
        top_method, top_count = method_counts.most_common(1)[0]
        if top_count / total_fights >= 0.85:
            warnings.append(
                f"Method concentration warning: {top_method} selected for {top_count}/{total_fights} fights."
            )

    predictions["__meta__"] = {
        "total_fights": total_fights,
        "unique_probabilities": unique_probs,
        "unique_feature_signatures": unique_feature_signatures,
        "missing_profile_fights": missing_profile_fights,
        "method_counts": dict(method_counts),
        "warnings": warnings,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(predictions, indent=2), encoding="utf-8")

    print(f"Wrote {len(predictions)} predictions to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
