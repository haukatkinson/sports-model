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

dominant_path_logit, secondary_logit, db, dominant_path_name = bp.compute_logit_components(pa, pb, 'Light Heavyweight')

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
lp = (logit_base * 0.4 + dominant_path_logit * 0.6 + secondary_logit + db + age) * unc
p = bp.sigmoid(lp)

regime, dominant = bp.detect_fight_regime(pa, pb)

# Raw path scores for display
tdd_vuln_a = max(0.0, 1.0 - pa.get('td_def', 0.0) / 100.0)
tdd_vuln_b = max(0.0, 1.0 - pb.get('td_def', 0.0) / 100.0)
td_vol_a   = min(1.0, pa.get('td_avg', 0.0) / 3.5)
td_vol_b   = min(1.0, pb.get('td_avg', 0.0) / 3.5)
wrestling_path = math.tanh((td_vol_a * tdd_vuln_b - td_vol_b * tdd_vuln_a) * 2.5) * 1.3

slpm_a, slpm_b = pa.get('slpm', 0.0), pb.get('slpm', 0.0)
sapm_a, sapm_b = pa.get('sapm', 0.0), pb.get('sapm', 0.0)
sdef_a, sdef_b = pa.get('str_def', 0.0), pb.get('str_def', 0.0)
striking_raw  = (slpm_a - slpm_b) * 0.18 + ((sdef_a - sdef_b) / 100.0) * 0.30 + (sapm_b - sapm_a) * 0.12
striking_path = math.tanh(striking_raw * 2.0) * 1.3

sub_threat_a = pa.get('sub_avg', 0.0) * bp.tdd_liability(pb.get('td_def', 0.0))
sub_threat_b = pb.get('sub_avg', 0.0) * bp.tdd_liability(pa.get('td_def', 0.0))
sub_path = math.tanh((sub_threat_a - sub_threat_b) * 2.0) * 1.0

print()
print("  TUCO TOKKOS vs IVAN ERSLAN — LOGIT SCORECARD")
print("  ================================================")
print(f"  ML model prob (raw):        {p_raw:.1%}")
print()
print(f"  BASE LOGIT (raw):           {base_raw:+.4f}")
print(f"  Base logit (x0.4):          {logit_base * 0.4:+.4f}")
print()
print("  PATH SCORES (pre-gate)")
print(f"  Wrestling path:             {wrestling_path:+.4f}")
print(f"  Striking path:              {striking_path:+.4f}")
print(f"  Submission path:            {sub_path:+.4f}")
print(f"  *** Dominant ({dominant_path_name}):  {dominant_path_logit:+.4f}  (x0.6 = {dominant_path_logit*0.6:+.4f})")
print(f"  Secondary blend (x0.15):    {secondary_logit:+.4f}")
print(f"  Age adjust:                 {age:+.4f}")
print()
print(f"  REGIME:  {regime}  (dominant: {dominant})")
print(f"  Dominance bonus:            {db:+.4f}")
print(f"  Uncertainty factor:         {unc:.4f}")
print()
print(f"  FINAL LOGIT:                {lp:+.4f}")
print("  ================================================")
print(f"  Tuco win prob:              {p:.1%}")
print(f"  Ivan win prob:              {1-p:.1%}")
print()
print("  WRESTLING BREAKDOWN")
print(f"  TD vol / TDD vuln (Tuco):   {td_vol_a:.3f} vol / {tdd_vuln_a:.3f} vuln")
print(f"  TD vol / TDD vuln (Ivan):   {td_vol_b:.3f} vol / {tdd_vuln_b:.3f} vuln")
print(f"  w_dom_a (Tuco on Ivan):     {td_vol_a * tdd_vuln_b:.4f}")
print(f"  w_dom_b (Ivan on Tuco):     {td_vol_b * tdd_vuln_a:.4f}")
print()
print("  OTHER PROFILE SCORES")
print(f"  Power score   (Tuco/Ivan):  {bp.compute_power_score(pa):.4f} / {bp.compute_power_score(pb):.4f}")
print(f"  Control proxy (Tuco/Ivan):  {bp.compute_control_proxy(pa):.4f} / {bp.compute_control_proxy(pb):.4f}")
print(f"  Anti-wrestling(Tuco/Ivan):  {bp.compute_anti_wrestling_score(pa):.4f} / {bp.compute_anti_wrestling_score(pb):.4f}")
print(f"  TDD liability (Tuco/Ivan):  {bp.tdd_liability(pa.get('td_def', 0)):.2f} / {bp.tdd_liability(pb.get('td_def', 0)):.2f}")
