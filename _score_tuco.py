import sys, math
sys.path.insert(0, 'api')
import build_event_predictions as bp
from predict import predict_fight

cache = {}
pa = bp.parse_fighter_profile('http://ufcstats.com/fighter-details/3e8118c1ab52f211', cache, include_sos=False)
pb = bp.parse_fighter_profile('http://ufcstats.com/fighter-details/64ad3e3b0efa30bb', cache, include_sos=False)
payload = bp.build_model_feature_payload('Tuco Tokkos', 'Ivan Erslan', pa, pb)
_, probs = predict_fight(payload)

p_raw = float(probs.get('Tuco Tokkos', 0.5))
p_clamped = max(0.01, min(0.99, p_raw))
base_raw = math.log(p_clamped / (1 - p_clamped))
logit_base = math.tanh(base_raw * 0.6) * 1.2 * 0.55

dominant_path_logit, secondary_logit, regime_multiplier, dominant_path_name, regime = bp.compute_logit_components(pa, pb, 'Light Heavyweight')

aa, ab_ = bp.fighter_age(pa), bp.fighter_age(pb)
age = 0.0
if aa is not None and ab_ is not None:
    gap = aa - ab_
    heavier = bp.is_heavy_division('Light Heavyweight')
    if aa >= 37 and ab_ <= 33 and gap >= 5:
        age = -0.35 if heavier else -0.55
    elif ab_ >= 37 and aa <= 33 and (-gap) >= 5:
        age = 0.35 if heavier else 0.55

unc = bp.compute_uncertainty_factor(pa, pb, 'Light Heavyweight')
logit_components = regime_multiplier * dominant_path_logit + secondary_logit + age
lp = (logit_base + logit_components) * unc
p = bp.sigmoid(lp)

_, dominant_side = bp.detect_fight_regime(pa, pb)

# Raw path scores for display
td_vol_a   = min(1.0, pa.get('td_avg', 0.0) / 3.5)
td_vol_b   = min(1.0, pb.get('td_avg', 0.0) / 3.5)
tdd_vuln_a = max(0.0, 1.0 - pa.get('td_def', 0.0) / 100.0)
tdd_vuln_b = max(0.0, 1.0 - pb.get('td_def', 0.0) / 100.0)
w_dom_a    = td_vol_a * tdd_vuln_b
w_dom_b    = td_vol_b * tdd_vuln_a
wrestling_path = math.tanh((w_dom_a - w_dom_b) * 2.5) * 0.85

slpm_a, slpm_b = pa.get('slpm', 0.0), pb.get('slpm', 0.0)
sapm_a, sapm_b = pa.get('sapm', 0.0), pb.get('sapm', 0.0)
sdef_a, sdef_b = pa.get('str_def', 0.0), pb.get('str_def', 0.0)
pw_a, pw_b    = bp.compute_power_score(pa), bp.compute_power_score(pb)
striking_raw  = (slpm_a - slpm_b)*0.20 + ((sdef_a - sdef_b)/100.0)*0.35 + (sapm_b - sapm_a)*0.14 + (pw_a - pw_b)*0.50
striking_path = math.tanh(striking_raw * 2.0) * 0.85

tdd_lib_a = bp.tdd_liability(pa.get('td_def', 0.0))
tdd_lib_b = bp.tdd_liability(pb.get('td_def', 0.0))
sub_threat_a = pa.get('sub_avg', 0.0) * tdd_lib_b * (1.0 + td_vol_a * 0.5)
sub_threat_b = pb.get('sub_avg', 0.0) * tdd_lib_a * (1.0 + td_vol_b * 0.5)
sub_path = math.tanh((sub_threat_a - sub_threat_b) * 2.0) * 0.70

print()
print("  TUCO TOKKOS vs IVAN ERSLAN — LOGIT SCORECARD")
print("  ================================================")
print(f"  ML model prob (raw):        {p_raw:.1%}")
print(f"  Base logit (bounded):       {logit_base:+.4f}")
print()
print("  PATH SCORES")
print(f"  Wrestling path:             {wrestling_path:+.4f}  (w_dom_a={w_dom_a:.3f} vs w_dom_b={w_dom_b:.3f})")
print(f"  Striking path:              {striking_path:+.4f}")
print(f"  Submission path:            {sub_path:+.4f}")
print(f"  *** Dominant: [{dominant_path_name}]    {dominant_path_logit:+.4f}")
print(f"  Regime multiplier:          x{regime_multiplier:.3f}")
print(f"  Dominant x mult:            {regime_multiplier * dominant_path_logit:+.4f}")
print(f"  Secondary blend:            {secondary_logit:+.4f}")
print(f"  Age adjust:                 {age:+.4f}")
print(f"  ----------------------------------------")
print(f"  logit_components:           {logit_components:+.4f}")
print()
print(f"  REGIME: {regime}  (dominant side: {dominant_side})")
print(f"  Uncertainty factor:         {unc:.4f}")
print()
print(f"  FINAL LOGIT:                {lp:+.4f}")
print("  ================================================")
print(f"  Tuco win prob:              {p:.1%}")
print(f"  Ivan win prob:              {1-p:.1%}")
print()
print("  WRESTLING BREAKDOWN")
print(f"  TD vol (Tuco/Ivan):         {td_vol_a:.3f} / {td_vol_b:.3f}")
print(f"  TDD vuln (Tuco/Ivan):       {tdd_vuln_a:.3f} / {tdd_vuln_b:.3f}")
print(f"  TDD liability (Tuco/Ivan):  {tdd_lib_a:.2f} / {tdd_lib_b:.2f}")
print()
print("  OTHER PROFILE SCORES")
print(f"  Power score   (Tuco/Ivan):  {pw_a:.4f} / {pw_b:.4f}")
print(f"  Control proxy (Tuco/Ivan):  {bp.compute_control_proxy(pa):.4f} / {bp.compute_control_proxy(pb):.4f}")
print(f"  Anti-wrestling(Tuco/Ivan):  {bp.compute_anti_wrestling_score(pa):.4f} / {bp.compute_anti_wrestling_score(pb):.4f}")
print(f"  TDD liability (Tuco/Ivan):  {bp.tdd_liability(pa.get('td_def', 0)):.2f} / {bp.tdd_liability(pb.get('td_def', 0)):.2f}")
