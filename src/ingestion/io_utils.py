import json
from pathlib import Path


def load_json(path: str | Path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def validate_enum(value, allowed: set, field_name: str, record_id: str):
    if value not in allowed:
        raise ValueError(f"{field_name} '{value}' on '{record_id}' not in {allowed}")
    return value