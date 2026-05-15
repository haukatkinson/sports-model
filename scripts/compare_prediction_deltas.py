"""
Compare latest vs previous logged predictions per fight and flag large moves.

Usage:
    python scripts/compare_prediction_deltas.py
    python scripts/compare_prediction_deltas.py --threshold 0.05
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from track_predictions import load_predictions_history  # noqa: E402


def parse_dt(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return datetime.min


def pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def compare_prediction_deltas(threshold: float = 0.05) -> int:
    history = load_predictions_history()
    predictions = history.get("predictions", [])

    if not predictions:
        print("No prediction history found.")
        return 1

    by_fight: dict[str, list[dict]] = defaultdict(list)
    for row in predictions:
        fighter_a = str(row.get("fighter_a", "")).strip()
        fighter_b = str(row.get("fighter_b", "")).strip()
        if not fighter_a or not fighter_b:
            continue
        key = f"{fighter_a}|||{fighter_b}"
        by_fight[key].append(row)

    rows: list[dict] = []
    for key, entries in by_fight.items():
        if len(entries) < 2:
            continue

        ordered = sorted(entries, key=lambda x: parse_dt(str(x.get("logged_at", ""))))
        prev = ordered[-2]
        curr = ordered[-1]

        prev_prob_a = float(prev.get("predicted_prob_a", 0.5) or 0.5)
        curr_prob_a = float(curr.get("predicted_prob_a", 0.5) or 0.5)

        delta = curr_prob_a - prev_prob_a
        abs_delta = abs(delta)

        prev_regime = str(prev.get("regime", ""))
        curr_regime = str(curr.get("regime", ""))

        prev_int = float(prev.get("interaction_logit", 0.0) or 0.0)
        curr_int = float(curr.get("interaction_logit", 0.0) or 0.0)

        prev_round = float(prev.get("round_win_logit", 0.0) or 0.0)
        curr_round = float(curr.get("round_win_logit", 0.0) or 0.0)

        fighter_a, fighter_b = key.split("|||", 1)
        rows.append(
            {
                "fighter_a": fighter_a,
                "fighter_b": fighter_b,
                "prev_prob_a": prev_prob_a,
                "curr_prob_a": curr_prob_a,
                "delta": delta,
                "abs_delta": abs_delta,
                "prev_regime": prev_regime,
                "curr_regime": curr_regime,
                "regime_changed": prev_regime != curr_regime,
                "prev_interaction": prev_int,
                "curr_interaction": curr_int,
                "prev_round_win": prev_round,
                "curr_round_win": curr_round,
                "flagged": abs_delta >= threshold,
                "prev_logged_at": str(prev.get("logged_at", "")),
                "curr_logged_at": str(curr.get("logged_at", "")),
            }
        )

    if not rows:
        print("No fights with at least two logged predictions yet.")
        return 1

    rows.sort(key=lambda x: x["abs_delta"], reverse=True)

    print("\n" + "=" * 110)
    print("PREDICTION DELTA REPORT (LATEST vs PREVIOUS)")
    print("=" * 110)
    print(f"Threshold flag: {threshold * 100:.1f}%")
    print()

    header = (
        f"{'FIGHT':44s} "
        f"{'PREV':>7s} {'CURR':>7s} {'DELTA':>8s} "
        f"{'REGIME':22s} {'INTΔ':>8s} {'RWΔ':>8s} {'FLAG':>6s}"
    )
    print(header)
    print("-" * len(header))

    flagged_count = 0
    for row in rows:
        fight = f"{row['fighter_a']} vs {row['fighter_b']}"
        prev_prob = pct(row["prev_prob_a"])
        curr_prob = pct(row["curr_prob_a"])
        delta_pct = row["delta"] * 100.0
        delta_str = f"{delta_pct:+.1f}%"

        regime_text = row["curr_regime"]
        if row["regime_changed"]:
            regime_text = f"{row['prev_regime']}→{row['curr_regime']}"

        int_delta = row["curr_interaction"] - row["prev_interaction"]
        rw_delta = row["curr_round_win"] - row["prev_round_win"]

        flag = "YES" if row["flagged"] else ""
        if row["flagged"]:
            flagged_count += 1

        print(
            f"{fight[:44]:44s} "
            f"{prev_prob:>7s} {curr_prob:>7s} {delta_str:>8s} "
            f"{regime_text[:22]:22s} {int_delta:+8.4f} {rw_delta:+8.4f} {flag:>6s}"
        )

    print("\n" + "-" * 110)
    print(f"Total comparable fights: {len(rows)}")
    print(f"Flagged (|delta| >= {threshold * 100:.1f}%): {flagged_count}")
    print("=" * 110 + "\n")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare latest vs previous prediction deltas")
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.05,
        help="Absolute delta threshold for flagging (default: 0.05 = 5%%)",
    )
    args = parser.parse_args()
    return compare_prediction_deltas(threshold=args.threshold)


if __name__ == "__main__":
    raise SystemExit(main())
