from __future__ import annotations

import math
import os
import re
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Deque, Dict, List, Optional, Tuple

import mysql.connector
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT / "data" / "model_features.csv"


@dataclass
class FightSnapshot:
    event_date: date
    fighter_id: int
    opponent_id: int
    won: int
    method: str
    round_num: int
    duration_seconds: int
    sig_landed: int
    sig_attempted: int
    td_landed: int
    td_attempted: int
    knockdowns: int
    control_time_seconds: int
    opp_sig_landed: int
    opp_sig_attempted: int
    opp_td_landed: int
    opp_td_attempted: int
    opp_knockdowns: int


def get_connection():
    return mysql.connector.connect(
        host=os.getenv("DB_HOST", "localhost"),
        user=os.getenv("DB_USER", "user"),
        password=os.getenv("DB_PASS", "pass"),
        database=os.getenv("DB_NAME", "ufc_db"),
    )


def parse_fight_duration(round_num: int, time_in_round: Optional[str]) -> int:
    if round_num is None or round_num <= 0:
        return 0
    remaining = 0
    if time_in_round and ":" in time_in_round:
        mins, secs = time_in_round.split(":", 1)
        if mins.isdigit() and secs.isdigit():
            remaining = int(mins) * 60 + int(secs)
    return (max(round_num - 1, 0) * 300) + remaining


def is_ko_method(method: str) -> bool:
    return bool(re.search(r"\b(KO|TKO)\b", method or "", flags=re.IGNORECASE))


def is_finish_method(method: str) -> bool:
    return bool(re.search(r"\b(KO|TKO|Submission)\b", method or "", flags=re.IGNORECASE))


def safe_div(num: float, den: float) -> float:
    if den == 0:
        return 0.0
    return float(num) / float(den)


def age_on_day(birth: Optional[date], event_date: date) -> float:
    if not birth:
        return 0.0
    return (event_date - birth).days / 365.25


def prime_score(age: float) -> float:
    if age <= 0:
        return 0.0
    return math.exp(-(abs(age - 30.0) / 10.0))


def age_curve_penalty(age: float) -> float:
    if age <= 0:
        return 0.0
    return max(0.0, age - 33.0)


def aggregate_prefight_metrics(history: List[FightSnapshot]) -> Dict[str, float]:
    total_fights = len(history)
    if total_fights == 0:
        return {
            "strikes_per_min": 0.0,
            "strike_accuracy": 0.0,
            "takedown_defense": 0.0,
            "takedown_accuracy": 0.0,
            "win_streak": 0,
            "avg_fight_time_seconds": 0,
            "last_5_fights_wins": 0,
            "power_score": 0.0,
            "durability_score": 0.0,
            "grappling_score": 0.0,
            "cardio_score": 0.0,
            "sos_score": 0.0,
            "times_koed": 0,
            "times_knocked_down": 0,
            "ko_rate": 0.0,
            "late_round_finished_rate": 0.0,
        }

    total_sig_landed = sum(item.sig_landed for item in history)
    total_sig_attempted = sum(item.sig_attempted for item in history)
    total_td_landed = sum(item.td_landed for item in history)
    total_td_attempted = sum(item.td_attempted for item in history)
    total_ctrl = sum(item.control_time_seconds for item in history)
    total_duration = sum(item.duration_seconds for item in history)

    opp_td_landed = sum(item.opp_td_landed for item in history)
    opp_td_attempted = sum(item.opp_td_attempted for item in history)
    opp_sig_landed = sum(item.opp_sig_landed for item in history)

    wins = sum(item.won for item in history)
    losses = total_fights - wins
    wins_by_ko = sum(1 for item in history if item.won == 1 and is_ko_method(item.method))
    ko_losses = sum(1 for item in history if item.won == 0 and is_ko_method(item.method))

    knockdowns_for = sum(item.knockdowns for item in history)
    knockdowns_against = sum(item.opp_knockdowns for item in history)

    win_streak = 0
    for item in reversed(history):
        if item.won == 1:
            win_streak += 1
        else:
            break

    last_5 = history[-5:]
    last_5_wins = sum(item.won for item in last_5)

    late_losses = [item for item in history if item.won == 0 and item.round_num >= 3 and is_finish_method(item.method)]
    late_round_finished_rate = safe_div(len(late_losses), max(losses, 1))

    strikes_per_min = safe_div(total_sig_landed * 60.0, max(total_duration, 1))
    strike_accuracy = safe_div(total_sig_landed, max(total_sig_attempted, 1))
    takedown_accuracy = safe_div(total_td_landed, max(total_td_attempted, 1))
    takedown_defense = 1.0 - safe_div(opp_td_landed, max(opp_td_attempted, 1))
    avg_fight_time_seconds = int(safe_div(total_duration, total_fights))

    knockdowns_per_fight = safe_div(knockdowns_for, total_fights)
    ko_rate = safe_div(wins_by_ko, max(wins, 1))
    strike_power = safe_div(knockdowns_for, max(total_sig_landed, 1))
    power_score = (ko_rate * 0.5) + (knockdowns_per_fight * 0.3) + (strike_power * 0.2)

    finish_rate_against = safe_div(ko_losses, max(losses, 1))
    durability_score = math.exp(-finish_rate_against * 2.0) / (1.0 + (0.05 * knockdowns_against))

    control_time_per_min = safe_div(total_ctrl, max(total_duration, 1))
    grappling_score = (control_time_per_min * 0.4) + (takedown_accuracy * 0.3) + (max(takedown_defense, 0.0) * 0.3)

    late_round_wins = [item for item in history if item.won == 1 and item.round_num >= 3]
    late_round_win_rate = safe_div(len(late_round_wins), total_fights)
    cardio_score = (
        safe_div(avg_fight_time_seconds, 900.0) * 0.4
        + (late_round_win_rate * 0.4)
        - (late_round_finished_rate * 0.2)
    )

    return {
        "strikes_per_min": strikes_per_min,
        "strike_accuracy": strike_accuracy,
        "takedown_defense": max(takedown_defense, 0.0),
        "takedown_accuracy": takedown_accuracy,
        "win_streak": win_streak,
        "avg_fight_time_seconds": avg_fight_time_seconds,
        "last_5_fights_wins": last_5_wins,
        "power_score": power_score,
        "durability_score": durability_score,
        "grappling_score": grappling_score,
        "cardio_score": cardio_score,
        "times_koed": ko_losses,
        "times_knocked_down": knockdowns_against,
        "ko_rate": ko_rate,
        "late_round_finished_rate": late_round_finished_rate,
        "sos_score": 0.0,
        "wins": wins,
        "fights": total_fights,
        "sig_absorbed_per_fight": safe_div(opp_sig_landed, total_fights),
    }


def fetch_rows(conn) -> List[Dict]:
    query = """
    SELECT
        f.id AS fight_id,
        f.event_date,
        f.fighter_a_id,
        f.fighter_b_id,
        f.winner_id,
        f.method,
        f.round_num,
        f.time_in_round,
        f.weight_class_id,
        fa.date_of_birth AS fighter_a_dob,
        fb.date_of_birth AS fighter_b_dob,
        fa.reach_cm AS fighter_a_reach,
        fb.reach_cm AS fighter_b_reach,
        sa.sig_strikes_landed AS a_sig_landed,
        sa.sig_strikes_attempted AS a_sig_attempted,
        sa.takedowns_landed AS a_td_landed,
        sa.takedowns_attempted AS a_td_attempted,
        sa.knockdowns AS a_knockdowns,
        sa.control_time_seconds AS a_control,
        sb.sig_strikes_landed AS b_sig_landed,
        sb.sig_strikes_attempted AS b_sig_attempted,
        sb.takedowns_landed AS b_td_landed,
        sb.takedowns_attempted AS b_td_attempted,
        sb.knockdowns AS b_knockdowns,
        sb.control_time_seconds AS b_control
    FROM fights f
    JOIN fighters fa ON fa.id = f.fighter_a_id
    JOIN fighters fb ON fb.id = f.fighter_b_id
    LEFT JOIN fighter_stats sa ON sa.fight_id = f.id AND sa.fighter_id = f.fighter_a_id
    LEFT JOIN fighter_stats sb ON sb.fight_id = f.id AND sb.fighter_id = f.fighter_b_id
    ORDER BY f.event_date ASC, f.id ASC
    """
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(query)
        return list(cur.fetchall())
    finally:
        cur.close()


def upsert_fighter_fight_metrics(conn, rows: List[Tuple]):
    if not rows:
        return
    cur = conn.cursor()
    try:
        cur.executemany(
            """
            INSERT INTO fighter_fight_metrics (
                fighter_id, fight_id, strikes_per_min, strike_accuracy,
                takedown_defense, takedown_accuracy, win_streak,
                avg_fight_time_seconds, last_5_fights_wins
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                strikes_per_min = VALUES(strikes_per_min),
                strike_accuracy = VALUES(strike_accuracy),
                takedown_defense = VALUES(takedown_defense),
                takedown_accuracy = VALUES(takedown_accuracy),
                win_streak = VALUES(win_streak),
                avg_fight_time_seconds = VALUES(avg_fight_time_seconds),
                last_5_fights_wins = VALUES(last_5_fights_wins)
            """,
            rows,
        )
        conn.commit()
    finally:
        cur.close()


def build_feature_engine() -> pd.DataFrame:
    conn = get_connection()
    try:
        rows = fetch_rows(conn)
        history: Dict[int, List[FightSnapshot]] = defaultdict(list)
        recent_opponents: Dict[int, Deque[int]] = defaultdict(lambda: deque(maxlen=10))
        fight_metric_upserts: List[Tuple] = []
        model_rows: List[Dict] = []

        for item in rows:
            fight_id = int(item["fight_id"])
            event_date = item["event_date"]
            fighter_a = int(item["fighter_a_id"])
            fighter_b = int(item["fighter_b_id"])
            winner_id = int(item["winner_id"]) if item["winner_id"] is not None else None
            method = item.get("method") or ""
            round_num = int(item.get("round_num") or 0)
            duration_seconds = parse_fight_duration(round_num, item.get("time_in_round"))

            pre_a = aggregate_prefight_metrics(history[fighter_a])
            pre_b = aggregate_prefight_metrics(history[fighter_b])

            def opponent_rating(opp_id: int) -> float:
                opp_hist = history.get(opp_id, [])
                if not opp_hist:
                    return 0.5
                opp_wins = sum(h.won for h in opp_hist)
                opp_win_pct = safe_div(opp_wins, len(opp_hist))
                return 0.7 * opp_win_pct + 0.3 * min(len(opp_hist) / 20.0, 1.0)

            sos_a = 0.0
            if recent_opponents[fighter_a]:
                weighted = []
                for idx, opp_id in enumerate(reversed(recent_opponents[fighter_a]), start=1):
                    weight = 1.0 / idx
                    weighted.append((opponent_rating(opp_id), weight))
                sos_a = safe_div(sum(r * w for r, w in weighted), sum(w for _, w in weighted))

            sos_b = 0.0
            if recent_opponents[fighter_b]:
                weighted = []
                for idx, opp_id in enumerate(reversed(recent_opponents[fighter_b]), start=1):
                    weight = 1.0 / idx
                    weighted.append((opponent_rating(opp_id), weight))
                sos_b = safe_div(sum(r * w for r, w in weighted), sum(w for _, w in weighted))

            age_a = age_on_day(item.get("fighter_a_dob"), event_date)
            age_b = age_on_day(item.get("fighter_b_dob"), event_date)

            feature_row = {
                "fight_id": fight_id,
                "event_date": event_date,
                "fighterA_id": fighter_a,
                "fighterB_id": fighter_b,
                "fighterA_won": 1 if winner_id == fighter_a else 0,
                "age_diff": age_a - age_b,
                "age_curve_penalty_diff": age_curve_penalty(age_a) - age_curve_penalty(age_b),
                "prime_score_diff": prime_score(age_a) - prime_score(age_b),
                "power_diff": pre_a["power_score"] - pre_b["power_score"],
                "durability_diff": pre_a["durability_score"] - pre_b["durability_score"],
                "grappling_diff": pre_a["grappling_score"] - pre_b["grappling_score"],
                "cardio_diff": pre_a["cardio_score"] - pre_b["cardio_score"],
                "sos_diff": sos_a - sos_b,
                "weak_jaw_diff": (pre_b["times_koed"] + pre_b["times_knocked_down"]) - (pre_a["times_koed"] + pre_a["times_knocked_down"]),
                "control_avg_diff": pre_a["avg_fight_time_seconds"] - pre_b["avg_fight_time_seconds"],
                "reach_diff": float(item.get("fighter_a_reach") or 0) - float(item.get("fighter_b_reach") or 0),
                "strike_diff": pre_a["strike_accuracy"] - pre_b["strike_accuracy"],
                "takedown_diff": pre_a["takedown_accuracy"] - pre_b["takedown_accuracy"],
                "win_streak_diff": pre_a["win_streak"] - pre_b["win_streak"],
            }
            model_rows.append(feature_row)

            fight_metric_upserts.append(
                (
                    fighter_a,
                    fight_id,
                    pre_a["strikes_per_min"],
                    pre_a["strike_accuracy"],
                    pre_a["takedown_defense"],
                    pre_a["takedown_accuracy"],
                    pre_a["win_streak"],
                    pre_a["avg_fight_time_seconds"],
                    pre_a["last_5_fights_wins"],
                )
            )
            fight_metric_upserts.append(
                (
                    fighter_b,
                    fight_id,
                    pre_b["strikes_per_min"],
                    pre_b["strike_accuracy"],
                    pre_b["takedown_defense"],
                    pre_b["takedown_accuracy"],
                    pre_b["win_streak"],
                    pre_b["avg_fight_time_seconds"],
                    pre_b["last_5_fights_wins"],
                )
            )

            won_a = 1 if winner_id == fighter_a else 0
            won_b = 1 if winner_id == fighter_b else 0

            a_snapshot = FightSnapshot(
                event_date=event_date,
                fighter_id=fighter_a,
                opponent_id=fighter_b,
                won=won_a,
                method=method,
                round_num=round_num,
                duration_seconds=duration_seconds,
                sig_landed=int(item.get("a_sig_landed") or 0),
                sig_attempted=int(item.get("a_sig_attempted") or 0),
                td_landed=int(item.get("a_td_landed") or 0),
                td_attempted=int(item.get("a_td_attempted") or 0),
                knockdowns=int(item.get("a_knockdowns") or 0),
                control_time_seconds=int(item.get("a_control") or 0),
                opp_sig_landed=int(item.get("b_sig_landed") or 0),
                opp_sig_attempted=int(item.get("b_sig_attempted") or 0),
                opp_td_landed=int(item.get("b_td_landed") or 0),
                opp_td_attempted=int(item.get("b_td_attempted") or 0),
                opp_knockdowns=int(item.get("b_knockdowns") or 0),
            )
            b_snapshot = FightSnapshot(
                event_date=event_date,
                fighter_id=fighter_b,
                opponent_id=fighter_a,
                won=won_b,
                method=method,
                round_num=round_num,
                duration_seconds=duration_seconds,
                sig_landed=int(item.get("b_sig_landed") or 0),
                sig_attempted=int(item.get("b_sig_attempted") or 0),
                td_landed=int(item.get("b_td_landed") or 0),
                td_attempted=int(item.get("b_td_attempted") or 0),
                knockdowns=int(item.get("b_knockdowns") or 0),
                control_time_seconds=int(item.get("b_control") or 0),
                opp_sig_landed=int(item.get("a_sig_landed") or 0),
                opp_sig_attempted=int(item.get("a_sig_attempted") or 0),
                opp_td_landed=int(item.get("a_td_landed") or 0),
                opp_td_attempted=int(item.get("a_td_attempted") or 0),
                opp_knockdowns=int(item.get("a_knockdowns") or 0),
            )

            history[fighter_a].append(a_snapshot)
            history[fighter_b].append(b_snapshot)
            recent_opponents[fighter_a].append(fighter_b)
            recent_opponents[fighter_b].append(fighter_a)

        upsert_fighter_fight_metrics(conn, fight_metric_upserts)

        features_df = pd.DataFrame(model_rows)
        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        features_df.to_csv(OUTPUT_PATH, index=False)
        return features_df
    finally:
        conn.close()


def main() -> None:
    features_df = build_feature_engine()
    print(f"Built {len(features_df)} fight-level feature rows -> {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
