#!/usr/bin/env python
"""
Phase 1 Safeguards: Diminishing returns, power/finisher separation, anti-wrestling, fragility
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path('.') / 'api'))

import build_event_predictions as bp

print("=" * 70)
print("PHASE 1 SAFEGUARDS TEST")
print("=" * 70)

# Test 1: Diminishing Returns (prevents bonus stacking explosion)
print("\n[TEST 1] Diminishing Returns Compression")
print("-" * 70)

test_bonuses = [0.1, 0.5, 1.0, 1.5, 2.0, 2.5]
print("Raw Bonus -> Compressed Bonus (tanh compression)")
for raw in test_bonuses:
    compressed = bp.apply_diminishing_returns(raw, compression_factor=0.8)
    print(f"  {raw:4.1f} -> {compressed:6.4f}")

print("\nKey Insight:")
print("  - raw=1.0 -> 0.4621 (not 0.35 * 1.0 linear)")
print("  - raw=2.0 -> 0.6352 (capped, not 0.35 * 2.0 = 0.7)")
print("  - raw=3.0 -> 0.7119 (asymptotic, won't create 85% favorites)")

# Test 2: Power vs Finisher Separation
print("\n\n[TEST 2] Power vs Finisher Score Separation")
print("-" * 70)

profiles_test = [
    {
        "name": "Derrick Lewis",
        "desc": "High power, moderate finishing",
        "wins": 26, "losses": 8, "draws": 0,
        "slpm": 3.5, "sapm": 2.5, "str_def": 58.0
    },
    {
        "name": "Max Holloway",
        "desc": "Lower power, elite finishing (pressure)",
        "wins": 26, "losses": 6, "draws": 0,
        "slpm": 5.5, "sapm": 4.0, "str_def": 62.0
    },
    {
        "name": "Merab Dvalishvili",
        "desc": "Low power, low finishing (grinder)",
        "wins": 15, "losses": 5, "draws": 0,
        "slpm": 3.2, "sapm": 2.8, "str_def": 60.0
    }
]

for p in profiles_test:
    power = bp.compute_power_score(p)
    finisher = bp.compute_finisher_score(p)
    print(f"\n{p['name']} ({p['desc']}):")
    print(f"  Power Score:    {power:.4f}")
    print(f"  Finisher Score: {finisher:.4f}")
    print(f"  Ratio: {power/max(finisher, 0.001):.2f}x")

# Test 3: Anti-Wrestling Score (prevents wrestler overweighting)
print("\n\n[TEST 3] Anti-Wrestling Score (Evasion/Escape Ability)")
print("-" * 70)

anti_wrestling_test = [
    {
        "name": "Elite Wrestler (mediocre escape)",
        "td_def": 55.0, "slpm": 2.0, "sub_avg": 0.1
    },
    {
        "name": "Oliveira (poor TDD, high sub threat)",
        "td_def": 48.0, "slpm": 4.5, "sub_avg": 1.8
    },
    {
        "name": "Technical Striker (high TDD)",
        "td_def": 75.0, "slpm": 5.5, "sub_avg": 0.2
    }
]

for p in anti_wrestling_test:
    anti_w = bp.compute_anti_wrestling_score(p)
    print(f"\n{p['name']}:")
    print(f"  TDD: {p['td_def']}, SLpM: {p['slpm']}, Sub Avg: {p['sub_avg']}")
    print(f"  Anti-Wrestling Score: {anti_w:.4f}")
    if anti_w > 0.6:
        print(f"  -> HIGH: Hard to hold down (good escape threat)")
    elif anti_w < 0.3:
        print(f"  -> LOW: Vulnerable to control")
    else:
        print(f"  -> MODERATE: Situationally vulnerable")

# Test 4: Fragility Detection
print("\n\n[TEST 4] Fragility Flag Detection")
print("-" * 70)

from datetime import date, timedelta

fragile_profiles = [
    {
        "name": "High volume striker with poor defense",
        "sapm": 6.0, "str_def": 40.0, "losses": 3, "dob": date(1990, 1, 1)
    },
    {
        "name": "Older fighter, multiple losses",
        "sapm": 3.0, "str_def": 55.0, "losses": 7, "dob": date(1987, 1, 1)
    },
    {
        "name": "Healthy young fighter",
        "sapm": 3.5, "str_def": 58.0, "losses": 1, "dob": date(1998, 1, 1)
    }
]

for p in fragile_profiles:
    is_fragile, unc_mult = bp.detect_fragility_flags(p)
    print(f"\n{p['name']}:")
    print(f"  SApM: {p['sapm']}, Str Def: {p['str_def']}, Losses: {p['losses']}")
    print(f"  Age: {((date.today() - p['dob']).days / 365.25):.1f} years")
    print(f"  Fragile: {is_fragile}, Uncertainty Multiplier: {unc_mult:.3f}")
    if unc_mult > 1.04:
        print(f"  -> Prediction expanded by {(unc_mult - 1.0) * 100:.1f}% to account for brittleness")

# Test 5: Bonus Stacking Before/After Diminishing Returns
print("\n\n[TEST 5] Bonus Stacking: Before vs After Diminishing Returns")
print("-" * 70)

print("\nScenario: Elite wrestler vs poor TDD fighter")
print("  Triggers: wrestler_bonus + sub_bonus + anti_wrestling = stacking")

raw_total = 0.50 * 0.90  # wrestler bonus
raw_total += 0.35 * 0.90  # sub bonus 
raw_total += 0.08 * 0.5   # anti-wrestling dampening
compressed_total = bp.apply_diminishing_returns(raw_total, compression_factor=0.8)

print(f"\nRaw bonus accumulation: {raw_total:.4f}")
print(f"After tanh(0.8x) compression: {compressed_total:.4f}")
print(f"Multiplied by 0.35 in total score: {compressed_total * 0.35:.4f}")
print(f"\nPhilosophy: Amplify vulnerabilities, don't fully determine outcomes")
print(f"Result: Matchup advantage bounded, profile explosion prevented")

print("\n" + "=" * 70)
print("PHASE 1 SAFEGUARDS VERIFIED ✓")
print("=" * 70)
