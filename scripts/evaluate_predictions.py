"""
Performance evaluator: calculates win rates and identifies underperforming patterns.
"""

import json
import os
from pathlib import Path
import sys

# Add parent directory to path for imports
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from track_predictions import (
    load_predictions_history, get_predictions_by_regime, get_predictions_by_weight_class
)


def evaluate_predictions(min_confidence=None):
    """
    Evaluate prediction accuracy across all tracked fights.
    
    Args:
        min_confidence: Only evaluate predictions with |prob_a - 0.5| >= min_confidence
                       (helps focus on confident predictions)
    
    Returns:
        dict with overall stats and breakdown by regime/weight_class
    """
    history = load_predictions_history()
    preds = [p for p in history["predictions"] if p["result"] is not None]
    
    if not preds:
        print("No predictions with recorded results yet.")
        return None
    
    # Filter by confidence if specified
    if min_confidence:
        preds = [p for p in preds if abs(p["predicted_prob_a"] - 0.5) >= min_confidence]
    
    # Overall stats
    correct = 0
    for p in preds:
        if p["result"] == "draw":
            correct += 0.5  # Draw: 0.5 credit for both
        elif p["result"] == "A" and p["predicted_prob_a"] > 0.5:
            correct += 1
        elif p["result"] == "B" and p["predicted_prob_a"] < 0.5:
            correct += 1
    
    win_rate = correct / len(preds) if preds else 0
    
    results = {
        "overall": {
            "total_evaluated": len(preds),
            "correct_predictions": correct,
            "win_rate": round(win_rate, 4),
            "win_rate_pct": round(win_rate * 100, 2),
            "target_rate_pct": 65.0,
            "vs_target": round(win_rate * 100 - 65, 2)
        },
        "by_regime": {},
        "by_weight_class": {},
        "by_probability_confidence": {}
    }
    
    # By regime
    by_regime = get_predictions_by_regime()
    for regime, regime_preds in by_regime.items():
        regime_preds_with_results = [p for p in regime_preds if p["result"] is not None]
        if regime_preds_with_results:
            correct_regime = 0
            for p in regime_preds_with_results:
                if p["result"] == "draw":
                    correct_regime += 0.5
                elif p["result"] == "A" and p["predicted_prob_a"] > 0.5:
                    correct_regime += 1
                elif p["result"] == "B" and p["predicted_prob_a"] < 0.5:
                    correct_regime += 1
            
            regime_win_rate = correct_regime / len(regime_preds_with_results)
            results["by_regime"][regime] = {
                "total": len(regime_preds_with_results),
                "correct": correct_regime,
                "win_rate_pct": round(regime_win_rate * 100, 2),
                "vs_target": round(regime_win_rate * 100 - 65, 2)
            }
    
    # By weight class
    by_wc = get_predictions_by_weight_class()
    for wc, wc_preds in by_wc.items():
        wc_preds_with_results = [p for p in wc_preds if p["result"] is not None]
        if wc_preds_with_results:
            correct_wc = 0
            for p in wc_preds_with_results:
                if p["result"] == "draw":
                    correct_wc += 0.5
                elif p["result"] == "A" and p["predicted_prob_a"] > 0.5:
                    correct_wc += 1
                elif p["result"] == "B" and p["predicted_prob_a"] < 0.5:
                    correct_wc += 1
            
            wc_win_rate = correct_wc / len(wc_preds_with_results)
            results["by_weight_class"][wc] = {
                "total": len(wc_preds_with_results),
                "correct": correct_wc,
                "win_rate_pct": round(wc_win_rate * 100, 2),
                "vs_target": round(wc_win_rate * 100 - 65, 2)
            }
    
    # By probability confidence (0.5-0.55, 0.55-0.60, 0.60-0.70, 0.70+)
    confidence_buckets = {
        "marginal (0.50-0.55)": (0.50, 0.55),
        "slight (0.55-0.60)": (0.55, 0.60),
        "moderate (0.60-0.65)": (0.60, 0.65),
        "strong (0.65-0.70)": (0.65, 0.70),
        "very_strong (0.70+)": (0.70, 1.00)
    }
    
    for bucket_name, (min_prob, max_prob) in confidence_buckets.items():
        bucket_preds = [p for p in preds 
                       if min(p["predicted_prob_a"], p["predicted_prob_b"]) >= min_prob
                       and min(p["predicted_prob_a"], p["predicted_prob_b"]) < max_prob]
        
        if bucket_preds:
            correct_bucket = 0
            for p in bucket_preds:
                if p["result"] == "draw":
                    correct_bucket += 0.5
                elif p["result"] == "A" and p["predicted_prob_a"] > 0.5:
                    correct_bucket += 1
                elif p["result"] == "B" and p["predicted_prob_a"] < 0.5:
                    correct_bucket += 1
            
            bucket_win_rate = correct_bucket / len(bucket_preds)
            results["by_probability_confidence"][bucket_name] = {
                "total": len(bucket_preds),
                "correct": correct_bucket,
                "win_rate_pct": round(bucket_win_rate * 100, 2),
                "vs_target": round(bucket_win_rate * 100 - 65, 2)
            }
    
    return results


def print_evaluation_report():
    """Print detailed performance evaluation report."""
    results = evaluate_predictions()
    
    if not results:
        return
    
    print(f"\n{'='*80}")
    print(f"PREDICTION PERFORMANCE EVALUATION")
    print(f"{'='*80}\n")
    
    overall = results["overall"]
    print(f"OVERALL PERFORMANCE")
    print(f"{'─'*80}")
    print(f"Total predictions evaluated: {overall['total_evaluated']}")
    print(f"Correct predictions:         {overall['correct_predictions']}")
    print(f"Win rate:                    {overall['win_rate_pct']}% (target: {overall['target_rate_pct']}%)")
    print(f"Vs target:                   {overall['vs_target']:+.2f}%")
    
    if results["by_regime"]:
        print(f"\n{'─'*80}")
        print(f"PERFORMANCE BY REGIME")
        print(f"{'─'*80}")
        for regime, stats in sorted(results["by_regime"].items()):
            status = "⚠ UNDERPERFORMING" if stats["vs_target"] < -5 else "✓" if stats["vs_target"] > 0 else "⚠ SLIGHTLY LOW"
            print(f"{regime:25s}: {stats['win_rate_pct']:6.2f}% ({stats['total']:3d} preds) {stats['vs_target']:+.2f}% vs target {status}")
    
    if results["by_weight_class"]:
        print(f"\n{'─'*80}")
        print(f"PERFORMANCE BY WEIGHT CLASS")
        print(f"{'─'*80}")
        for wc, stats in sorted(results["by_weight_class"].items()):
            status = "⚠ UNDERPERFORMING" if stats["vs_target"] < -5 else "✓" if stats["vs_target"] > 0 else "⚠ SLIGHTLY LOW"
            print(f"{wc:25s}: {stats['win_rate_pct']:6.2f}% ({stats['total']:3d} preds) {stats['vs_target']:+.2f}% vs target {status}")
    
    if results["by_probability_confidence"]:
        print(f"\n{'─'*80}")
        print(f"PERFORMANCE BY PROBABILITY CONFIDENCE")
        print(f"{'─'*80}")
        for confidence, stats in sorted(results["by_probability_confidence"].items()):
            print(f"{confidence:35s}: {stats['win_rate_pct']:6.2f}% ({stats['total']:3d} preds) {stats['vs_target']:+.2f}% vs target")
    
    print(f"\n{'='*80}\n")
    
    # Calibration recommendations
    print(f"CALIBRATION RECOMMENDATIONS")
    print(f"{'─'*80}")
    
    underperforming = []
    for regime, stats in results["by_regime"].items():
        if stats["vs_target"] < -5:
            underperforming.append((regime, "regime", stats["vs_target"], stats["total"]))
    
    for wc, stats in results["by_weight_class"].items():
        if stats["vs_target"] < -5:
            underperforming.append((wc, "weight_class", stats["vs_target"], stats["total"]))
    
    if not underperforming:
        print("✓ No significant underperformance detected!")
        print(f"  Overall win rate: {overall['win_rate_pct']}% (target: {overall['target_rate_pct']}%)")
    else:
        print(f"⚠ Found {len(underperforming)} underperforming patterns:\n")
        for pattern_name, pattern_type, vs_target, count in sorted(underperforming, key=lambda x: x[2]):
            if pattern_type == "regime":
                print(f"  • Regime '{pattern_name}' is {abs(vs_target):.2f}% below target ({count} predictions)")
                print(f"    → Consider reducing {pattern_name} regime strength multiplier")
            else:
                print(f"  • Weight class '{pattern_name}' is {abs(vs_target):.2f}% below target ({count} predictions)")
                print(f"    → Consider specific adjustments for {pattern_name}")
    
    print()


if __name__ == "__main__":
    print_evaluation_report()
