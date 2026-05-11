from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Dict, Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "data" / "nearest_event_fights.csv"
OUTPUT_PATH = ROOT / "data" / "nearest_event_predictions.json"

import sys

API_DIR = ROOT / "api"
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from predict import predict_fight  # noqa: E402


def build_fight_key(fighter_a: str, fighter_b: str) -> str:
    return hashlib.md5(f"{fighter_a}|{fighter_b}".encode("utf-8")).hexdigest()


def main() -> None:
    if not DATA_PATH.exists():
        raise FileNotFoundError(f"Missing input file: {DATA_PATH}")

    df = pd.read_csv(DATA_PATH)
    if "fighterA" not in df.columns or "fighterB" not in df.columns:
        raise ValueError("Input CSV must include fighterA and fighterB columns")

    predictions: Dict[str, Any] = {}
    for _, row in df.iterrows():
        fighter_a = str(row.get("fighterA", "")).strip()
        fighter_b = str(row.get("fighterB", "")).strip()
        if not fighter_a or not fighter_b:
            continue

        payload = {
            "fighterA": fighter_a,
            "fighterB": fighter_b,
        }
        winner, probabilities = predict_fight(payload)

        predictions[build_fight_key(fighter_a, fighter_b)] = {
            "winner": winner,
            "probabilities": {
                fighter_a: float(probabilities.get(fighter_a, 0.0)),
                fighter_b: float(probabilities.get(fighter_b, 0.0)),
            },
        }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(predictions, indent=2), encoding="utf-8")

    print(f"Wrote {len(predictions)} predictions to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
