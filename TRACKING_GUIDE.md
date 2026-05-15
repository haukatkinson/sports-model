# Prediction Tracking & Calibration System

This system tracks all fight predictions and measures performance to identify over-scoring patterns and suggest model adjustments for profitability.

## Quick Start

### 1. **Automatic Prediction Logging** 
Every time you run predictions, they're automatically logged:

```bash
python api/build_event_predictions.py
# Predictions are logged with: fighter names, probabilities, regime, weight class, logits
```

### 2. **Record Fight Results**
Once a fight concludes, log the result:

```bash
# Format: record_result.py "Fighter A" "Fighter B" A|B|draw [--method] [--round] [--time]

# Example: Decision Win
python scripts/record_result.py "Nikolay Veretennikov" "Khaos Williams" A --method DEC --round 3

# Example: Submission
python scripts/record_result.py "Fighter A" "Fighter B" B --method SUB --round 2 --time 4:35

# Example: Draw
python scripts/record_result.py "Fighter A" "Fighter B" draw
```

### 3. **Evaluate Prediction Performance**
Check win rates by regime, weight class, and confidence level:

```bash
python scripts/evaluate_predictions.py
```

**Output shows:**
- Overall win rate (target: 65%)
- Performance by regime (clean_dominance, contested, etc.)
- Performance by weight class
- Performance by probability confidence level

### 4. **Generate Calibration Report**
Identify underperforming patterns and get adjustment suggestions:

```bash
python scripts/calibrate_model.py
```

**Output shows:**
- Which regimes/weight classes are underperforming
- Suggested adjustment percentages
- Whether to reduce confidence or tighten regime detection

### 5. **Check Tracking Status**
View how many predictions you've logged:

```bash
python scripts/track_predictions.py
```

---

## How It Works

### Data Flow

```
build_event_predictions.py
    ↓ [logs each prediction]
data/predictions_history.json
    ↓ [user records actual result]
record_result.py
    ↓ [updates same file]
data/predictions_history.json
    ↓ [on demand analysis]
evaluate_predictions.py → Performance Report
calibrate_model.py       → Calibration Suggestions
```

### Predictions JSON Structure

```json
{
  "predictions": [
    {
      "logged_at": "2026-05-13T20:28:00.000",
      "event_date": "2026-05-13",
      "fighter_a": "Nikolay Veretennikov",
      "fighter_b": "Khaos Williams",
      "predicted_prob_a": 0.688,
      "predicted_prob_b": 0.312,
      "regime": "submission_threat",
      "weight_class": "Welterweight",
      "dom_logit": 0.3963,
      "interaction_logit": 0.0021,
      "round_win_logit": -0.0236,
      "result": null,           // Filled in when result recorded
      "result_method": null,    // e.g., "SUB", "KO/TKO", "DEC"
      "result_round": null,     // Round number (1-5)
      "result_time": null       // Time in round (e.g., "2:45")
    }
  ]
}
```

---

## Understanding Performance Reports

### Win Rate Interpretation

| Win Rate | Status | Action |
|----------|--------|--------|
| 65%+ | ✓ Profitable | Continue current model |
| 60-65% | ⚠ Monitor | Watch for patterns, may become profitable at scale |
| 55-60% | ⚠ Underperforming | Start calibration adjustments |
| <55% | ✗ Losing | Significant model changes needed |

### Regime-Specific Guidance

**Underperforming Regime (e.g., -10% vs target):**
- Suggests reducing regime strength multiplier
- Example: Contested matchups too confident → reduce interaction_logit weight

**Overperforming Regime (e.g., +10% vs target):**
- May indicate overconfidence
- Suggests verifying fight data or tightening regime thresholds

### Weight Class Patterns

**If lightweight underperforms, heavyweight overperforms:**
- Could indicate different fighter profile quality across divisions
- May need weight-class specific adjustment factors

---

## Calibration Workflow

### Phase 1: Baseline (10-15 predictions)
- Record first batch of results
- Run `evaluate_predictions.py` to establish baseline
- Expected noise; not actionable yet

### Phase 2: Pattern Identification (30-50 predictions)
- Run `calibrate_model.py`
- Look for consistent underperformance (≥-10% vs target)
- Focus on patterns with 5+ predictions before adjusting

### Phase 3: Model Adjustment (after calibration)
- Make suggested adjustments in `build_event_predictions.py`
- Test on new predictions
- Compare before/after win rates

### Phase 4: Refinement (ongoing)
- Continue tracking predictions
- Monthly calibration reports
- Adjust thresholds based on new data

---

## Common Scenarios

### Scenario 1: All Regimes Underperforming (-5% to -10%)
**Diagnosis:** Model is overconfident globally
**Fix:** Reduce ML prior multiplier from 0.25 → 0.20, or apply global dampening
**Code:** `logit_base = base_logit_raw * 0.20`

### Scenario 2: "Contested" Underperforming, Others Fine
**Diagnosis:** Interaction logit weight too high
**Fix:** Reduce `interaction_logit` weight in assembly
**Code:** `interaction_logit * 0.9` instead of `* 1.0`

### Scenario 3: One Weight Class Underperforming
**Diagnosis:** Weight class has different fighter profile quality
**Fix:** Apply weight-class specific multiplier
**Code:** Add regime multiplier adjustment for that weight class

### Scenario 4: Marginal Predictions (<0.55 confidence) Underperforming
**Diagnosis:** Model is weak on close matchups
**Fix:** Increase uncertainty dampening or reduce support layer weight
**Code:** Increase uncertainty factor or reduce `round_win_logit` weight

---

## Tips for Best Results

### 1. **Minimum Data Before Adjusting**
- Don't adjust based on <5 predictions per pattern
- Aim for 10-15 per regime before major changes

### 2. **Gradual Adjustments**
- Change one parameter at a time
- Adjust by 10-20% increments, not wholesale changes
- Monitor in next batch before bigger moves

### 3. **Separate Training vs Testing**
- Use first 20-30 predictions to calibrate
- Then test on fresh predictions to validate improvements

### 4. **Track External Factors**
- Note weight class distribution changes
- Document injury/return patterns
- Track if data quality shifts (updates to fighter profiles)

### 5. **A/B Test**
- Option 1: Create a variant model with calibration
- Run both on new fights
- Compare win rates before fully committing

---

## File Locations

- **Predictions History:** `data/predictions_history.json`
- **Prediction Logic:**  `api/build_event_predictions.py`
- **Tracking Module:** `scripts/track_predictions.py`
- **Evaluation:** `scripts/evaluate_predictions.py`
- **Calibration:** `scripts/calibrate_model.py`
- **Result Recording:** `scripts/record_result.py`

---

## Example: Full Workflow

```bash
# 1. Generate predictions (automatic tracking)
python api/build_event_predictions.py

# 2. After first 3 fights concludes, record results
python scripts/record_result.py "Fighter A" "Fighter B" A --method DEC --round 3
python scripts/record_result.py "Fighter C" "Fighter D" C --method SUB --round 1 --time 2:15
python scripts/record_result.py "Fighter E" "Fighter F" B --method DEC

# 3. Check status
python scripts/track_predictions.py

# 4. Evaluate performance
python scripts/evaluate_predictions.py

# 5. Generate calibration suggestions
python scripts/calibrate_model.py

# 6. Based on suggestions, adjust model in build_event_predictions.py

# 7. Rebuild predictions and test on fresh fights
python api/build_event_predictions.py
```

---

**Goal:** Achieve ~65% win rate for profitability. Track consistently, adjust gradually, validate thoroughly.
