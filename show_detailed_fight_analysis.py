#!/usr/bin/env python3
"""Display detailed analysis for a specific fight from predictions."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
pred_file = ROOT / "data" / "predictions_history.json"

if not pred_file.exists():
    print("ERROR: No predictions history found")
    exit(1)

with open(pred_file) as f:
    data = json.load(f)

predictions = data.get('predictions', [])
if not predictions:
    print("ERROR: Predictions history is empty")
    exit(1)

# Get the most recent prediction with a result recorded
entry = None
for pred in predictions:
    if pred.get('result'):
        entry = pred
        break

if not entry:
    entry = predictions[0]

print("\n" + "="*90)
print("DETAILED FIGHT ANALYSIS")
print("="*90)

fighter_a = entry['fighter_a']
fighter_b = entry['fighter_b']
prob_a = entry['predicted_prob_a']
prob_b = entry['predicted_prob_b']

print(f"\nFight: {fighter_a} vs {fighter_b}")
print(f"Timestamp: {entry['logged_at']}")
print(f"Weight Class: {entry.get('weight_class', 'N/A')}")

print("\n" + "-"*90)
print("PREDICTION PROBABILITIES")
print("-"*90)

print(f"\n{fighter_a:<40} {prob_a*100:>5.1f}%")
print(f"{fighter_b:<40} {prob_b*100:>5.1f}%")

winner = fighter_a if prob_a > prob_b else fighter_b
print(f"\nPredicted Winner: {winner} ({max(prob_a, prob_b)*100:.1f}% confidence)")

print("\n" + "-"*90)
print("METHOD BREAKDOWN")
print("-"*90)
print("\n  (Method data available in extended prediction logs)")

print("\n" + "-"*90)
print("FIGHT REGIME & LOGIT ANALYSIS")
print("-"*90)
regime = entry.get('regime', 'N/A')
logit_details = entry

print(f"\nRegime: {regime}")

print(f"\nLogit Components:")
print(f"  Dom Logit:         {logit_details.get('dom_logit', 0.0):+.4f}")
print(f"  Interaction Logit: {logit_details.get('interaction_logit', 0.0):+.4f}")
print(f"  Round Win Logit:   {logit_details.get('round_win_logit', 0.0):+.4f}")

if 'result' in entry and entry['result']:
    print(f"\n" + "-"*90)
    print("ACTUAL RESULT")
    print("-"*90)
    result_map = {"A": fighter_a, "B": fighter_b}
    actual_winner = result_map.get(entry['result'], entry['result'])
    print(f"Actual Winner: {actual_winner}")
    print(f"Actual Method: {entry.get('result_method', 'N/A')}")
    print(f"Actual Round: {entry.get('result_round', 'N/A')}")
    print(f"Result Recorded: {entry.get('result_recorded_at', 'N/A')}")
    
    # Prediction accuracy
    prediction_correct = (winner == actual_winner)
    accuracy_str = "✓ CORRECT" if prediction_correct else "✗ INCORRECT"
    print(f"\nPrediction Accuracy: {accuracy_str}")

print("\n" + "="*90 + "\n")
