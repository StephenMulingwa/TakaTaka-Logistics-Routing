import os
import time
from datetime import datetime

import pytz

WAREHOUSES = {
    "TTS": {"lat": -1.1404981978961632, "lon": 36.733312587683756},
    "Rubis Banana": {"lat": -1.1747329246654616, "lon": 36.758819701449724},
}

EAT = pytz.timezone("Africa/Nairobi")

os.environ["TZ"] = "Africa/Nairobi"
try:
    time.tzset()
except Exception:
    pass


def parse_datetime_local(value: str, label: str = "Date/time") -> datetime:
    if not value or not str(value).strip():
        raise ValueError(f"{label} is required.")
    raw = str(value).strip()
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            naive = datetime.strptime(raw, fmt)
            return EAT.localize(naive)
        except ValueError:
            continue
    raise ValueError(f"Invalid {label.lower()} format.")


def compute_route_timestamps_from_range(from_value: str, to_value: str, from_label="Route From", to_label="Route End"):
    start_time = parse_datetime_local(from_value, from_label)
    end_time = parse_datetime_local(to_value, to_label)
    tf, tt = int(start_time.timestamp()), int(end_time.timestamp())
    if tt <= tf:
        raise ValueError(f"{to_label} must be after {from_label}.")
    return tf, tt
