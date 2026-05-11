from __future__ import annotations

from pathlib import Path

import joblib
import pandas as pd
from sklearn.linear_model import LogisticRegression

ROOT = Path(__file__).resolve().parents[1]
RAW_PATH = ROOT / "data" / "raw_fights.csv"
FEATURE_DATA_PATH = ROOT / "data" / "model_features.csv"
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


def build_training_set(df: pd.DataFrame):
    X = df[FEATURE_COLUMNS].copy()
    y = (df["winner"] == df["fighterA"]).astype(int)
    return X, y


def build_training_set_from_feature_engine(df: pd.DataFrame):
    if "fighterA_won" not in df.columns:
        raise ValueError("Feature dataset is missing required target column: fighterA_won")

    blocked_columns = {
        "fighterA_won",
        "fight_id",
        "event_date",
        "fighterA_id",
        "fighterB_id",
    }
    candidate_columns = [col for col in df.columns if col not in blocked_columns]

    X = df[candidate_columns].apply(pd.to_numeric, errors="coerce").fillna(0.0)
    y = pd.to_numeric(df["fighterA_won"], errors="coerce").fillna(0).astype(int)

    valid_columns = [col for col in X.columns if X[col].nunique() > 1 or len(X) == 1]
    if not valid_columns:
        raise ValueError("Feature dataset has no usable numeric feature columns")

    X = X[valid_columns]
    return X, y


def main() -> None:
    if FEATURE_DATA_PATH.exists():
        df = pd.read_csv(FEATURE_DATA_PATH)
        X, y = build_training_set_from_feature_engine(df)
        feature_columns = list(X.columns)
        source_path = FEATURE_DATA_PATH
    else:
        if not RAW_PATH.exists():
            raise FileNotFoundError(
                f"Missing dataset at {RAW_PATH}. Run scripts/scrape_ufc.py first."
            )
        df = pd.read_csv(RAW_PATH)
        X, y = build_training_set(df)
        feature_columns = FEATURE_COLUMNS
        source_path = RAW_PATH

    if y.nunique() < 2:
        raise ValueError(
            "Training data has only one class. Scrape more fights before training."
        )

    model = LogisticRegression(max_iter=1000)
    model.fit(X, y)

    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, MODEL_PATH)
    FEATURES_PATH.write_text("\n".join(feature_columns), encoding="utf-8")

    print(f"Model trained from {source_path} and saved to {MODEL_PATH}")


if __name__ == "__main__":
    main()
