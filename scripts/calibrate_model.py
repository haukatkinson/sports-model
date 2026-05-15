"""
Model calibration system: identifies underperforming patterns and suggests adjustments.
"""

import json
from pathlib import Path
import sys

# Add parent directory to path for imports
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from evaluate_predictions import evaluate_predictions


def generate_calibration_report():
    """
    Generate calibration recommendations based on performance evaluation.
    """
    results = evaluate_predictions()
    
    if not results:
        return None
    
    calibration = {
        "overall_win_rate": results["overall"]["win_rate_pct"],
        "target_win_rate": 65.0,
        "adjustments_needed": [],
        "regime_adjustments": {},
        "weight_class_adjustments": {},
        "general_notes": []
    }
    
    overall_vs_target = results["overall"]["vs_target"]
    
    # If overall is significantly below target, suggest global dampening
    if overall_vs_target < -5:
        dampening_factor = 1.0 + (abs(overall_vs_target) / 100 * 0.15)  # Suggest 15% adjustment per 100% miss
        calibration["adjustments_needed"].append("GLOBAL_DAMPENING")
        calibration["general_notes"].append(
            f"Overall win rate is {abs(overall_vs_target):.2f}% below target. "
            f"Consider reducing confidence across all predictions by ~{(1-1/dampening_factor)*100:.1f}%"
        )
    
    # If overall is significantly above target, suggest global amplification
    if overall_vs_target > 5:
        amplification_factor = 1.0 / (1.0 + (overall_vs_target / 100 * 0.10))
        calibration["adjustments_needed"].append("GLOBAL_AMPLIFICATION")
        calibration["general_notes"].append(
            f"Overall win rate is {overall_vs_target:.2f}% above target. "
            f"Consider increasing confidence across predictions by ~{(1 - amplification_factor)*100:.1f}%"
        )
    
    # Regime-specific adjustments
    for regime, stats in results["by_regime"].items():
        if stats["total"] >= 5:  # Only if we have enough data points
            regime_vs_target = stats["vs_target"]
            
            if regime_vs_target < -10:
                # Significantly underperforming: reduce regime strength
                reduction = abs(regime_vs_target) / 100 * 0.20  # 20% adjustment per 100% miss
                calibration["regime_adjustments"][regime] = {
                    "action": "REDUCE_STRENGTH",
                    "current_performance": stats["win_rate_pct"],
                    "vs_target": regime_vs_target,
                    "suggested_reduction": f"{reduction*100:.1f}%",
                    "reason": f"Underperforming by {abs(regime_vs_target):.2f}% with {stats['total']} predictions"
                }
                calibration["adjustments_needed"].append(f"REGIME_ADJUST_{regime}")
            
            elif regime_vs_target < -5:
                calibration["regime_adjustments"][regime] = {
                    "action": "MONITOR",
                    "current_performance": stats["win_rate_pct"],
                    "vs_target": regime_vs_target,
                    "reason": f"Slightly underperforming, monitor for consistency"
                }
            
            elif regime_vs_target > 5:
                calibration["regime_adjustments"][regime] = {
                    "action": "POTENTIALLY_REDUCE",
                    "current_performance": stats["win_rate_pct"],
                    "vs_target": regime_vs_target,
                    "reason": f"Overperforming, may indicate overconfidence"
                }
    
    # Weight class adjustments
    for wc, stats in results["by_weight_class"].items():
        if stats["total"] >= 5:
            wc_vs_target = stats["vs_target"]
            
            if wc_vs_target < -10:
                reduction = abs(wc_vs_target) / 100 * 0.20
                calibration["weight_class_adjustments"][wc] = {
                    "action": "REDUCE_CONFIDENCE",
                    "current_performance": stats["win_rate_pct"],
                    "vs_target": wc_vs_target,
                    "suggested_reduction": f"{reduction*100:.1f}%",
                    "reason": f"Underperforming by {abs(wc_vs_target):.2f}% with {stats['total']} predictions"
                }
                calibration["adjustments_needed"].append(f"WC_ADJUST_{wc}")
    
    return calibration


def print_calibration_report():
    """Print calibration recommendations."""
    calibration = generate_calibration_report()
    
    if not calibration:
        print("Cannot generate calibration report yet.")
        return
    
    print(f"\n{'='*80}")
    print(f"MODEL CALIBRATION REPORT")
    print(f"{'='*80}\n")
    
    print(f"Current win rate:  {calibration['overall_win_rate']:.2f}%")
    print(f"Target win rate:   {calibration['target_win_rate']:.2f}%")
    print(f"Difference:        {calibration['overall_win_rate'] - calibration['target_win_rate']:+.2f}%\n")
    
    if not calibration["adjustments_needed"]:
        print("✓ Model is well-calibrated. No major adjustments needed.")
    else:
        print(f"Adjustments suggested: {len(set(calibration['adjustments_needed']))} patterns\n")
        
        if calibration["regime_adjustments"]:
            print(f"{'─'*80}")
            print(f"REGIME-SPECIFIC ADJUSTMENTS")
            print(f"{'─'*80}")
            for regime, adj in sorted(calibration["regime_adjustments"].items()):
                print(f"\n{regime}:")
                print(f"  Action:        {adj['action']}")
                print(f"  Current perf:  {adj['current_performance']:.2f}%")
                print(f"  vs target:     {adj['vs_target']:+.2f}%")
                if 'suggested_reduction' in adj:
                    print(f"  Suggested adj: {adj['suggested_reduction']}")
                print(f"  Reason:        {adj['reason']}")
        
        if calibration["weight_class_adjustments"]:
            print(f"\n{'─'*80}")
            print(f"WEIGHT CLASS ADJUSTMENTS")
            print(f"{'─'*80}")
            for wc, adj in sorted(calibration["weight_class_adjustments"].items()):
                print(f"\n{wc}:")
                print(f"  Action:        {adj['action']}")
                print(f"  Current perf:  {adj['current_performance']:.2f}%")
                print(f"  vs target:     {adj['vs_target']:+.2f}%")
                if 'suggested_reduction' in adj:
                    print(f"  Suggested adj: {adj['suggested_reduction']}")
                print(f"  Reason:        {adj['reason']}")
    
    if calibration["general_notes"]:
        print(f"\n{'─'*80}")
        print(f"NOTES")
        print(f"{'─'*80}")
        for note in calibration["general_notes"]:
            print(f"• {note}")
    
    print(f"\n{'='*80}\n")


if __name__ == "__main__":
    print_calibration_report()
