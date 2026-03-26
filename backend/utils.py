from __future__ import annotations

import base64
import hashlib
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import cv2
import numpy as np


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def decode_jpeg_from_base64(img_b64: str) -> np.ndarray:
    """
    Decode JPEG bytes sent from the browser.

    Accepts either raw base64 or data URL prefixed strings.
    """
    if img_b64.startswith("data:image"):
        img_b64 = img_b64.split(",", 1)[1]
    jpeg_bytes = base64.b64decode(img_b64)
    arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("Failed to decode JPEG from base64")
    return img


def frame_to_jpeg_bytes(frame_bgr: np.ndarray, quality: int = 80) -> bytes:
    encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)]
    ok, buf = cv2.imencode(".jpg", frame_bgr, encode_param)
    if not ok:
        raise RuntimeError("Failed to encode JPEG")
    return bytes(buf.tobytes())


def make_event_id(session_id: str, track_id: int, vehicle_type: str, direction: str, entry_zone: str, exit_zone: str) -> str:
    """
    Deterministic Event ID for de-duplication.

    Limitation: If your tracker reuses track_id after a session reset, a new real-world event
    could hash to the same Event ID. This is unlikely within one running session.
    """
    raw = f"{session_id}|{track_id}|{vehicle_type}|{direction}|{entry_zone}->{exit_zone}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def ensure_relative_snapshot_path(path: str) -> str:
    if not path:
        return ""
    # Keep paths simple for spreadsheets / UI.
    return os.path.relpath(path, os.getcwd())


