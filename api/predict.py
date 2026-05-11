from __future__ import annotations

from pathlib import Path
from typing import Dict, Tuple

import joblib
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH = ROOT / "models" / "model.pkl"
FEATURES_PATH = ROOT / "models" / "features.txt"

FEATURE_COLUMNS = [
    "strike_diff",
    "takedown_diff",
    "reach_diff",
    "win_streak_diff",
    "age_diff",
    "experience_diff",
    "finish_rate_diff",
]


def _load_model_and_features():
    if not MODEL_PATH.exists() or not FEATURES_PATH.exists():
        return None, FEATURE_COLUMNS
    try:
        model = joblib.load(MODEL_PATH)
        features = [line.strip() for line in FEATURES_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
        if not features:
            features = FEATURE_COLUMNS
        return model, features
    except Exception:
        return None, FEATURE_COLUMNS


def _build_feature_row(payload: Dict, features: list[str]) -> pd.DataFrame:
    row = {}
    for feature in features:
        value = payload.get(feature, 0)
        row[feature] = float(value)
    return pd.DataFrame([row])


def predict_fight(payload: Dict) -> Tuple[str, Dict[str, float]]:
    fighter_a = payload.get("fighterA", "Fighter A")
    fighter_b = payload.get("fighterB", "Fighter B")

    model, features = _load_model_and_features()
    feature_df = _build_feature_row(payload, features)

    if model is None:
        prob_a = 0.5
    else:
        X = feature_df[features].to_numpy(dtype=np.float32)
        proba = model.predict_proba(X)[0]
        prob_a = float(proba[1])

    prob_b = 1.0 - prob_a
    winner = fighter_a if prob_a >= prob_b else fighter_b

    return winner, {
        fighter_a: prob_a,
        fighter_b: prob_b,
    }
