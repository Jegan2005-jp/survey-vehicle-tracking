from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
from ultralytics import YOLO

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Track:
    track_id: int
    bbox_xyxy: Tuple[float, float, float, float]
    centroid_xy: Tuple[float, float]
    confidence: float
    vehicle_type: str


class VehicleTracker:
    """
    Real-time multi-object tracking using Ultralytics YOLO + ByteTrack.

    Notes:
    - This uses Ultralytics `model.track(..., persist=True)` which keeps tracker state
      across consecutive calls.
    - The implementation assumes a single active survey session per backend process
      (for reliable track_id state). You can extend to multiple sessions by running
      multiple model workers.
    """

    # Default COCO mappings. Custom models may have additional class names.
    _yolo_name_to_vehicle_type_default: Dict[str, str] = {
        "bicycle": "bike",
        "motorcycle": "bike",
        "car": "car",
        "truck": "truck",
        "bus": "bus",
        # Optional: if your model includes them, map to "other vehicle".
        "auto-rickshaw": "other vehicle",
        "auto rickshaw": "other vehicle",
        "rickshaw": "other vehicle",
        "rickshaw (auto)": "other vehicle",
    }

    def __init__(
        self,
        yolo_model_path: str,
        conf: float,
        iou: float,
        tracker_cfg: str,
        van_aspect_ratio_threshold: float,
        van_min_area_pixels: int,
        only_vehicle_classes: bool = True,
    ):
        self._model = YOLO(yolo_model_path)
        self._conf = float(conf)
        self._iou = float(iou)
        self._tracker_cfg = tracker_cfg

        self._van_aspect_ratio_threshold = float(van_aspect_ratio_threshold)
        self._van_min_area_pixels = int(van_min_area_pixels)

        self._class_names = self._resolve_class_names()
        if only_vehicle_classes:
            self._allowed_yolo_names = set(self._yolo_name_to_vehicle_type_default.keys()) | {"car", "truck", "bus", "bicycle", "motorcycle"}
        else:
            self._allowed_yolo_names = set(self._class_names.values())

    def _resolve_class_names(self) -> Dict[int, str]:
        names = getattr(self._model, "names", None)
        if isinstance(names, dict):
            return {int(k): str(v) for k, v in names.items()}
        if isinstance(names, list):
            return {i: str(n) for i, n in enumerate(names)}
        model_names = getattr(getattr(self._model, "model", None), "names", None)
        if isinstance(model_names, dict):
            return {int(k): str(v) for k, v in model_names.items()}
        if isinstance(model_names, list):
            return {i: str(n) for i, n in enumerate(model_names)}
        logger.warning("Could not resolve class names for YOLO model.")
        return {}

    @staticmethod
    def _centroid_from_xyxy(bbox_xyxy: Tuple[float, float, float, float]) -> Tuple[float, float]:
        x1, y1, x2, y2 = bbox_xyxy
        return (x1 + x2) / 2.0, (y1 + y2) / 2.0

    def _map_to_vehicle_type(
        self, yolo_class_name: str, bbox_xyxy: Tuple[float, float, float, float]
    ) -> str:
        if yolo_class_name in self._yolo_name_to_vehicle_type_default:
            mapped = self._yolo_name_to_vehicle_type_default[yolo_class_name]
            if mapped != "car":
                return mapped

        # Practical van heuristic: COCO has no dedicated van class.
        if yolo_class_name == "car":
            x1, y1, x2, y2 = bbox_xyxy
            width = max(1.0, x2 - x1)
            height = max(1.0, y2 - y1)
            area = float(width * height)
            aspect_ratio = float(width / height)
            if area >= self._van_min_area_pixels and aspect_ratio >= self._van_aspect_ratio_threshold:
                return "van"
            return "car"

        return "other vehicle"

    def update(self, frame_bgr: np.ndarray) -> List[Track]:
        try:
            results = self._model.track(
                frame_bgr,
                persist=True,
                conf=self._conf,
                iou=self._iou,
                tracker=self._tracker_cfg,
                verbose=False,
            )
        except Exception:
            logger.exception("YOLO track() failed; returning empty tracks.")
            return []

        if not results:
            return []

        res = results[0]
        boxes = res.boxes
        if boxes is None or boxes.id is None:
            return []

        xyxy = boxes.xyxy.cpu().numpy()
        confs = boxes.conf.cpu().numpy()
        clss = boxes.cls.cpu().numpy().astype(int)
        ids = boxes.id.cpu().numpy().astype(int)

        tracks: List[Track] = []
        for (x1, y1, x2, y2), c, cls_id, track_id in zip(xyxy, confs, clss, ids):
            yolo_class_name = self._class_names.get(int(cls_id), str(int(cls_id)))
            if yolo_class_name not in self._allowed_yolo_names:
                continue

            bbox_xyxy = (float(x1), float(y1), float(x2), float(y2))
            centroid_xy = self._centroid_from_xyxy(bbox_xyxy)
            vehicle_type = self._map_to_vehicle_type(yolo_class_name, bbox_xyxy)

            tracks.append(
                Track(
                    track_id=int(track_id),
                    bbox_xyxy=bbox_xyxy,
                    centroid_xy=centroid_xy,
                    confidence=float(c),
                    vehicle_type=vehicle_type,
                )
            )

        return tracks

