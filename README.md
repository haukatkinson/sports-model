# UFC Sports Model

Developer build order implemented:

1. Database schema and PHP DB connection
2. Data pipeline scaffold (scrape -> normalize -> CSV)
3. Feature engineering columns for fighter comparison
4. Baseline Logistic Regression model training
5. Flask `/predict` API
6. PHP pages that call the API

## Project Structure

```
sports-model-1/
├── php/
│   ├── db.php
│   ├── index.php
│   ├── ufc.php
│   └── fighters.php
├── api/
│   ├── app.py
│   └── predict.py
├── database/
│   └── schema.sql
├── data/
│   ├── raw_fights.csv
│   ├── nearest_event_fights.csv
│   └── model_features.csv
├── models/
│   └── model.pkl
├── scripts/
│   ├── scrape_ufc.py
│   ├── build_feature_engine.py
│   └── train_model.py
├── README.md
└── ufc.php
```

## Quick Start

### 1) Create MySQL database and tables

Run `database/schema.sql` in your MySQL client.

### 2) Configure PHP DB connection

Set environment variables on server/local shell:

- `DB_HOST`
- `DB_USER`
- `DB_PASS`
- `DB_NAME` (default: `ufc_db`)

### 3) Python dependencies

Install:

- flask
- pandas
- scikit-learn
- joblib
- numpy
- requests
- beautifulsoup4
- mysql-connector-python

Example:

`pip install flask pandas scikit-learn joblib numpy requests beautifulsoup4 mysql-connector-python`

### 4) Build initial dataset + model

From project root:

`python scripts/scrape_ufc.py`

`python scripts/build_feature_engine.py`

`python scripts/train_model.py`

### 5) Start API

`python api/app.py`

### 6) Open PHP UI

Serve `php/` from your PHP environment and open `php/index.php`.

## Notes

- `scripts/scrape_ufc.py` now scrapes UFCStats completed events, normalizes feature rows, writes CSV, and attempts MySQL upserts.
- It currently targets only the nearest completed event with available fight stats.
- Event fight-level stats are exported to `data/nearest_event_fights.csv`.
- `scripts/build_feature_engine.py` builds leakage-safe pre-fight features and upserts `fighter_fight_metrics`.
- Advanced engineered features include age curve, power, durability (weak jaw proxy), grappling/control, cardio, and strength of schedule.
- Model baseline is Logistic Regression as requested.
- `api/predict.py` uses these engineered feature fields:
	- `strike_diff`
	- `takedown_diff`
	- `reach_diff`
	- `win_streak_diff`
	- `age_diff`
	- `experience_diff`
	- `finish_rate_diff`

## Landing Page Tracking Automation

- `php/index.php` now writes model picks/tier snapshots to `data/prediction_history.csv` automatically.
- After an event is completed, run:

`python scripts/update_prediction_results.py`

- This fills `actual_winner` values for pending rows so the tracked record and tier stats on the landing page update.
