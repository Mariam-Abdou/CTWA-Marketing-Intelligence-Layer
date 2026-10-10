import json
import logging
from pathlib import Path


def load_json(path: str | Path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


UNKNOWN = "unknown"
_warned: set = set()


def validate_enum(value, allowed: set, field_name: str, record_id: str):
    """Return value if known, else the general value "unknown" (and warn once
    per distinct value) so one new label in fresh data cannot abort the run.
    Downstream: an "unknown" outcome type is excluded from scoring (amounts.py),
    an "unknown" platform is treated like any non-organic source."""
    if value in allowed:
        return value
    if (field_name, value) not in _warned:
        _warned.add((field_name, value))
        logging.getLogger(__name__).warning(
            "%s %r (first seen on %s) is not in %s: treated as %r", field_name, value, record_id, sorted(allowed), UNKNOWN)
    return UNKNOWN