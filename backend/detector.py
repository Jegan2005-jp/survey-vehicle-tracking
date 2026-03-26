from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np
from ultralytics import YOLO


@dataclass(frozen=True)
class Detection:
    bbox_xyxy: Tuple[float, float, float, float]
    class_id: int
    confidence: float


class YoloDetector:
    """Single-frame detector using Ultralytics YOLO."""

    def __init__(self, model_path: str, conf: float, iou: float):
        self._model = YOLO(model_path)
        self._conf = float(conf)
        self._iou = float(iou)

    def detect(self, frame_bgr: np.ndarray, class_filter: Optional[List[int]] = None) -> List[Detection]:
        results = self._model.predict(frame_bgr, conf=self._conf, iou=self._iou, verbose=False)
        if not results:
            return []
        res = results[0]
        boxes = res.boxes
        if boxes is None:
            return []

        xyxy = boxes.xyxy.cpu().numpy()
        confs = boxes.conf.cpu().numpy()
        clss = boxes.cls.cpu().numpy().astype(int)

        out: List[Detection] = []
        for (x1, y1, x2, y2), c, cls_id in zip(xyxy, confs, clss):
            if class_filter is not None and cls_id not in class_filter:
                continue
            out.append(Detection(bbox_xyxy=(float(x1), float(y1), float(x2), float(y2)), class_id=int(cls_id), confidence=float(c)))
        return out

