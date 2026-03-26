from __future__ import annotations

from typing import Any, Dict

import numpy as np

from backend.utils import decode_jpeg_from_base64


def decode_frame_message(payload: Dict[str, Any]) -> tuple[np.ndarray, int]:
    """
    Decode a websocket message payload into:
      (frame_bgr, frame_number)

    Expected keys:
      - imageData: base64 JPEG or data URL
      - frameNumber: integer
    """
    img = payload.get("imageData")
    if not isinstance(img, str):
        raise ValueError("Missing imageData in payload")
    frame_number = payload.get("frameNumber")
    if not isinstance(frame_number, int):
        frame_number = int(frame_number) if frame_number is not None else 0
    frame_bgr = decode_jpeg_from_base64(img)
    return frame_bgr, frame_number

