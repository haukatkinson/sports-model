import sys, pathlib, json
sys.path.insert(0, 'api')
import build_event_predictions as bp

cache = {}

url_a = 'http://ufcstats.com/fighter-details/3e8118c1ab52f211'
url_b = 'http://ufcstats.com/fighter-details/64ad3e3b0efa30bb'

print("Fetching Tuco Tokkos...")
pa = bp.parse_fighter_profile(url_a, cache, include_sos=False)

print("Fetching Ivan Erslan...")
pb = bp.parse_fighter_profile(url_b, cache, include_sos=False)

from datetime import date

def display_profile(name, p):
    wins   = int(p.get('wins', 0) or 0)
    losses = int(p.get('losses', 0) or 0)
    draws  = int(p.get('draws', 0) or 0)
    total  = wins + losses + draws
    dob    = p.get('dob')
    age    = round((date.today() - dob).days / 365.25, 1) if dob else 'N/A'

    power    = bp.compute_power_score(p)
    finisher = bp.compute_finisher_score(p)
    anti_w   = bp.compute_anti_wrestling_score(p)
    archtype = bp.classify_archetype(p)
    control  = bp.compute_control_proxy(p)
    fragile, unc_mult = bp.detect_fragility_flags(p)

    print(f"\n{'='*55}")
    print(f"  {name}")
    print(f"{'='*55}")
    print(f"\n-- RECORD & DEMOGRAPHICS --")
    print(f"  Record:          {wins}-{losses}-{draws} ({total} fights)")
    print(f"  Age:             {age}")
    print(f"  Reach:           {p.get('reach_cm', 'N/A')} cm ({round(p.get('reach_cm', 0)/2.54, 1) if p.get('reach_cm') else 'N/A'}\")")
    print(f"\n-- STRIKING (UFC Stats) --")
    print(f"  SLpM:            {p.get('slpm', 0.0)}")
    print(f"  SApM:            {p.get('sapm', 0.0)}")
    print(f"  Str. Def:        {p.get('str_def', 0.0)}%")
    print(f"\n-- GRAPPLING (UFC Stats) --")
    print(f"  TD Avg:          {p.get('td_avg', 0.0)} per 15 min")
    print(f"  TD Def:          {p.get('td_def', 0.0)}%")
    print(f"  Sub Avg:         {p.get('sub_avg', 0.0)} per 15 min")
    print(f"\n-- COMPUTED SCORES --")
    print(f"  Power Score:     {power:.4f}  (damage creation)")
    print(f"  Finisher Score:  {finisher:.4f}  (damage conversion)")
    print(f"  Anti-Wrestling:  {anti_w:.4f}  (higher = harder to hold down)")
    print(f"  Control Proxy:   {control:.4f}  (tanh-saturated, not capped at 1.0)")
    print(f"  Archetype:       {archtype}")
    print(f"\n-- RISK FLAGS --")
    print(f"  TDD Liability:   {bp.tdd_liability(p.get('td_def', 0.0))}")
    print(f"  StrDef Liability:{bp.str_def_liability(p.get('str_def', 0.0))}")
    print(f"  Fragile:         {fragile} (UNC mult: {unc_mult:.3f})")

display_profile("TUCO TOKKOS", pa)
display_profile("IVAN ERSLAN", pb)

# Matchup interaction summary
print(f"\n{'='*55}")
print(f"  MATCHUP INTERACTION SUMMARY")
print(f"{'='*55}")
style_diff, strike_edge = bp.matchup_score(pa, pb, 'Light Heavyweight')
base_a = bp.fighter_base_score(pa)
base_b = bp.fighter_base_score(pb)

chain_factor_a = min(1.0, pa.get('td_avg', 0.0) / 3.5)
chain_factor_b = min(1.0, pb.get('td_avg', 0.0) / 3.5)
entry_factor_a = bp.compute_wrestling_entry_factor(pa, pb)
entry_factor_b = bp.compute_wrestling_entry_factor(pb, pa)
anti_w_a = bp.compute_anti_wrestling_score(pa)
anti_w_b = bp.compute_anti_wrestling_score(pb)

eff_pressure_b_from_a = bp.tdd_liability(pb.get('td_def', 0.0)) * chain_factor_a * entry_factor_a * (1.0 - anti_w_b * 0.35)
eff_pressure_a_from_b = bp.tdd_liability(pa.get('td_def', 0.0)) * chain_factor_b * entry_factor_b * (1.0 - anti_w_a * 0.35)

print(f"\n  Base Score Tuco:           {base_a:.4f}")
print(f"  Base Score Ivan:           {base_b:.4f}")
print(f"  Base Diff:                 {base_a - base_b:+.4f}")
print(f"  Style Diff:                {style_diff:+.4f}")
print(f"  Strike Edge:               {strike_edge:+.4f}")
print(f"\n-- WRESTLING INTERACTION BREAKDOWN --")
print(f"  Tuco chain factor:         {chain_factor_a:.3f}  (how often does Tuco chain TDs?)")
print(f"  Ivan chain factor:         {chain_factor_b:.3f}")
print(f"  Tuco entry factor vs Ivan: {entry_factor_a:.3f}  (can Tuco close/shoot vs Ivan?)")
print(f"  Ivan entry factor vs Tuco: {entry_factor_b:.3f}")
print(f"  Tuco effective TD pressure on Ivan: {eff_pressure_b_from_a:.4f}")
print(f"  Ivan effective TD pressure on Tuco: {eff_pressure_a_from_b:.4f}")
print(f"\n  Raw TDD Liability Tuco (33%): {bp.tdd_liability(pa.get('td_def',0.0))}")
print(f"  Raw TDD Liability Ivan (61%): {bp.tdd_liability(pb.get('td_def',0.0))}")
print(f"\n  Note: Tuco's 0.90 liability is conditioned on Ivan's chain factor ({chain_factor_b:.3f})")
print(f"  -> Ivan barely shoots (TD Avg {pb.get('td_avg',0)}) so Tuco's TDD hole is NOT catastrophic in this specific fight")
