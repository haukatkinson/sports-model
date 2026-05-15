from __future__ import annotations

import argparse
import csv
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

import sys

ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "api"
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

import build_event_predictions as bp  # noqa: E402
from predict import predict_fight  # noqa: E402

FIGHTS_PATH = ROOT / "data" / "nearest_event_fights.csv"
DEBUG_CSV_PATH = ROOT / "data" / "debug_fight_scores.csv"
HISTORY_PATH = ROOT / "data" / "prediction_history.csv"


def normalize_name(value: str) -> str:
    return " ".join((value or "").strip().lower().split())


def implied_probability_from_odds(american_odds: float | None) -> float | None:
    if american_odds is None:
        return None
    odds = float(american_odds)
    if odds == 0.0:
        return None
    if odds > 0:
        return 100.0 / (odds + 100.0)
    return abs(odds) / (abs(odds) + 100.0)


def confidence_bucket(probability: float) -> str:
    clamped = max(0.0, min(0.999999, float(probability)))
    lower = int(math.floor(clamped * 20.0) * 5)
    upper = min(100, lower + 5)
    return f"{lower}-{upper}%"


def historical_bucket_winrate(bucket: str) -> tuple[float | None, int]:
    if not HISTORY_PATH.exists():
        return None, 0

    wins = 0
    total = 0
    with HISTORY_PATH.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            actual_winner = (row.get("actual_winner") or "").strip()
            fighter_a = (row.get("fighterA") or "").strip()
            fighter_b = (row.get("fighterB") or "").strip()
            model_prob_raw = (row.get("model_prob") or "").strip()
            if not actual_winner or not fighter_a or not fighter_b or not model_prob_raw:
                continue
            try:
                model_prob = float(model_prob_raw)
            except ValueError:
                continue

            row_bucket = confidence_bucket(model_prob)
            if row_bucket != bucket:
                continue

            total += 1
            if normalize_name(actual_winner) == normalize_name(fighter_a):
                wins += 1

    if total == 0:
        return None, 0
    return wins / total, total


def find_fight_row(fighter_a: str, fighter_b: str) -> Dict[str, str]:
    with FIGHTS_PATH.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            row_a = normalize_name(str(row.get("fighterA", "")))
            row_b = normalize_name(str(row.get("fighterB", "")))
            if row_a == normalize_name(fighter_a) and row_b == normalize_name(fighter_b):
                return dict(row)
    raise ValueError(f"Fight not found in {FIGHTS_PATH}: {fighter_a} vs {fighter_b}")


def compute_age_adjust_logit(profile_a: Dict[str, Any], profile_b: Dict[str, Any], weight_class_name: str) -> float:
    age_adjust_logit = 0.0
    age_a = bp.fighter_age(profile_a)
    age_b = bp.fighter_age(profile_b)
    if age_a is not None and age_b is not None:
        heavier = bp.is_heavy_division(weight_class_name)
        age_gap = age_a - age_b
        if age_a >= 37 and age_b <= 33 and age_gap >= 5:
            age_adjust_logit = -0.35 if heavier else -0.55
        elif age_b >= 37 and age_a <= 33 and (-age_gap) >= 5:
            age_adjust_logit = 0.35 if heavier else 0.55
    return age_adjust_logit


def append_debug_csv(row: Dict[str, Any]) -> None:
    headers = list(row.keys())
    exists = DEBUG_CSV_PATH.exists()
    DEBUG_CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    with DEBUG_CSV_PATH.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        if not exists:
            writer.writeheader()
        writer.writerow(row)


def main() -> None:
    parser = argparse.ArgumentParser(description="Detailed fight score diagnostics with CSV logging")
    parser.add_argument("--fighter-a", default="Dooho Choi")
    parser.add_argument("--fighter-b", default="Daniel Santos")
    parser.add_argument("--odds-a", type=float, default=None, help="American odds for fighter A")
    args = parser.parse_args()

    fight_row = find_fight_row(args.fighter_a, args.fighter_b)
    fighter_a = str(fight_row.get("fighterA", "")).strip()
    fighter_b = str(fight_row.get("fighterB", "")).strip()
    fighter_a_url = str(fight_row.get("fighterA_url", "")).strip()
    fighter_b_url = str(fight_row.get("fighterB_url", "")).strip()
    weight_class_name = str(fight_row.get("weight_class_name", "")).strip()

    cache: Dict[str, Dict[str, Any]] = {}
    profile_a = bp.parse_fighter_profile(fighter_a_url, cache)
    profile_b = bp.parse_fighter_profile(fighter_b_url, cache)

    payload = bp.build_model_feature_payload(fighter_a, fighter_b, profile_a, profile_b)
    _, probabilities = predict_fight(payload)

    prob_model_a_raw = float(probabilities.get(fighter_a, 0.5))
    prob_model_a_clamped = max(0.01, min(0.99, prob_model_a_raw))

    base_logit_raw = math.log(prob_model_a_clamped / (1.0 - prob_model_a_clamped))
    logit_base = base_logit_raw * 0.25  # ML prior = ~25% of signal in true logit space

    logit_details = bp.compute_logit_components_detailed(profile_a, profile_b, weight_class_name)
    main_logit      = float(logit_details.get("main_logit",      0.0) or 0.0)
    support_logit   = float(logit_details.get("support_logit",   0.0) or 0.0)
    regime_strength = float(logit_details.get("regime_strength", 1.0) or 1.0)
    regime_weakness = float(logit_details.get("regime_weakness", 1.0) or 1.0)
    interaction_logit = float(logit_details.get("interaction_logit", 0.0) or 0.0)

    age_adjust_logit = compute_age_adjust_logit(profile_a, profile_b, weight_class_name)
    uncertainty_factor = bp.compute_uncertainty_factor(profile_a, profile_b, weight_class_name)

    logit_components = main_logit * regime_strength + support_logit * regime_weakness + interaction_logit + age_adjust_logit

    # Regime strength is already embedded in main/support assembly.
    # Apply uncertainty only to the prior to avoid draw-magnet compression.
    logit_p = logit_components + (logit_base * uncertainty_factor)

    # Probability mapping: apply logit directly to sigmoid.
    # No scaling factor; let simulation logit drive the probability.
    prob_a = max(0.01, min(0.99, bp.sigmoid(logit_p)))
    prob_b = 1.0 - prob_a

    market_prob_a = implied_probability_from_odds(args.odds_a)
    model_market_gap = (prob_a - market_prob_a) if market_prob_a is not None else None

    bucket = confidence_bucket(prob_a)
    bucket_winrate, bucket_samples = historical_bucket_winrate(bucket)

    archetype_a = bp.classify_archetype(profile_a)
    archetype_b = bp.classify_archetype(profile_b)

    regime_scores = dict(logit_details.get("regime_scores", {}))

    print()
    print("SCORE_DIAGNOSTICS")
    print(f"FIGHT {fighter_a} vs {fighter_b}")
    print(f"WEIGHT_CLASS {weight_class_name}")
    print(f"RAW_MODEL_PROB_A {prob_model_a_raw:.6f}")
    print(f"FINAL_PROB_A {prob_a:.6f}")
    print(f"FINAL_PROB_B {prob_b:.6f}")
    print()
    print("DOMAIN_LOGITS")
    print(f"STRIKING_LOGIT {float(logit_details.get('striking_logit', 0.0) or 0.0):+.6f}")
    print(f"GRAPPLING_LOGIT {float(logit_details.get('grappling_logit', 0.0) or 0.0):+.6f}")
    print(f"SUBMISSION_LOGIT {float(logit_details.get('submission_logit', 0.0) or 0.0):+.6f}")
    print(f"INTERACTION_LOGIT {interaction_logit:+.6f}")
    print(f"ROUND_WIN_LOGIT {float(logit_details.get('round_win_logit', 0.0) or 0.0):+.6f}")
    print(f"MAIN_LOGIT {main_logit:+.6f}  [regime path]")
    print(f"SUPPORT_LOGIT {support_logit:+.6f}  [compressed non-dominant]")
    print(f"REGIME_STRENGTH {regime_strength:.4f}")
    print(f"REGIME_WEAKNESS {regime_weakness:.4f}")
    print(f"ASSEMBLED_PATH {main_logit * regime_strength + support_logit * regime_weakness:+.6f}")
    print()
    print("REGIME")
    print(f"DOM_REGIME {str(logit_details.get('regime', 'contested'))}")
    print("REGIME_SCORES")
    print(f"STRIKING_REGIME_SCORE {float(regime_scores.get('striking_regime_score', 0.0) or 0.0):+.6f}")
    print(f"WRESTLING_REGIME_SCORE {float(regime_scores.get('wrestling_regime_score', 0.0) or 0.0):+.6f}")
    print(f"SUBMISSION_REGIME_SCORE {float(regime_scores.get('submission_regime_score', 0.0) or 0.0):+.6f}")
    print(f"CONTESTED_SCORE {float(regime_scores.get('contested_score', 0.0) or 0.0):.6f}")
    print()
    print("PRESSURE")
    print(f"EFF_PRESSURE_A_TO_B {float(logit_details.get('effective_pressure_a_to_b', 0.0) or 0.0):.6f}")
    print(f"EFF_PRESSURE_B_TO_A {float(logit_details.get('effective_pressure_b_to_a', 0.0) or 0.0):.6f}")
    print()
    print("ENTRY_GATING")
    print(f"ENTRY_PROB_A {float(logit_details.get('entry_prob_a', 1.0) or 1.0):.4f}  [{fighter_a} grappling entry reliability]")
    print(f"ENTRY_PROB_B {float(logit_details.get('entry_prob_b', 1.0) or 1.0):.4f}  [{fighter_b} grappling entry reliability]")
    print()
    print("ROUND_WINNING")
    print(f"ROUND_WIN_SCORE_A {float(logit_details.get('round_win_score_a', 0.0) or 0.0):.4f}  [{fighter_a} minute-winning profile]")
    print(f"ROUND_WIN_SCORE_B {float(logit_details.get('round_win_score_b', 0.0) or 0.0):.4f}  [{fighter_b} minute-winning profile]")
    print()
    print("RAW_VS_STABILIZED_A")
    print(f"RAW_SLPM {float(profile_a.get('raw_slpm', 0.0) or 0.0):.6f}")
    print(f"STABILIZED_SLPM {float(profile_a.get('stabilized_slpm', 0.0) or 0.0):.6f}")
    print(f"RAW_TD_AVG {float(profile_a.get('raw_td_avg', 0.0) or 0.0):.6f}")
    print(f"STABILIZED_TD_AVG {float(profile_a.get('stabilized_td_avg', 0.0) or 0.0):.6f}")
    print(f"RAW_KD_PER15 {float(profile_a.get('raw_kd_per15', 0.0) or 0.0):.6f}")
    print(f"STABILIZED_KD_PER15 {float(profile_a.get('stabilized_kd_per15', 0.0) or 0.0):.6f}")
    print()
    print("RAW_VS_STABILIZED_B")
    print(f"RAW_SLPM {float(profile_b.get('raw_slpm', 0.0) or 0.0):.6f}")
    print(f"STABILIZED_SLPM {float(profile_b.get('stabilized_slpm', 0.0) or 0.0):.6f}")
    print(f"RAW_TD_AVG {float(profile_b.get('raw_td_avg', 0.0) or 0.0):.6f}")
    print(f"STABILIZED_TD_AVG {float(profile_b.get('stabilized_td_avg', 0.0) or 0.0):.6f}")
    print(f"RAW_KD_PER15 {float(profile_b.get('raw_kd_per15', 0.0) or 0.0):.6f}")
    print(f"STABILIZED_KD_PER15 {float(profile_b.get('stabilized_kd_per15', 0.0) or 0.0):.6f}")
    print()
    print("ARCHETYPES")
    print(f"A_ARCHETYPE {archetype_a}")
    print(f"B_ARCHETYPE {archetype_b}")
    print()
    print("MARKET")
    if market_prob_a is None:
        print("MARKET_PROB_A NA")
        print("MODEL_MARKET_GAP NA")
    else:
        print(f"MARKET_PROB_A {market_prob_a:.6f}")
        print(f"MODEL_MARKET_GAP {model_market_gap:+.6f}")
    print()
    print("CALIBRATION")
    print(f"MODEL_BUCKET {bucket}")
    if bucket_winrate is None:
        print("HISTORICAL_ACTUAL NA")
        print("HISTORICAL_BUCKET_SAMPLES 0")
    else:
        print(f"HISTORICAL_ACTUAL {bucket_winrate:.6f}")
        print(f"HISTORICAL_BUCKET_SAMPLES {bucket_samples}")

    debug_row: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "event_name": str(fight_row.get("event_name", "")),
        "event_date": str(fight_row.get("event_date", "")),
        "fighterA": fighter_a,
        "fighterB": fighter_b,
        "weight_class_name": weight_class_name,
        "raw_model_prob_a": round(prob_model_a_raw, 6),
        "final_prob_a": round(prob_a, 6),
        "final_prob_b": round(prob_b, 6),
        "striking_logit": round(float(logit_details.get("striking_logit", 0.0) or 0.0), 6),
        "grappling_logit": round(float(logit_details.get("grappling_logit", 0.0) or 0.0), 6),
        "submission_logit": round(float(logit_details.get("submission_logit", 0.0) or 0.0), 6),
        "interaction_logit": round(interaction_logit, 6),
        "round_win_logit": round(float(logit_details.get("round_win_logit", 0.0) or 0.0), 6),
        "round_win_score_a": round(float(logit_details.get("round_win_score_a", 0.0) or 0.0), 6),
        "round_win_score_b": round(float(logit_details.get("round_win_score_b", 0.0) or 0.0), 6),
        "dominant_path_logit": round(main_logit, 6),
        "secondary_logit": round(support_logit, 6),
        "regime_multiplier": round(regime_strength, 6),
        "dom_regime": str(logit_details.get("regime", "contested")),
        "striking_regime_score": round(float(regime_scores.get("striking_regime_score", 0.0) or 0.0), 6),
        "wrestling_regime_score": round(float(regime_scores.get("wrestling_regime_score", 0.0) or 0.0), 6),
        "submission_regime_score": round(float(regime_scores.get("submission_regime_score", 0.0) or 0.0), 6),
        "contested_score": round(float(regime_scores.get("contested_score", 0.0) or 0.0), 6),
        "eff_pressure_a_to_b": round(float(logit_details.get("effective_pressure_a_to_b", 0.0) or 0.0), 6),
        "eff_pressure_b_to_a": round(float(logit_details.get("effective_pressure_b_to_a", 0.0) or 0.0), 6),
        "a_archetype": archetype_a,
        "b_archetype": archetype_b,
        "a_raw_slpm": round(float(profile_a.get("raw_slpm", 0.0) or 0.0), 6),
        "a_stabilized_slpm": round(float(profile_a.get("stabilized_slpm", 0.0) or 0.0), 6),
        "a_raw_td_avg": round(float(profile_a.get("raw_td_avg", 0.0) or 0.0), 6),
        "a_stabilized_td_avg": round(float(profile_a.get("stabilized_td_avg", 0.0) or 0.0), 6),
        "a_raw_kd_per15": round(float(profile_a.get("raw_kd_per15", 0.0) or 0.0), 6),
        "a_stabilized_kd_per15": round(float(profile_a.get("stabilized_kd_per15", 0.0) or 0.0), 6),
        "b_raw_slpm": round(float(profile_b.get("raw_slpm", 0.0) or 0.0), 6),
        "b_stabilized_slpm": round(float(profile_b.get("stabilized_slpm", 0.0) or 0.0), 6),
        "b_raw_td_avg": round(float(profile_b.get("raw_td_avg", 0.0) or 0.0), 6),
        "b_stabilized_td_avg": round(float(profile_b.get("stabilized_td_avg", 0.0) or 0.0), 6),
        "b_raw_kd_per15": round(float(profile_b.get("raw_kd_per15", 0.0) or 0.0), 6),
        "b_stabilized_kd_per15": round(float(profile_b.get("stabilized_kd_per15", 0.0) or 0.0), 6),
        "market_prob_a": None if market_prob_a is None else round(market_prob_a, 6),
        "model_market_gap": None if model_market_gap is None else round(model_market_gap, 6),
        "model_bucket": bucket,
        "historical_actual": None if bucket_winrate is None else round(bucket_winrate, 6),
        "historical_bucket_samples": bucket_samples,
    }

    append_debug_csv(debug_row)
    print()
    print(f"DEBUG_CSV_APPENDED {DEBUG_CSV_PATH}")


if __name__ == "__main__":
    main()
