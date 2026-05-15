#!/usr/bin/env python3
"""
Quick fight scoring utility — search for fighters and predict the matchup.
"""
import sys
import re
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "api"))
sys.path.insert(0, str(ROOT / "scripts"))

from build_event_predictions import (
    parse_fighter_profile,
    predict_fight,
    build_model_feature_payload,
    compute_logit_components_detailed,
    method_probabilities,
)


def search_fighter_ufc_stats(fighter_name: str) -> str:
    """Search UFC Stats for a fighter and return their profile URL."""
    print(f"  Searching UFC Stats for '{fighter_name}'...")
    search_url = f"https://www.ufcstats.com/search?query={fighter_name.replace(' ', '+')}"
    
    try:
        request = Request(
            search_url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            },
        )
        with urlopen(request, timeout=15) as response:
            html = response.read().decode("utf-8", errors="ignore")
        
        # Look for fighter profile link
        match = re.search(r'href="(/fighter-details/[^"]+)"', html)
        if match:
            fighter_url = f"https://www.ufcstats.com{match.group(1)}"
            print(f"    ✓ Found: {fighter_url}")
            return fighter_url
        else:
            print(f"    ✗ Not found in search results")
            return ""
    except Exception as e:
        print(f"    ✗ Search failed: {e}")
        return ""


def score_fight(fighter_a_name: str, fighter_b_name: str, weight_class: str = "Middleweight"):
    """Score a single matchup between two fighters."""
    print(f"\n{'='*80}")
    print(f"SCORING: {fighter_a_name} vs {fighter_b_name}")
    print(f"Weight Class: {weight_class}")
    print(f"{'='*80}\n")
    
    profile_cache = {}
    
    # Search for both fighters
    print(f"Searching for fighters...")
    fighter_a_url = search_fighter_ufc_stats(fighter_a_name)
    fighter_b_url = search_fighter_ufc_stats(fighter_b_name)
    
    if not fighter_a_url or not fighter_b_url:
        print("\nERROR: Could not find one or both fighters on UFC Stats.")
        return
    
    # Parse profiles
    print(f"\nFetching fighter profiles...")
    profile_a = parse_fighter_profile(fighter_a_url, profile_cache)
    profile_b = parse_fighter_profile(fighter_b_url, profile_cache)
    
    if not profile_a or not profile_b:
        print("ERROR: Could not parse one or both fighter profiles.")
        return
    
    print(f"✓ Profiles loaded, computing prediction...\n")
    
    # Build and predict
    payload = build_model_feature_payload(fighter_a_name, fighter_b_name, profile_a, profile_b)
    winner, probabilities = predict_fight(payload)
    
    prob_a = probabilities.get(fighter_a_name, 0.5)
    prob_b = probabilities.get(fighter_b_name, 0.5)
    
    # Get logit details
    logit_details = compute_logit_components_detailed(profile_a, profile_b, weight_class)
    
    # Get method breakdown
    winner_is_a = winner == fighter_a_name
    winner_profile = profile_a if winner_is_a else profile_b
    loser_profile = profile_b if winner_is_a else profile_a
    winner_confidence = max(prob_a, prob_b)
    
    method_context = {
        "dominant_regime": logit_details.get("regime", "contested"),
        "dominant_path_name": logit_details.get("dominant_path_name", "contested"),
        "weight_class": weight_class,
        "winner_main_logit": logit_details.get("main_logit", 0.0) if winner_is_a else -logit_details.get("main_logit", 0.0),
        "winner_interaction_logit": logit_details.get("interaction_logit", 0.0) if winner_is_a else -logit_details.get("interaction_logit", 0.0),
        "winner_round_win_logit": logit_details.get("round_win_logit", 0.0) if winner_is_a else -logit_details.get("round_win_logit", 0.0),
        "winner_logit_components": (logit_details.get("main_logit", 0.0) + logit_details.get("interaction_logit", 0.0)) if winner_is_a else -(logit_details.get("main_logit", 0.0) + logit_details.get("interaction_logit", 0.0)),
        "entry_prob_winner": logit_details.get("entry_prob_a", 0.55) if winner_is_a else logit_details.get("entry_prob_b", 0.55),
        "entry_prob_loser": logit_details.get("entry_prob_b", 0.55) if winner_is_a else logit_details.get("entry_prob_a", 0.55),
    }
    
    method_probs = method_probabilities(winner_profile or {}, loser_profile or {}, winner_confidence, method_context)
    
    # Display results
    print(f"\n{'PREDICTION RESULT':^80}")
    print(f"{fighter_a_name:35} {prob_a*100:5.1f}%")
    print(f"{fighter_b_name:35} {prob_b*100:5.1f}%")
    print(f"\nPredicted Winner: {winner}")
    print(f"Confidence: {winner_confidence*100:.1f}%")
    
    print(f"\n{'METHOD BREAKDOWN':^80}")
    print(f"KO/TKO:     {method_probs['KO/TKO']*100:5.1f}%")
    print(f"Submission: {method_probs['Submission']*100:5.1f}%")
    print(f"Decision:   {method_probs['Decision']*100:5.1f}%")
    
    print(f"\n{'FIGHT REGIME & LOGITS':^80}")
    print(f"Regime: {logit_details.get('regime', 'N/A')}")
    print(f"Dominant Path: {logit_details.get('dominant_path_name', 'N/A')}")
    print(f"Main Logit: {logit_details.get('main_logit', 0.0):+.4f}")
    print(f"Support Logit: {logit_details.get('support_logit', 0.0):+.4f}")
    print(f"Interaction Logit: {logit_details.get('interaction_logit', 0.0):+.4f}")
    print(f"Regime Strength: {logit_details.get('regime_strength', 1.0):.2f}x")
    
    print(f"\n{'DOMAIN ANALYSIS':^80}")
    print(f"Striking:    {logit_details.get('striking_logit', 0.0):+.4f}")
    print(f"Grappling:   {logit_details.get('grappling_logit', 0.0):+.4f}")
    print(f"Submission:  {logit_details.get('submission_logit', 0.0):+.4f}")
    print(f"Entry Prob A: {logit_details.get('entry_prob_a', 0.0):.2f}")
    print(f"Entry Prob B: {logit_details.get('entry_prob_b', 0.0):.2f}")
    
    print(f"\n{'='*80}\n")


if __name__ == "__main__":
    if len(sys.argv) >= 3:
        fighter_a = sys.argv[1]
        fighter_b = sys.argv[2]
        weight_class = sys.argv[3] if len(sys.argv) > 3 else "Middleweight"
        score_fight(fighter_a, fighter_b, weight_class)
    else:
        print("Usage: python score_test_fight.py 'Fighter A' 'Fighter B' [Weight Class]")
        print("\nExample: python score_test_fight.py 'Ateba Gautier' 'Ozzy Diaz' Middleweight")
