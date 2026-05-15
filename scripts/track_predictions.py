"""
Prediction tracking system: logs all predictions with metadata for later evaluation.
"""

import json
import os
from datetime import datetime

PREDICTIONS_FILE = "data/predictions_history.json"


def load_predictions_history():
    """Load existing predictions history."""
    if os.path.exists(PREDICTIONS_FILE):
        with open(PREDICTIONS_FILE, 'r') as f:
            return json.load(f)
    return {"predictions": []}


def save_predictions_history(history):
    """Save predictions history."""
    os.makedirs(os.path.dirname(PREDICTIONS_FILE), exist_ok=True)
    with open(PREDICTIONS_FILE, 'w') as f:
        json.dump(history, f, indent=2)


def log_prediction(fighter_a, fighter_b, prob_a, prob_b, regime, weight_class, 
                   dom_logit, interaction_logit, round_win_logit, event_date=None):
    """
    Log a fight prediction with full metadata.
    
    Args:
        fighter_a, fighter_b: Fighter names
        prob_a, prob_b: Predicted probabilities
        regime: Detected regime (clean_dominance, contested, etc.)
        weight_class: Weight class name
        dom_logit, interaction_logit, round_win_logit: Component logits
        event_date: Event date (defaults to today)
    """
    history = load_predictions_history()
    
    if event_date is None:
        event_date = datetime.now().strftime("%Y-%m-%d")
    
    prediction = {
        "logged_at": datetime.now().isoformat(),
        "event_date": event_date,
        "fighter_a": fighter_a,
        "fighter_b": fighter_b,
        "predicted_prob_a": round(prob_a, 4),
        "predicted_prob_b": round(prob_b, 4),
        "regime": regime,
        "weight_class": weight_class,
        "dom_logit": round(dom_logit, 4),
        "interaction_logit": round(interaction_logit, 4),
        "round_win_logit": round(round_win_logit, 4),
        "result": None,  # To be filled in later
        "result_method": None,
        "result_round": None,
        "result_time": None
    }
    
    history["predictions"].append(prediction)
    save_predictions_history(history)
    
    return prediction


def record_fight_result(fighter_a, fighter_b, result_winner, method=None, round_num=None, 
                        time_str=None):
    """
    Record the actual result of a fight. Matches against logged prediction by fighters.
    
    Args:
        fighter_a, fighter_b: Fighter names (used to find matching prediction)
        result_winner: "A", "B", or "draw"
        method: "KO/TKO", "SUB", "DEC", "DRAW", etc.
        round_num: Round number
        time_str: Time in round (e.g., "2:45")
    """
    history = load_predictions_history()
    
    # Find matching prediction (most recent one for these fighters)
    for pred in reversed(history["predictions"]):
        if ((pred["fighter_a"].lower() == fighter_a.lower() and 
             pred["fighter_b"].lower() == fighter_b.lower())
            or (pred["fighter_a"].lower() == fighter_b.lower() and 
                pred["fighter_b"].lower() == fighter_a.lower())):
            if pred["result"] is None:  # Only update if not already recorded
                pred["result"] = result_winner
                pred["result_method"] = method
                pred["result_round"] = round_num
                pred["result_time"] = time_str
                pred["result_recorded_at"] = datetime.now().isoformat()
                break
    
    save_predictions_history(history)


def get_predictions_by_regime():
    """Get all predictions grouped by regime."""
    history = load_predictions_history()
    by_regime = {}
    
    for pred in history["predictions"]:
        regime = pred["regime"]
        if regime not in by_regime:
            by_regime[regime] = []
        by_regime[regime].append(pred)
    
    return by_regime


def get_predictions_by_weight_class():
    """Get all predictions grouped by weight class."""
    history = load_predictions_history()
    by_wc = {}
    
    for pred in history["predictions"]:
        wc = pred["weight_class"]
        if wc not in by_wc:
            by_wc[wc] = []
        by_wc[wc].append(pred)
    
    return by_wc


def print_tracking_status():
    """Print summary of tracked predictions."""
    history = load_predictions_history()
    preds = history["predictions"]
    
    total = len(preds)
    with_results = len([p for p in preds if p["result"] is not None])
    pending = total - with_results
    
    print(f"\n{'='*70}")
    print(f"PREDICTION TRACKING STATUS")
    print(f"{'='*70}")
    print(f"Total predictions tracked: {total}")
    print(f"With recorded results:     {with_results}")
    print(f"Pending results:           {pending}")
    print(f"\nGrouped by regime:")
    
    by_regime = get_predictions_by_regime()
    for regime, preds in sorted(by_regime.items()):
        regime_with_results = len([p for p in preds if p["result"] is not None])
        print(f"  {regime:20s}: {len(preds):3d} predictions ({regime_with_results:3d} with results)")
    
    print(f"\nGrouped by weight class:")
    by_wc = get_predictions_by_weight_class()
    for wc, preds in sorted(by_wc.items()):
        wc_with_results = len([p for p in preds if p["result"] is not None])
        print(f"  {wc:20s}: {len(preds):3d} predictions ({wc_with_results:3d} with results)")


if __name__ == "__main__":
    print_tracking_status()
