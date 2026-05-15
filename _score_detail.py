import sys, math
sys.path.insert(0, 'api')
import build_event_predictions as bep
import pandas as pd

df = pd.read_csv('data/nearest_event_fights.csv')
row = df[df.fighterA.str.contains('Bannon')].iloc[0]
cache = {}
pa = bep.parse_fighter_profile(row['fighterA_url'], cache)
pb = bep.parse_fighter_profile(row['fighterB_url'], cache)

td_def_a  = float(pa.get('td_def', 0))
td_def_b  = float(pb.get('td_def', 0))
td_avg_a  = float(pa.get('td_avg', 0))
td_avg_b  = float(pb.get('td_avg', 0))
slpm_a    = float(pa.get('slpm', 0))
slpm_b    = float(pb.get('slpm', 0))
sapm_a    = float(pa.get('sapm', 0))
sapm_b    = float(pb.get('sapm', 0))
str_def_a = float(pa.get('str_def', 0))
str_def_b = float(pb.get('str_def', 0))
sub_avg_a = float(pa.get('sub_avg', 0))
sub_avg_b = float(pb.get('sub_avg', 0))
reach_a   = float(pa.get('reach_cm', 175))
reach_b   = float(pb.get('reach_cm', 175))

tdd_lib_a = bep.tdd_liability(td_def_a)
tdd_lib_b = bep.tdd_liability(td_def_b)
power_a   = bep.compute_power_score(pa)
power_b   = bep.compute_power_score(pb)
entry_a   = bep.compute_grappling_entry_prob(pa, pb)
entry_b   = bep.compute_grappling_entry_prob(pb, pa)

td_vol_a   = min(1.0, td_avg_a / 3.5)
td_vol_b   = min(1.0, td_avg_b / 3.5)
tdd_vuln_a = max(0.0, 1.0 - td_def_a / 100.0)
tdd_vuln_b = max(0.0, 1.0 - td_def_b / 100.0)

print("=== ENTRY GATING ===")
print(f"  td_def_a={td_def_a}%  tdd_vuln_a={tdd_vuln_a:.3f}  tdd_lib_a={tdd_lib_a:.3f}")
print(f"  td_def_b={td_def_b}%  tdd_vuln_b={tdd_vuln_b:.3f}  tdd_lib_b={tdd_lib_b:.3f}")
print()
print(f"  === Bannon entry into Caliari ===")
sp_b = str_def_b / 100.0
dp_b = 1.0 - sp_b * 0.50
rg_b = max(0.0, (reach_a - reach_b)) / 20.0
rp_b = 1.0 - min(0.40, rg_b * 0.40)
dam_b = max(0.0, (sapm_b - 3.5) / 7.0)
damp_b = 1.0 - min(0.50, dam_b)
print(f"    str_def_b={str_def_b:.1f}%  → defense_penalty_on_Caliari = 1 - {sp_b:.3f}*0.50 = {dp_b:.4f}")
print(f"    reach gap (Bannon-Caliari) = {reach_a-reach_b:.1f}cm  → reach_penalty_on_Caliari = {rp_b:.4f}")
print(f"    sapm_b={sapm_b:.2f}  → damage_penalty_on_Caliari = 1 - min(0.50, {dam_b:.4f}) = {damp_b:.4f}")
print(f"    ENTRY_PROB_B = {dp_b:.4f} × {rp_b:.4f} × {damp_b:.4f} = {entry_b:.4f}")
print()
print(f"  === Caliari entry into Bannon ===")
sp_a = str_def_a / 100.0
dp_a = 1.0 - sp_a * 0.50
rg_a = max(0.0, (reach_b - reach_a)) / 20.0
rp_a = 1.0 - min(0.40, rg_a * 0.40)
dam_a = max(0.0, (sapm_a - 3.5) / 7.0)
damp_a = 1.0 - min(0.50, dam_a)
print(f"    str_def_a={str_def_a:.1f}%  → defense_penalty_on_Bannon = 1 - {sp_a:.3f}*0.50 = {dp_a:.4f}")
print(f"    reach gap (Caliari-Bannon) = {max(0, reach_b-reach_a):.1f}cm  → reach_penalty_on_Bannon = {rp_a:.4f}")
print(f"    sapm_a={sapm_a:.2f}  → damage_penalty_on_Bannon = 1 - min(0.50, {dam_a:.4f}) = {damp_a:.4f}")
print(f"    ENTRY_PROB_A = {dp_a:.4f} × {rp_a:.4f} × {damp_a:.4f} = {entry_a:.4f}")

print()
print("=== GRAPPLING DOMAIN ===")
g_adv_a_raw  = td_vol_a * tdd_vuln_b
g_adv_b_raw  = td_vol_b * tdd_vuln_a
g_adv_a_gate = g_adv_a_raw * entry_a
g_adv_b_gate = g_adv_b_raw * entry_b
print(f"  td_vol_a = min(1, {td_avg_a:.3f}/3.5) = {td_vol_a:.4f}")
print(f"  td_vol_b = min(1, {td_avg_b:.3f}/3.5) = {td_vol_b:.4f}")
print(f"  tdd_vuln_a = 1 - {td_def_a}/100 = {tdd_vuln_a:.4f}")
print(f"  tdd_vuln_b = 1 - {td_def_b}/100 = {tdd_vuln_b:.4f}")
print(f"  raw_adv_a (no gate) = {td_vol_a:.4f} × {tdd_vuln_b:.4f} = {g_adv_a_raw:.4f}")
print(f"  raw_adv_b (no gate) = {td_vol_b:.4f} × {tdd_vuln_a:.4f} = {g_adv_b_raw:.4f}")
print(f"  gated_adv_a = {g_adv_a_raw:.4f} × entry_a({entry_a:.4f}) = {g_adv_a_gate:.4f}")
print(f"  gated_adv_b = {g_adv_b_raw:.4f} × entry_b({entry_b:.4f}) = {g_adv_b_gate:.4f}")
g_logit = math.tanh(g_adv_a_gate / 0.50) - math.tanh(g_adv_b_gate / 0.50)
print(f"  tanh({g_adv_a_gate:.4f}/0.50) = {math.tanh(g_adv_a_gate/0.50):.4f}")
print(f"  tanh({g_adv_b_gate:.4f}/0.50) = {math.tanh(g_adv_b_gate/0.50):.4f}")
print(f"  GRAPPLING_LOGIT = {g_logit:+.6f}  → {'Bannon' if g_logit > 0 else 'Caliari'}")

print()
print("=== STRIKING DOMAIN ===")
reach_diff = reach_a - reach_b
s_adv_a = (slpm_a*0.20 + (str_def_a/100)*0.35 + sapm_b*0.14 + power_a*0.50 + max(0.0, reach_diff/100)*0.06)
s_adv_b = (slpm_b*0.20 + (str_def_b/100)*0.35 + sapm_a*0.14 + power_b*0.50 + max(0.0,-reach_diff/100)*0.06)
s_logit = math.tanh(s_adv_a/1.50) - math.tanh(s_adv_b/1.50)
print(f"  BANNON:   slpm={slpm_a*0.20:.4f}  str_def={(str_def_a/100)*0.35:.4f}  sapm_opp={sapm_b*0.14:.4f}  power={power_a*0.50:.4f}  reach={max(0.0,reach_diff/100)*0.06:.4f}  TOTAL={s_adv_a:.4f}")
print(f"  CALIARI:  slpm={slpm_b*0.20:.4f}  str_def={(str_def_b/100)*0.35:.4f}  sapm_opp={sapm_a*0.14:.4f}  power={power_b*0.50:.4f}  reach=0.0000  TOTAL={s_adv_b:.4f}")
print(f"  tanh(Bannon/1.50)={math.tanh(s_adv_a/1.50):.4f}  tanh(Caliari/1.50)={math.tanh(s_adv_b/1.50):.4f}")
print(f"  STRIKING_LOGIT = {s_logit:+.6f}  → {'Bannon' if s_logit > 0 else 'Caliari'}")

print()
print("=== SUBMISSION DOMAIN ===")
sub_adv_a = max(0.0, sub_avg_a * tdd_lib_b * (1.0 + td_vol_a * 0.5) * entry_a)
sub_adv_b = max(0.0, sub_avg_b * tdd_lib_a * (1.0 + td_vol_b * 0.5) * entry_b)
sub_logit = math.tanh(sub_adv_a/0.40) - math.tanh(sub_adv_b/0.40)
print(f"  sub_avg_a={sub_avg_a:.4f} × tdd_lib_b({tdd_lib_b:.4f}) × (1+{td_vol_a:.3f}*0.5) × entry_a({entry_a:.4f}) = {sub_adv_a:.4f}")
print(f"  sub_avg_b={sub_avg_b:.4f} × tdd_lib_a({tdd_lib_a:.4f}) × (1+{td_vol_b:.3f}*0.5) × entry_b({entry_b:.4f}) = {sub_adv_b:.4f}")
print(f"  SUBMISSION_LOGIT = {sub_logit:+.6f}  → {'Bannon' if sub_logit > 0 else 'Caliari'}")

print()
print("=== POWER SCORES ===")
print(f"  power_a (Bannon)  = {power_a:.4f}")
print(f"  power_b (Caliari) = {power_b:.4f}")

det = bep.compute_logit_components_detailed(pa, pb, "Women's Strawweight")
regime = det['regime']
main   = det['main_logit']
supp   = det['support_logit']
rs     = det['regime_strength']
rw     = det['regime_weakness']
inter  = det['interaction_logit']
assembled = main*rs + supp*rw + inter

print()
print("=== REGIME & FINAL ASSEMBLY ===")
print(f"  regime = {regime}")
print(f"  main_logit   = {main:+.6f}  × regime_strength {rs:.2f}  = {main*rs:+.6f}")
print(f"  support_logit= {supp:+.6f}  × regime_weakness {rw:.2f}  = {supp*rw:+.6f}")
print(f"  interaction  = {inter:+.6f}")
print(f"  assembled_path_logit = {assembled:+.6f}")
print()
print(f"  FINAL_PROB_Bannon  = sigmoid({assembled:.6f}) = {1/(1+math.exp(-assembled)):.4f}")
print(f"  FINAL_PROB_Caliari = {1 - 1/(1+math.exp(-assembled)):.4f}")
print()
print("  (Note: assembled_path_logit feeds into main() alongside base logit + uncertainty compression)")
