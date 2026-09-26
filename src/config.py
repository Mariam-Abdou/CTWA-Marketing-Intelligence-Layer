"""
Single source of truth for tunable thresholds -- see config.yaml at the repo
root for the actual values and their grounding. Every module that used to
hardcode a constant (PRIOR_STRENGTH, PROBABILITY_THRESHOLD, fatigue/CPA
thresholds, EXPLOIT_SHARE/EXPLORE_SHARE, ...) now reads it from here, so the
pipeline is config-driven: change config.yaml, re-run, nothing to edit in code.
"""
from functools import lru_cache
from pathlib import Path

import yaml

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"


@lru_cache(maxsize=1)
def load_config(path: str | Path = CONFIG_PATH) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)
