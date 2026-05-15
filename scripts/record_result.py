#!/usr/bin/env python3
"""
CLI tool to record fight results and update prediction tracking.
Usage:
    python scripts/record_result.py "Fighter A" "Fighter B" A|B|draw [--method METHOD] [--round ROUND] [--time TIME]

Example:
    python scripts/record_result.py "Nikolay Veretennikov" "Khaos Williams" A --method SUB --round 2 --time 4:35
    python scripts/record_result.py "Ketlen Vieira" "Jacqueline Cavalcanti" B --method DEC --round 3
"""

import sys
import argparse
from pathlib import Path
from datetime import datetime

# Add parent directory to path for imports
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from track_predictions import record_fight_result, load_predictions_history


def find_matching_prediction(fighter_a, fighter_b):
    """Find if we have a prediction for these fighters."""
    history = load_predictions_history()
    
    for pred in reversed(history["predictions"]):
        if ((pred["fighter_a"].lower() == fighter_a.lower() and 
             pred["fighter_b"].lower() == fighter_b.lower())
            or (pred["fighter_a"].lower() == fighter_b.lower() and 
                pred["fighter_b"].lower() == fighter_a.lower())):
            if pred["result"] is None:
                return pred
    
    return None


def main():
    parser = argparse.ArgumentParser(
        description="Record a fight result for prediction tracking"
    )
    parser.add_argument("fighter_a", help="Fighter A name")
    parser.add_argument("fighter_b", help="Fighter B name")
    parser.add_argument("result", choices=["A", "B", "draw"], help="Winner: A, B, or draw")
    parser.add_argument("--method", choices=["KO/TKO", "SUB", "DEC", "DRAW"], help="Finish method")
    parser.add_argument("--round", type=int, help="Round number")
    parser.add_argument("--time", help="Time in round (e.g., 2:45)")
    
    args = parser.parse_args()
    
    # Check if we have a prediction for these fighters
    prediction = find_matching_prediction(args.fighter_a, args.fighter_b)
    
    if not prediction:
        print(f"\n⚠ No pending prediction found for {args.fighter_a} vs {args.fighter_b}")
        print("  Available predictions:")
        history = load_predictions_history()
        found_any = False
        for pred in reversed(history["predictions"][-10:]):
            if pred["result"] is None:
                print(f"  • {pred['fighter_a']} vs {pred['fighter_b']}")
                found_any = True
        if not found_any:
            print("  (No pending predictions)")
        return 1
    
    # Record the result
    record_fight_result(
        fighter_a=prediction["fighter_a"],
        fighter_b=prediction["fighter_b"],
        result_winner=args.result,
        method=args.method,
        round_num=args.round,
        time_str=args.time
    )
    
    # Display result confirmation
    prob_winner = prediction["predicted_prob_a"] if args.result == "A" else prediction["predicted_prob_b"]
    prob_winner_pct = round(prob_winner * 100, 1)
    
    correct = ""
    if args.result == "A" and prediction["predicted_prob_a"] > 0.5:
        correct = " ✓ CORRECT"
    elif args.result == "B" and prediction["predicted_prob_b"] > 0.5:
        correct = " ✓ CORRECT"
    elif args.result == "draw":
        correct = " ~ DRAW (50/50 credit)"
    else:
        correct = " ✗ INCORRECT"
    
    print(f"\n{'='*80}")
    print(f"RESULT RECORDED{correct}")
    print(f"{'='*80}")
    print(f"Fight:       {prediction['fighter_a']} vs {prediction['fighter_b']}")
    print(f"Prediction:  {prediction['fighter_a']} {prob_winner_pct}% vs {prediction['fighter_b']} {100-prob_winner_pct:.1f}%")
    print(f"Result:      Winner: {args.result}")
    if args.method:
        print(f"             Method: {args.method}")
    if args.round:
        print(f"             Round:  {args.round}", end="")
        if args.time:
            print(f" @ {args.time}")
        else:
            print()
    print(f"Regime:      {prediction['regime']}")
    print(f"Weight:      {prediction['weight_class']}")
    print(f"Logged:      {datetime.fromisoformat(prediction['logged_at']).strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*80}\n")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
