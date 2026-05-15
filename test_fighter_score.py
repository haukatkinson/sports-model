#!/usr/bin/env python3
import sys
import re
from pathlib import Path
from urllib.request import Request, urlopen

# Prevent build_event_predictions main() from running
sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "api"))

# Prevent main() execution
import build_event_predictions as bep
original_main = bep.main
bep.main = lambda: None

from build_event_predictions import (
    parse_fighter_profile, predict_fight, build_model_feature_payload,
    compute_logit_components_detailed, method_probabilities
)

def search_fighter(name):
    print(f"  Searching '{name}'...")
    try:
        search_url = f"https://www.ufcstats.com/search?query={name.replace(' ', '+')}"
        request = Request(search_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urlopen(request, timeout=10) as response:
            html = response.read().decode('utf-8', errors='ignore')
        match = re.search(r'href="(/fighter-details/[^"]+)"', html)
        if match:
            url = f"https://www.ufcstats.com{match.group(1)}"
            print(f"    ✓ {url}")
            return url
        print("    ✗ Not found")
    except Exception as e:
        print(f"    ✗ {e}")
    return None

print("\n" + "="*80)
print("SCORING: Ateba Gautier vs Ozzy Diaz")
print("="*80 + "\n")

print("Searching fighters...")
url_a = search_fighter("Ateba Gautier")
url_b = search_fighter("Ozzy Diaz")

if not (url_a and url_b):
    print("ERROR: Could not find fighters")
    sys.exit(1)

print("\nFetching profiles...")
cache = {}
profile_a = parse_fighter_profile(url_a, cache)
profile_b = parse_fighter_profile(url_b, cache)

if not (profile_a and profile_b):
    print("ERROR: Could not parse profiles")
    sys.exit(1)

print("Computing prediction...\n")

payload = build_model_feature_payload("Ateba Gautier", "Ozzy Diaz", profile_a, profile_b)
winner, probs = predict_fight(payload)

prob_a = probs.get("Ateba Gautier", 0.5)
prob_b = probs.get("Ozzy Diaz", 0.5)

details = compute_logit_components_detailed(profile_a, profile_b, "Welterweight")

# Method breakdown
winner_is_a = (winner == "Ateba Gautier")
winner_profile = profile_a if winner_is_a else profile_b
loser_profile = profile_b if winner_is_a else profile_a
winner_conf = max(prob_a, prob_b)

method_context = {
    "dominant_regime": details.get("regime", "contested"),
    "dominant_path_name": details.get("dominant_path_name", "contested"),
    "weight_class": "Welterweight",
    "winner_main_logit": details.get("main_logit", 0.0) if winner_is_a else -details.get("main_logit", 0.0),
    "winner_interaction_logit": details.get("interaction_logit", 0.0) if winner_is_a else -details.get("interaction_logit", 0.0),
    "winner_round_win_logit": details.get("round_win_logit", 0.0) if winner_is_a else -details.get("round_win_logit", 0.0),
    "winner_logit_components": 0.0,
    "entry_prob_winner": details.get("entry_prob_a", 0.55) if winner_is_a else details.get("entry_prob_b", 0.55),
    "entry_prob_loser": details.get("entry_prob_b", 0.55) if winner_is_a else details.get("entry_prob_a", 0.55),
}

method_probs = method_probabilities(winner_profile or {}, loser_profile or {}, winner_conf, method_context)

# Display
print("PREDICTION RESULT")
print(f"{'Ateba Gautier':<35} {prob_a*100:>5.1f}%")
print(f"{'Ozzy Diaz':<35} {prob_b*100:>5.1f}%")
print(f"\nPredicted Winner: {winner}")
print(f"Confidence: {winner_conf*100:.1f}%")

print("\nMETHOD BREAKDOWN")
print(f"  KO/TKO:     {method_probs['KO/TKO']*100:>5.1f}%")
print(f"  Submission: {method_probs['Submission']*100:>5.1f}%")
print(f"  Decision:   {method_probs['Decision']*100:>5.1f}%")

print("\nFIGHT REGIME & ANALYSIS")
print(f"  Regime: {details.get('regime', 'N/A')}")
print(f"  Dominant Path: {details.get('dominant_path_name', 'N/A')}")
print(f"  Main Logit: {details.get('main_logit', 0.0):+.4f}")
print(f"  Support Logit: {details.get('support_logit', 0.0):+.4f}")
print(f"  Interaction Logit: {details.get('interaction_logit', 0.0):+.4f}")
print(f"  Regime Strength: {details.get('regime_strength', 1.0):.2f}x")

print("\nDOMAIN LOGITS")
print(f"  Striking:    {details.get('striking_logit', 0.0):+.4f}")
print(f"  Grappling:   {details.get('grappling_logit', 0.0):+.4f}")
print(f"  Submission:  {details.get('submission_logit', 0.0):+.4f}")

print("\nENTRY PROBABILITIES")
print(f"  Ateba Entry Prob: {details.get('entry_prob_a', 0.0):.3f}")
print(f"  Ozzy Entry Prob:  {details.get('entry_prob_b', 0.0):.3f}")

print("\n" + "="*80 + "\n")
