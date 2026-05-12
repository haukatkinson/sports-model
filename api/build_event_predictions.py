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


def is_wmma_division(weight_class_name: str) -> bool:
    lowered = (weight_class_name or "").lower()
    return "women" in lowered


def fighter_base_score(profile: Dict[str, Any]) -> float:
    wins = int(profile.get("wins", 0) or 0)
    losses = int(profile.get("losses", 0) or 0)
    draws = int(profile.get("draws", 0) or 0)
    total = wins + losses + draws
    win_rate = (wins / total) if total else 0.5
    experience_bonus = math.log1p(total) / 14.0
    sos_score = float(profile.get("sos_score", 0.5) or 0.5)
    sos_bonus = (sos_score - 0.5) * 0.45
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


def compute_uncertainty_factor(profile_a: Dict[str, Any], profile_b: Dict[str, Any], weight_class_name: str) -> float:
    wins_a = int(profile_a.get("wins", 0) or 0)
    losses_a = int(profile_a.get("losses", 0) or 0)
    draws_a = int(profile_a.get("draws", 0) or 0)
    total_a = wins_a + losses_a + draws_a

    wins_b = int(profile_b.get("wins", 0) or 0)
    losses_b = int(profile_b.get("losses", 0) or 0)
    draws_b = int(profile_b.get("draws", 0) or 0)
    total_b = wins_b + losses_b + draws_b

    avg_total = (total_a + total_b) / 2.0
    volume_factor = min(1.0, avg_total / 12.0)
    low_exp_guard = min(1.0, max(0.0, float(min(total_a, total_b))) / 6.0)

    stat_keys = ["slpm", "sapm", "td_avg", "sub_avg", "str_def", "td_def", "reach_cm"]
    present_a = sum(1 for key in stat_keys if float(profile_a.get(key, 0.0) or 0.0) > 0.0)
    present_b = sum(1 for key in stat_keys if float(profile_b.get(key, 0.0) or 0.0) > 0.0)
    completeness = (present_a + present_b) / float(len(stat_keys) * 2)

    factor = (volume_factor * 0.55) + (low_exp_guard * 0.25) + (completeness * 0.20)
    if is_wmma_division(weight_class_name):
        factor *= 0.92
    return max(0.45, min(1.0, factor))


def build_model_feature_payload(
    fighter_a: str,
    fighter_b: str,
    profile_a: Dict[str, Any],
    profile_b: Dict[str, Any],
) -> Dict[str, Any]:
    wins_a = int(profile_a.get("wins", 0) or 0)
    losses_a = int(profile_a.get("losses", 0) or 0)
    draws_a = int(profile_a.get("draws", 0) or 0)
    total_a = wins_a + losses_a + draws_a

    wins_b = int(profile_b.get("wins", 0) or 0)
    losses_b = int(profile_b.get("losses", 0) or 0)
    draws_b = int(profile_b.get("draws", 0) or 0)
    total_b = wins_b + losses_b + draws_b

    slpm_a = float(profile_a.get("slpm", 0.0) or 0.0)
    slpm_b = float(profile_b.get("slpm", 0.0) or 0.0)
    sapm_a = float(profile_a.get("sapm", 0.0) or 0.0)
    sapm_b = float(profile_b.get("sapm", 0.0) or 0.0)
    str_def_a = float(profile_a.get("str_def", 0.0) or 0.0)
    str_def_b = float(profile_b.get("str_def", 0.0) or 0.0)

    td_avg_a = float(profile_a.get("td_avg", 0.0) or 0.0)
    td_avg_b = float(profile_b.get("td_avg", 0.0) or 0.0)
    td_def_a = float(profile_a.get("td_def", 0.0) or 0.0)
    td_def_b = float(profile_b.get("td_def", 0.0) or 0.0)

    sub_avg_a = float(profile_a.get("sub_avg", 0.0) or 0.0)
    sub_avg_b = float(profile_b.get("sub_avg", 0.0) or 0.0)

    strike_signal_a = (slpm_a - (sapm_a * 0.5)) + (str_def_a / 100.0)
    strike_signal_b = (slpm_b - (sapm_b * 0.5)) + (str_def_b / 100.0)

    takedown_signal_a = td_avg_a + (td_def_a / 100.0)
    takedown_signal_b = td_avg_b + (td_def_b / 100.0)

    age_a = fighter_age(profile_a)
    age_b = fighter_age(profile_b)

    finish_rate_proxy_a = (sub_avg_a * 0.55) + (max(0.0, slpm_a - 2.0) * 0.08)
    finish_rate_proxy_b = (sub_avg_b * 0.55) + (max(0.0, slpm_b - 2.0) * 0.08)

    return {
        "fighterA": fighter_a,
        "fighterB": fighter_b,
        "strike_diff": float(strike_signal_a - strike_signal_b),
        "takedown_diff": float(takedown_signal_a - takedown_signal_b),
        "reach_diff": float(float(profile_a.get("reach_cm", 0.0) or 0.0) - float(profile_b.get("reach_cm", 0.0) or 0.0)),
        "win_streak_diff": 0.0,
        "age_diff": float((age_a or 0.0) - (age_b or 0.0)),
        "experience_diff": float(total_a - total_b),
        "finish_rate_diff": float(finish_rate_proxy_a - finish_rate_proxy_b),
    }


def compute_power_score(profile: Dict[str, Any]) -> float:
    """
    Compute striking POWER score (damage creation ability).
    Weights: KO%, SLpM pressure, accuracy.
    Separates from finisher_score (damage conversion).
    Examples: Derrick Lewis (high), Merab (low).
    """
    wins = int(profile.get("wins", 0) or 0)
    losses = int(profile.get("losses", 0) or 0)
    draws = int(profile.get("draws", 0) or 0)
    total = wins + losses + draws
    
    if total == 0:
        return 0.0
    
    slpm = float(profile.get("slpm", 0.0) or 0.0)
    str_acc = float(profile.get("str_def", 0.0) or 0.0) if profile.get("str_def") else 0.0
    
    ko_win_rate = 0.0
    if wins > 0:
        ko_wins = wins * 0.35 
        ko_win_rate = min(1.0, ko_wins / wins)
    
    power_components = [
        ko_win_rate * 0.50,
        min(1.0, slpm / 8.0) * 0.35,
        min(1.0, str_acc / 50.0) * 0.15
    ]
    
    power_score = sum(power_components)
    return min(1.0, power_score)


def compute_finisher_score(profile: Dict[str, Any]) -> float:
    """
    Compute striking FINISHING ability (damage conversion).
    High finisher = swarms after damage, grinds clinch, TKOs from volume.
    High power + low finisher = big punchers who exhaust themselves (e.g., Lewis).
    High finisher + low power = pressure fighters (e.g., Holloway).
    Examples: Max Holloway (high), Francis Ngannou (high power, variable finishing).
    """
    wins = int(profile.get("wins", 0) or 0)
    losses = int(profile.get("losses", 0) or 0)
    draws = int(profile.get("draws", 0) or 0)
    total = wins + losses + draws
    
    if total == 0:
        return 0.0
    
    slpm = float(profile.get("slpm", 0.0) or 0.0)
    
    finishing_rate = 0.5 
    if total > 0:
        finishing_wins = wins * 0.6
        finishing_rate = min(1.0, finishing_wins / total)
    
    pressure_component = min(1.0, slpm / 6.5) if slpm > 0.0 else 0.0
    
    finisher_components = [
        finishing_rate * 0.55,
        pressure_component * 0.45
    ]
    
    finisher_score = sum(finisher_components)
    return min(1.0, finisher_score)


def apply_diminishing_returns(raw_bonus: float, compression_factor: float = 0.8) -> float:
    """
    Apply tanh-based diminishing returns to prevent bonus stacking explosions.
    Prevents: wrestler_bonus + sub_bonus + tdd_collapse + ... = 85% favorite
    Instead: compresses via tanh(raw * factor), keeping matchup realism bounded.
    
    Example: raw_bonus=1.2 -> tanh(1.2*0.8)=tanh(0.96)≈0.753 (capped realism)
    """
    if abs(raw_bonus) < 0.01:
        return 0.0
    compressed = math.tanh(raw_bonus * compression_factor)
    return compressed


def tdd_liability(td_def: float) -> float:
    """
    Nonlinear TDD vulnerability scaling.
    80%+ = essentially immune. Below 50% = catastrophic collapse zone.
    Sharper thresholds than before — regime collapse, not smooth scaling.
    """
    if td_def >= 80.0:
        return 0.02   # immune — elite TDD walls off wrestling completely
    elif td_def >= 70.0:
        return 0.10
    elif td_def >= 60.0:
        return 0.30
    elif td_def >= 50.0:
        return 0.65
    else:
        return 0.95   # catastrophic — gets controlled every single fight


def str_def_liability(str_def: float) -> float:
    """
    Nonlinear striking defense vulnerability scaling.
    Sharp penalty for poor striking defense (below 45%).
    Asymmetric: TDD matters more for wrestlers.
    """
    if str_def >= 65.0:
        return 0.05
    elif str_def >= 55.0:
        return 0.15
    elif str_def >= 45.0:
        return 0.30
    elif str_def >= 35.0:
        return 0.55
    else:
        return 0.85


def classify_archetype(profile: Dict[str, Any]) -> str:
    """
    Classify fighter archetype based on offensive/defensive profile.
    Categories: pressure_wrestler, submission_grappler, power_striker, technical_striker, balanced.
    """
    td_avg = float(profile.get("td_avg", 0.0) or 0.0)
    sub_avg = float(profile.get("sub_avg", 0.0) or 0.0)
    slpm = float(profile.get("slpm", 0.0) or 0.0)
    str_def = float(profile.get("str_def", 0.0) or 0.0)
    
    power_score_val = compute_power_score(profile)
    
    is_wrestler = td_avg > 2.0
    is_grappler = sub_avg > 0.6
    is_striker = slpm > 4.0
    is_technical = str_def > 55.0
    is_power_striker = power_score_val > 0.6 and is_striker
    
    if is_wrestler and td_avg > 3.5 and power_score_val < 0.5:
        return "pressure_wrestler"
    elif is_grappler and sub_avg > 1.0 and td_avg > 2.5:
        return "submission_grappler"
    elif is_power_striker:
        return "power_striker"
    elif is_technical and str_def > 58.0:
        return "technical_striker"
    else:
        return "balanced"


def compute_control_proxy(profile: Dict[str, Any]) -> float:
    """
    Estimate control time tendency from available stats.
    Higher = more grindy, control-oriented fighter.

    Uses tanh soft saturation to prevent artificial max-out.
    Previously: linear min(1.0, ...) caused Tuco to hit 1.000 (maxed).
    Now: tanh smoothly asymptotes — extreme wrestlers get ~0.95, not 1.00.
    Formula: tanh(td_avg*0.6 + sub_avg*0.3 + wrestling_tendency*0.4)
    """
    wins = int(profile.get("wins", 0) or 0)
    losses = int(profile.get("losses", 0) or 0)
    draws = int(profile.get("draws", 0) or 0)
    total = wins + losses + draws

    if total == 0:
        return 0.0

    td_avg = float(profile.get("td_avg", 0.0) or 0.0)
    sub_avg = float(profile.get("sub_avg", 0.0) or 0.0)
    td_def = float(profile.get("td_def", 0.0) or 0.0)

    wrestling_tendency = max(0.0, 1.0 - (td_def / 100.0))

    raw = (td_avg * 0.6) + (sub_avg * 0.3) + (wrestling_tendency * 0.4)
    return math.tanh(raw)


def compute_anti_wrestling_score(profile: Dict[str, Any]) -> float:
    """
    Estimate fighter's defensive wrestling ability — ability to avoid/escape control.
    DIRECTION: Higher = BETTER defense (harder to hold down).

    Components:
    - TD Def %: primary raw defense signal (50%)
    - SLpM: offensive output discourages control attempts (25%)
    - Sub threat off back: submission threat reduces opponent's top control safety (25%)

    Examples:
    - Oliveira (poor TDD but high sub avg + output): scores HIGH = correctly identified as hard to hold
    - Pure grappling dummy (low TDD, low output, no sub threat): scores LOW = correctly vulnerable
    """
    td_def = float(profile.get("td_def", 0.0) or 0.0)
    slpm = float(profile.get("slpm", 0.0) or 0.0)
    sub_avg = float(profile.get("sub_avg", 0.0) or 0.0)

    td_def_normalized = min(1.0, td_def / 80.0)
    slpm_normalized = min(1.0, slpm / 6.0)
    sub_threat = min(1.0, sub_avg / 1.5)

    components = [
        td_def_normalized * 0.50,
        slpm_normalized * 0.25,
        sub_threat * 0.25,
    ]

    return min(1.0, sum(components))


def compute_wrestling_entry_factor(attacker: Dict[str, Any], defender: Dict[str, Any]) -> float:
    """
    P(attacker successfully closes distance and initiates a takedown attempt).
    Semantic: probability of a clean entry given defender's threat profile.

    Reduces when defender has:
    - High striking output (punishes bad shots)
    - Good striking defense (exchanges favor defender, discourages wrestling)
    - Reach advantage (longer frame = harder clinch access)

    Returns: float in [0.25, 0.90] — even elite defenders don't prevent all entries,
             and even bad defenders resist some.
    """
    defender_slpm = float(defender.get("slpm", 0.0) or 0.0)
    defender_str_def = float(defender.get("str_def", 0.0) or 0.0)

    attacker_reach = float(attacker.get("reach_cm", 175.0) or 175.0)
    defender_reach = float(defender.get("reach_cm", 175.0) or 175.0)
    reach_gap = max(0.0, defender_reach - attacker_reach)

    # Each factor is a named probability component
    p_striking_threat = min(1.0, defender_slpm / 6.0)       # P(defender punishes entry)
    p_defense_quality = min(1.0, defender_str_def / 65.0)   # P(exchanges hurt shooter)
    p_reach_penalty = min(0.30, reach_gap / 70.0)           # P(reach gap blocks clinch)

    # Union of defender's entry-prevention mechanisms
    p_entry_blocked = 1.0 - (1.0 - p_striking_threat * 0.45) * (1.0 - p_defense_quality * 0.35) * (1.0 - p_reach_penalty * 0.20)

    # P(entry succeeds) = 1 - P(entry blocked), floored at 0.25
    return max(0.25, 1.0 - p_entry_blocked * 0.6)


def detect_fragility_flags(profile: Dict[str, Any], opponent_profile: Dict[str, Any] | None = None) -> tuple[bool, float]:
    """
    Detect brittle fighters or dangerous disparities.
    Returns: (has_fragility, uncertainty_multiplier)
    
    Fragility indicators:
    - Absorbs high incoming strikes (SApM > 5.5) + low StrDef
    - Recently suffered KOs (inferred from late losses)
    - Age 35+ in lower weight classes
    - History suggests control vulnerability
    
    When fragile: expand uncertainty slightly (multiply by >1.0)
    This prevents overconfident predictions on brittle matchups.
    """
    sapm = float(profile.get("sapm", 0.0) or 0.0)
    str_def = float(profile.get("str_def", 0.0) or 0.0)
    losses = int(profile.get("losses", 0) or 0)
    dob = profile.get("dob")
    
    uncertainty_mult = 1.0
    fragile = False
    
    if sapm > 5.5 and str_def < 42.0:
        fragile = True
        uncertainty_mult *= 1.08
    
    if dob and isinstance(dob, date):
        age = (date.today() - dob).days / 365.25
        if age > 36 and losses >= 5:
            fragile = True
            uncertainty_mult *= 1.06
    
    if losses >= 4:
        fragile = True
        uncertainty_mult *= 1.04
    
    if opponent_profile:
        opp_td_avg = float(opponent_profile.get("td_avg", 0.0) or 0.0)
        own_td_def = float(profile.get("td_def", 0.0) or 0.0)
        if opp_td_avg > 3.0 and own_td_def < 50.0:
            fragile = True
            uncertainty_mult *= 1.05
    
    return fragile, uncertainty_mult


def matchup_score(profile_a: Dict[str, Any], profile_b: Dict[str, Any], weight_class_name: str) -> tuple[float, float]:
    """
    Enhanced matchup scoring with Phase 1 interaction logic.
    Incorporates:
    - Nonlinear TDD/StrDef liability (exploitability)
    - Path-to-victory bonuses (wrestler vs low TDD, power striker vs low StrDef)
    - Power score integration (separates volume from power)
    - Archetype-aware weighting
    """
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

    matchup_bonus = 0.0
    raw_bonus = 0.0
    
    # -----------------------------------------------------------------------
    # All interaction signals defined as named probabilities (0.0–1.0)
    # -----------------------------------------------------------------------

    tdd_liability_a = tdd_liability(td_def_a)   # P(A gets controlled given TD lands)
    tdd_liability_b = tdd_liability(td_def_b)   # P(B gets controlled given TD lands)

    str_def_liability_a = str_def_liability(str_def_a)  # P(A is hurt in striking exchanges)
    str_def_liability_b = str_def_liability(str_def_b)  # P(B is hurt in striking exchanges)

    power_score_a = compute_power_score(profile_a)   # P(A creates meaningful damage)
    power_score_b = compute_power_score(profile_b)

    finisher_score_a = compute_finisher_score(profile_a)  # P(A converts damage to stoppage)
    finisher_score_b = compute_finisher_score(profile_b)

    anti_wrestling_a = compute_anti_wrestling_score(profile_a)  # P(A avoids/escapes control)
    anti_wrestling_b = compute_anti_wrestling_score(profile_b)

    # P(attacker has chain wrestling game plan) — scales with TD volume
    p_chain_a = min(1.0, td_avg_a / 4.5)
    p_chain_b = min(1.0, td_avg_b / 4.5)

    # P(entry attempt succeeds against specific defender)
    p_entry_a = compute_wrestling_entry_factor(profile_a, profile_b)
    p_entry_b = compute_wrestling_entry_factor(profile_b, profile_a)

    # P(control succeeds given TD lands) = vulnerability * P(can't escape)
    p_control_b_vs_a = tdd_liability_b * (1.0 - anti_wrestling_b * 0.45)   # B gets stuck
    p_control_a_vs_b = tdd_liability_a * (1.0 - anti_wrestling_a * 0.45)   # A gets stuck

    # P(attack initiates): union of chain game plan + opportunistic entry
    # Union prevents collapse when one factor is near-zero (e.g. Ivan barely shoots)
    # 1 - (1-A)(1-B) ensures even non-wrestlers have floor grappling pressure
    p_initiate_a = 1.0 - (1.0 - p_chain_a) * (1.0 - p_entry_a * 0.15)
    p_initiate_b = 1.0 - (1.0 - p_chain_b) * (1.0 - p_entry_b * 0.15)

    # Effective TDD pressure: union of initiation paths * control success
    # Floor at 0.05 — even strikers create occasional grappling obligation
    effective_tdd_pressure_b_vs_a = max(0.05, 1.0 - (1.0 - p_initiate_a) * (1.0 - p_control_b_vs_a * p_chain_a))
    effective_tdd_pressure_a_vs_b = max(0.05, 1.0 - (1.0 - p_initiate_b) * (1.0 - p_control_a_vs_b * p_chain_b))

    if td_avg_a > 4.0 and td_def_b < 55.0:
        raw_bonus += 0.50 * effective_tdd_pressure_b_vs_a
    elif td_avg_a > 3.0 and td_def_b < 65.0:
        raw_bonus += 0.30 * effective_tdd_pressure_b_vs_a

    if td_avg_b > 4.0 and td_def_a < 55.0:
        raw_bonus -= 0.50 * effective_tdd_pressure_a_vs_b
    elif td_avg_b > 3.0 and td_def_a < 65.0:
        raw_bonus -= 0.30 * effective_tdd_pressure_a_vs_b

    if sub_avg_a > 1.0 and td_def_b < 60.0:
        raw_bonus += 0.35 * effective_tdd_pressure_b_vs_a

    if sub_avg_b > 1.0 and td_def_a < 60.0:
        raw_bonus -= 0.35 * effective_tdd_pressure_a_vs_b

    if power_score_a > 0.70 and str_def_b < 45.0:
        raw_bonus += 0.45 * str_def_liability_b
        if sapm_b > 5.5:
            raw_bonus += 0.15 * str_def_liability_b
    elif power_score_a > 0.60 and str_def_b < 50.0:
        raw_bonus += 0.25 * str_def_liability_b

    if power_score_b > 0.70 and str_def_a < 45.0:
        raw_bonus -= 0.45 * str_def_liability_a
        if sapm_a > 5.5:
            raw_bonus -= 0.15 * str_def_liability_a
    elif power_score_b > 0.60 and str_def_a < 50.0:
        raw_bonus -= 0.25 * str_def_liability_a

    # Anti-wrestling global dampening — symmetric directional signal
    raw_bonus += ((1.0 - anti_wrestling_b) - (1.0 - anti_wrestling_a)) * 0.06

    matchup_bonus = apply_diminishing_returns(raw_bonus, compression_factor=0.8)

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

    total = ((striking_edge * 0.52) + (grappling_edge * 0.33) + (reach_combo_bonus * 0.75) + 
             age_adjust + (matchup_bonus * 0.35))
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
    favored_is_a = favored == fighter_a

    support_factors: list[tuple[float, str]] = []
    risk_factors: list[tuple[float, str]] = []

    def add_factor(score_for_a: float, text_when_a: str, text_when_b: str, weight: float = 1.0) -> None:
        if score_for_a == 0.0:
            return
        strength = abs(score_for_a) * weight
        if score_for_a > 0:
            text = text_when_a
            supports_favored = favored_is_a
        else:
            text = text_when_b
            supports_favored = not favored_is_a
        if supports_favored:
            support_factors.append((strength, text))
        else:
            risk_factors.append((strength, text))

    def add_named_risk(target_name: str, text: str, strength: float) -> None:
        if strength <= 0:
            return
        if target_name == favored:
            risk_factors.append((strength, text))
        else:
            support_factors.append((strength, text))

    metrics_a = profile_metrics(profile_a) if profile_a else {}
    metrics_b = profile_metrics(profile_b) if profile_b else {}
    is_heavy = is_heavy_division(weight_class_name)

    if metrics_a and metrics_b:
        win_rate_diff = metrics_a["win_rate"] - metrics_b["win_rate"]
        if abs(win_rate_diff) >= 0.04:
            add_factor(
                win_rate_diff,
                f"{fighter_a} has the stronger historical win rate",
                f"{fighter_b} has the stronger historical win rate",
            )

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
            combo_signal = (reach_diff / 50.0) + (strike_edge / 3.0)
            add_factor(
                combo_signal,
                f"{fighter_a}'s reach + striking edge creates stronger distance control",
                f"{fighter_b}'s reach + striking edge creates stronger distance control",
            )
        elif abs(reach_diff) >= 7.0:
            add_factor(
                reach_diff,
                f"{fighter_a} has a notable reach advantage",
                f"{fighter_b} has a notable reach advantage",
                weight=(1.0 / 130.0),
            )

        if abs(grappling_signal) >= 0.35:
            add_factor(
                grappling_signal,
                f"{fighter_a} projects stronger mat control and grappling pressure",
                f"{fighter_b} projects stronger mat control and grappling pressure",
            )

        wrestle_threat_a = max(0.0, td_avg_a - 1.0) + (max(0.0, sub_avg_a - 0.2) * 0.7)
        wrestle_threat_b = max(0.0, td_avg_b - 1.0) + (max(0.0, sub_avg_b - 0.2) * 0.7)
        if wrestle_threat_b >= 0.8 and td_def_a <= 58.0:
            add_named_risk(
                fighter_a,
                f"{fighter_a} could be vulnerable to {fighter_b}'s takedown pressure due to lower TD defense",
                wrestle_threat_b / 2.4,
            )
        if wrestle_threat_a >= 0.8 and td_def_b <= 58.0:
            add_named_risk(
                fighter_b,
                f"{fighter_b} could be vulnerable to {fighter_a}'s takedown pressure due to lower TD defense",
                wrestle_threat_a / 2.4,
            )

        if td_avg_a >= 1.6 and str_def_a <= 52.0:
            add_named_risk(fighter_a, f"{fighter_a}'s lower striking defense adds risk in stand-up exchanges", 0.22)
        if td_avg_b >= 1.6 and str_def_b <= 52.0:
            add_named_risk(fighter_b, f"{fighter_b}'s lower striking defense adds risk in stand-up exchanges", 0.22)

        exp_diff = metrics_a["total_fights"] - metrics_b["total_fights"]
        if abs(exp_diff) >= 5:
            add_factor(
                exp_diff,
                f"{fighter_a} has more pro fight experience",
                f"{fighter_b} has more pro fight experience",
                weight=(1.0 / 50.0),
            )

        sos_diff = metrics_a["sos_score"] - metrics_b["sos_score"]
        if abs(sos_diff) >= 0.05:
            add_factor(
                sos_diff,
                f"{fighter_a} has faced tougher recent opposition in the last 5 opponents (SoS)",
                f"{fighter_b} has faced tougher recent opposition in the last 5 opponents (SoS)",
                weight=1.9,
            )

        age_a = metrics_a.get("age_years")
        age_b = metrics_b.get("age_years")
        if isinstance(age_a, float) and isinstance(age_b, float) and not math.isnan(age_a) and not math.isnan(age_b):
            older_name = fighter_a if age_a > age_b else fighter_b
            younger_name = fighter_b if older_name == fighter_a else fighter_a
            older_age = max(age_a, age_b)
            younger_age = min(age_a, age_b)
            if older_age >= 37 and younger_age <= 33 and (older_age - younger_age) >= 5:
                weight_note = " (lighter division)" if not is_heavy else ""
                if older_name == favored:
                    risk_factors.append((0.18, f"Age gap is substantial: {older_name} is in late-career range vs {younger_name}{weight_note}"))
                else:
                    support_factors.append((0.18, f"Age gap is substantial: {older_name} is in late-career range vs {younger_name}{weight_note}"))

    support_factors.append((abs(prob_final_a - 0.5), f"Model confidence leans toward {favored}"))
    if prob_profile_a is not None:
        profile_supports_favored = (prob_profile_a >= 0.5 and favored_is_a) or (prob_profile_a < 0.5 and not favored_is_a)
        if profile_supports_favored:
            support_factors.append((abs(prob_profile_a - 0.5), "Profile matchup signal supports the lean"))
        else:
            risk_factors.append((abs(prob_profile_a - 0.5), f"Profile matchup signal leans toward {underdog}"))

    model_supports_favored = (prob_model_a >= 0.5 and favored_is_a) or (prob_model_a < 0.5 and not favored_is_a)
    if model_supports_favored:
        support_factors.append((abs(prob_model_a - 0.5), "Baseline trained model output contributes to the pick"))
    else:
        risk_factors.append((abs(prob_model_a - 0.5), f"Baseline trained model output leans toward {underdog}"))

    top_support = [item[1] for item in sorted(support_factors, key=lambda item: item[0], reverse=True)[:2]]
    top_risk = [f"Risk: {item[1]}" for item in sorted(risk_factors, key=lambda item: item[0], reverse=True)[:1]]
    factors_sorted = top_support + top_risk
    if len(factors_sorted) < 3:
        remaining_support = [item[1] for item in sorted(support_factors, key=lambda item: item[0], reverse=True)[2:]]
        remaining_risk = [f"Risk: {item[1]}" for item in sorted(risk_factors, key=lambda item: item[0], reverse=True)[1:]]
        factors_sorted = (factors_sorted + remaining_support + remaining_risk)[:3]

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


def detect_fight_regime(profile_a: Dict[str, Any], profile_b: Dict[str, Any]) -> tuple[str, str | None]:
    """
    Classify the dominant fight mode and which side holds the structural edge.

    Regimes:
    - 'wrestling_control': chain wrestler vs opponent with collapse-zone TDD
    - 'submission_threat': submission specialist vs poor TDD
    - 'striking_exchange': power striker vs poor striking defense
    - 'contested': no clear structural mismatch

    Returns (regime, dominant_side) where dominant_side is 'a', 'b', or None.
    Used to set dominance_mult in logit-space assembly.
    """
    td_avg_a = float(profile_a.get("td_avg", 0.0) or 0.0)
    td_avg_b = float(profile_b.get("td_avg", 0.0) or 0.0)
    td_def_a = float(profile_a.get("td_def", 0.0) or 0.0)
    td_def_b = float(profile_b.get("td_def", 0.0) or 0.0)
    sub_avg_a = float(profile_a.get("sub_avg", 0.0) or 0.0)
    sub_avg_b = float(profile_b.get("sub_avg", 0.0) or 0.0)
    str_def_a = float(profile_a.get("str_def", 0.0) or 0.0)
    str_def_b = float(profile_b.get("str_def", 0.0) or 0.0)

    # Wrestling dominance score: chain volume * TDD vulnerability of opponent
    wrestle_a = (td_avg_a / 4.5) * tdd_liability(td_def_b)
    wrestle_b = (td_avg_b / 4.5) * tdd_liability(td_def_a)

    # Submission dominance: sub threat * opponent's TDD vulnerability
    sub_a = sub_avg_a * tdd_liability(td_def_b)
    sub_b = sub_avg_b * tdd_liability(td_def_a)

    # Striking dominance: power * opponent's defense vulnerability
    power_a = compute_power_score(profile_a)
    power_b = compute_power_score(profile_b)
    strike_a = power_a * str_def_liability(str_def_b)
    strike_b = power_b * str_def_liability(str_def_a)

    WRESTLE_THRESH = 0.18   # lowered: soft wrestling control still triggers regime
    SUB_THRESH = 0.25        # lowered: partial sub threat still biases fight mode
    STRIKE_THRESH = 0.18     # lowered: partial striking mismatch still triggers
    EDGE_RATIO = 1.25        # lowered: 25% edge is enough — not 40%

    if wrestle_a >= WRESTLE_THRESH and wrestle_a > wrestle_b * EDGE_RATIO:
        return 'wrestling_control', 'a'
    if wrestle_b >= WRESTLE_THRESH and wrestle_b > wrestle_a * EDGE_RATIO:
        return 'wrestling_control', 'b'
    if sub_a >= SUB_THRESH and sub_a > sub_b * EDGE_RATIO:
        return 'submission_threat', 'a'
    if sub_b >= SUB_THRESH and sub_b > sub_a * EDGE_RATIO:
        return 'submission_threat', 'b'
    if strike_a >= STRIKE_THRESH and strike_a > strike_b * EDGE_RATIO:
        return 'striking_exchange', 'a'
    if strike_b >= STRIKE_THRESH and strike_b > strike_a * EDGE_RATIO:
        return 'striking_exchange', 'b'
    return 'contested', None


def compute_logit_components(
    profile_a: Dict[str, Any],
    profile_b: Dict[str, Any],
    weight_class_name: str,
) -> tuple[float, float, float, float]:
    """
    Compute structurally-separated logit adjustments for logit-space final assembly.

    Returns (striking_logit, grappling_logit, sub_logit, dominance_mult).
    All logit values are added to logit(p_model); dominance_mult scales the sum.

    Fixes:
    - No single linear container — each fight dimension is independent
    - dominance_mult > 1.0 expands separation when a structural mismatch exists
    - Natural curve behavior: MMA fights can collapse into one dominant mode
    """
    slpm_a  = float(profile_a.get("slpm",  0.0) or 0.0)
    slpm_b  = float(profile_b.get("slpm",  0.0) or 0.0)
    sapm_a  = float(profile_a.get("sapm",  0.0) or 0.0)
    sapm_b  = float(profile_b.get("sapm",  0.0) or 0.0)
    str_def_a = float(profile_a.get("str_def", 0.0) or 0.0)
    str_def_b = float(profile_b.get("str_def", 0.0) or 0.0)
    td_avg_a  = float(profile_a.get("td_avg",  0.0) or 0.0)
    td_avg_b  = float(profile_b.get("td_avg",  0.0) or 0.0)
    sub_avg_a = float(profile_a.get("sub_avg", 0.0) or 0.0)
    sub_avg_b = float(profile_b.get("sub_avg", 0.0) or 0.0)
    td_def_a  = float(profile_a.get("td_def",  0.0) or 0.0)
    td_def_b  = float(profile_b.get("td_def",  0.0) or 0.0)
    reach_a   = float(profile_a.get("reach_cm", 175.0) or 175.0)
    reach_b   = float(profile_b.get("reach_cm", 175.0) or 175.0)

    # --- Striking logit ---
    # Each unit of SLpM edge ≈ 0.18 logit. StrDef edge ≈ 0.30 logit per 1.0 diff.
    slpm_edge = (slpm_a - slpm_b) * 0.18
    def_edge  = ((str_def_a - str_def_b) / 100.0) * 0.30
    absorb_edge = (sapm_b - sapm_a) * 0.12
    reach_diff = (reach_a - reach_b) / 10.0
    # Reach bonus only matters when there's already a striking edge
    reach_bonus = reach_diff * max(0.0, (slpm_edge + def_edge) * 0.15)
    striking_logit = slpm_edge + def_edge + absorb_edge + reach_bonus

    # --- Grappling logit (effective pressure differential) ---
    tdd_lib_a = tdd_liability(td_def_a)
    tdd_lib_b = tdd_liability(td_def_b)
    anti_w_a  = compute_anti_wrestling_score(profile_a)
    anti_w_b  = compute_anti_wrestling_score(profile_b)
    entry_a   = compute_wrestling_entry_factor(profile_a, profile_b)
    entry_b   = compute_wrestling_entry_factor(profile_b, profile_a)
    p_chain_a = min(1.0, td_avg_a / 4.5)
    p_chain_b = min(1.0, td_avg_b / 4.5)
    p_initiate_a = 1.0 - (1.0 - p_chain_a) * (1.0 - entry_a * 0.15)
    p_initiate_b = 1.0 - (1.0 - p_chain_b) * (1.0 - entry_b * 0.15)
    p_control_b  = tdd_lib_b * (1.0 - anti_w_b * 0.45)
    p_control_a  = tdd_lib_a * (1.0 - anti_w_a * 0.45)
    eff_a_vs_b = max(0.05, 1.0 - (1.0 - p_initiate_a) * (1.0 - p_control_b * p_chain_a))
    eff_b_vs_a = max(0.05, 1.0 - (1.0 - p_initiate_b) * (1.0 - p_control_a * p_chain_b))
    # Pressure differential → logit. Full 0-to-1 range maps to ~1.20 logit swing.
    grappling_logit = (eff_a_vs_b - eff_b_vs_a) * 1.20

    # --- Submission logit ---
    # Sub threat conditioned on whether opponent can even be brought down
    sub_threat_a = sub_avg_a * tdd_lib_b   # more dangerous vs bad TDD
    sub_threat_b = sub_avg_b * tdd_lib_a
    sub_logit = (sub_threat_a - sub_threat_b) * 0.30

    # --- Interaction term (conditional explosion) ---
    # Power striker vs bad defense vs bad TDD = multiplicative collapse, not additive
    # This is the single highest-impact missing term.
    interaction_a = compute_power_score(profile_a) * str_def_liability(str_def_b) * tdd_lib_b
    interaction_b = compute_power_score(profile_b) * str_def_liability(str_def_a) * tdd_lib_a
    interaction_logit = (interaction_a - interaction_b) * 1.5

    # --- Regime-based dominance multiplier ---
    # Non-contested fights should naturally produce wider separation.
    # Multiplying the full logit (model + components) compresses contested fights
    # and expands dominant ones — mathematically correct via logit curve shape.
    regime, dominant = detect_fight_regime(profile_a, profile_b)
    if regime == 'wrestling_control':
        dominance_mult = 1.90
    elif regime == 'submission_threat':
        dominance_mult = 1.80
    elif regime == 'striking_exchange':
        dominance_mult = 1.55
    else:  # contested
        dominance_mult = 1.05

    return striking_logit, grappling_logit, sub_logit, interaction_logit, dominance_mult


def calibrate_probability(probability: float) -> float:
    # Legacy — kept for fallback (no-profile) path only.
    # Do NOT use in the logit-space assembly path.
    return max(0.01, min(0.99, probability))


def apply_matchup_correction(prob_model_a: float, prob_profile_a: float | None) -> tuple[float, float]:
    if prob_profile_a is None:
        return prob_model_a, 0.0

    raw_delta = prob_profile_a - prob_model_a
    model_extremeness = min(1.0, abs(prob_model_a - 0.5) * 2.0)
    correction_scale = 0.10 + (0.04 * model_extremeness)
    correction = math.tanh(raw_delta * 1.1) * correction_scale
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

        profile_a = parse_fighter_profile(fighter_a_url, profile_cache)
        profile_b = parse_fighter_profile(fighter_b_url, profile_cache)
        if not profile_a or not profile_b:
            missing_profile_fights += 1

        if profile_a and profile_b:
            payload = build_model_feature_payload(fighter_a, fighter_b, profile_a, profile_b)
        else:
            payload = {
                "fighterA": fighter_a,
                "fighterB": fighter_b,
            }

        winner, probabilities = predict_fight(payload)

        prob_model_a_raw = float(probabilities.get(fighter_a, 0.5))
        prob_model_a = calibrate_probability(prob_model_a_raw)

        feature_signatures.append(build_feature_signature(profile_a or {}, profile_b or {}, weight_class_name))

        prob_profile_a = None
        uncertainty_factor = 1.0
        if profile_a and profile_b:
            # -----------------------------------------------------------------
            # Logit-space assembly (Phase 2 — pure logit, no p-space blending)
            # -----------------------------------------------------------------
            # Work directly from raw model logit — no p-space calibration shrinkage.
            # ML model (logistic regression) outputs are already logit-calibrated.
            # A mild 0.85 scale accounts for model overconfidence without
            # collapsing variance the way ((p-0.5)*0.6)+0.5 did in p-space.
            prob_model_a_raw_clamped = max(0.01, min(0.99, prob_model_a_raw))
            logit_base = math.log(prob_model_a_raw_clamped / (1.0 - prob_model_a_raw_clamped)) * 0.85

            # Get structurally-separated logit components + interaction term
            striking_logit, grappling_logit, sub_logit, interaction_logit, dominance_mult = compute_logit_components(
                profile_a, profile_b, weight_class_name
            )

            # Age adjustment in logit space
            age_adjust_logit = 0.0
            age_a = fighter_age(profile_a)
            age_b = fighter_age(profile_b)
            if age_a is not None and age_b is not None:
                heavier = is_heavy_division(weight_class_name)
                age_gap = age_a - age_b
                if age_a >= 37 and age_b <= 33 and age_gap >= 5:
                    age_adjust_logit = -0.35 if heavier else -0.55
                elif age_b >= 37 and age_a <= 33 and (-age_gap) >= 5:
                    age_adjust_logit = 0.35 if heavier else 0.55

            # Uncertainty in logit space — dampens logit magnitude, not probability
            uncertainty_factor = compute_uncertainty_factor(profile_a, profile_b, weight_class_name)

            # Assemble all in logit space: one sigmoid at the very end
            logit_components = striking_logit + grappling_logit + sub_logit + interaction_logit + age_adjust_logit
            logit_p = (logit_base + logit_components) * dominance_mult * uncertainty_factor

            prob_a = sigmoid(logit_p)
            prob_profile_a = sigmoid(logit_components)  # for explanation layer only
            matchup_correction = logit_p - logit_base   # for calibration logging
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
                "uncertainty_factor": round(uncertainty_factor, 6),
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
