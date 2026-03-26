from __future__ import annotations

import csv
import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import cv2

from backend.config import AppSettings, load_direction_config
from backend.direction import DirectionClassifier
from backend.logger import GoogleSheetsLogger
from backend.tracker import Track, VehicleTracker
from backend.utils import make_event_id, utc_now

logger = logging.getLogger(__name__)


def _sum_direction_counts(direction_counts: Dict[str, int]) -> int:
    return int(sum(direction_counts.values()))


@dataclass
class SurveySession:
    session_id: str
    camera_type: str
    camera_name: str
    created_at: datetime
    frame_number: int = 0

    tracker: VehicleTracker = field(repr=False, default=None)
    direction_classifier: DirectionClassifier = field(repr=False, default=None)
    sheets_logger: GoogleSheetsLogger = field(repr=False, default=None)

    # Live UI helpers
    direction_by_track: Dict[int, str] = field(default_factory=dict)
    track_last_seen_frame: Dict[int, int] = field(default_factory=dict)
    last_logged_event: Optional[Dict[str, Any]] = None

    # FPS
    _last_proc_ts: float = field(default_factory=time.time)
    fps_ema: float = 0.0

    # Stop flag
    stopped: bool = False

    def update_fps(self) -> float:
        now = time.time()
        dt = max(1e-6, now - self._last_proc_ts)
        fps = 1.0 / dt
        self.fps_ema = fps if self.fps_ema <= 0 else (0.2 * fps + 0.8 * self.fps_ema)
        self._last_proc_ts = now
        return self.fps_ema


class SurveySessionManager:
    """
    Manages survey sessions and the frame processing loop.

    Default behavior:
    - Single active session at a time for reliable tracker state.
    """

    def __init__(self, settings: AppSettings):
        self._settings = settings
        self._lock = threading.Lock()
        self._active_session: Optional[SurveySession] = None

        # Logger is shared (de-dup keys include session_id).
        self._sheets_logger = GoogleSheetsLogger(
            settings=settings.sheets,
            sqlite_path=settings.sqlite_path,
            snapshot_dir=settings.snapshot_dir,
            snapshot_jpeg_quality=settings.snapshot_jpeg_quality,
        )

    def start_session(self, *, camera_type: str, camera_name: str) -> Dict[str, Any]:
        with self._lock:
            if self._active_session is not None and not self._active_session.stopped:
                raise RuntimeError("A survey session is already running.")

            session_id = str(uuid.uuid4())

            direction_config = load_direction_config(
                self._settings.camera_direction_config_path, camera_name=camera_name
            )
            direction_classifier = DirectionClassifier(direction_config.logic)
            direction_classifier.prepare_frame(self._settings.proc_width, self._settings.proc_height)
            zones_polygons = direction_classifier.get_zone_polygons()

            tracker = VehicleTracker(
                yolo_model_path=self._settings.yolo.model_path,
                conf=self._settings.yolo.conf,
                iou=self._settings.yolo.iou,
                tracker_cfg=self._settings.tracker_cfg,
                van_aspect_ratio_threshold=self._settings.van_aspect_ratio_threshold,
                van_min_area_pixels=self._settings.van_min_area_pixels,
            )

            session = SurveySession(
                session_id=session_id,
                camera_type=camera_type,
                camera_name=camera_name,
                created_at=utc_now(),
                tracker=tracker,
                direction_classifier=direction_classifier,
                sheets_logger=self._sheets_logger,
            )
            self._active_session = session

            zones_payload = []
            for name, poly in zones_polygons.items():
                points = [[int(x), int(y)] for x, y in poly.tolist()]
                zones_payload.append({"name": name, "points": points})

            return {
                "sessionId": session_id,
                "procWidth": self._settings.proc_width,
                "procHeight": self._settings.proc_height,
                "zones": zones_payload,
                "outsideZoneName": direction_config.logic.outside_zone_name,
                "cameraName": camera_name,
            }

    def stop_session(self, *, session_id: str) -> Dict[str, Any]:
        with self._lock:
            if self._active_session is None or self._active_session.session_id != session_id:
                raise RuntimeError("No active session to stop.")
            session = self._active_session
            session.stopped = True

        # Export CSV for the session from SQLite (offline backup).
        try:
            csv_path = self._export_session_csv(session_id)
        except Exception:
            logger.exception("Failed to export session CSV for session_id=%s", session_id)
            csv_path = ""

        # Compute summary from cached direction counts (in-memory) and cached events (SQLite).
        direction_counts = session.direction_classifier.get_logged_direction_counts()
        vehicle_type_counts: Dict[str, int] = {}
        try:
            conn = self._sheets_logger._conn  # type: ignore[attr-defined]
            cur = conn.execute("SELECT payload_json FROM events_cache WHERE status IN ('sent','pending')")
            for (payload_json,) in cur.fetchall():
                payload = json.loads(payload_json)
                if payload.get("session_id") != session_id:
                    continue
                vtype = str(payload.get("vehicle_type", "other vehicle"))
                vehicle_type_counts[vtype] = vehicle_type_counts.get(vtype, 0) + 1
        except Exception:
            logger.exception("Failed computing vehicle type counts for session_id=%s", session_id)

        try:
            self._sheets_logger.append_summary(
                timestamp_utc=utc_now(),
                session_id=session_id,
                session_duration_sec=(utc_now() - session.created_at).total_seconds(),
                total_logged=_sum_direction_counts(direction_counts),
                vehicle_type_counts=vehicle_type_counts,
                direction_counts=direction_counts,
            )
        except Exception:
            logger.exception("Failed to append summary to Google Sheets for session_id=%s", session_id)
        summary = {
            "sessionId": session_id,
            "totalLogged": _sum_direction_counts(direction_counts),
            "loggedCountsByDirection": direction_counts,
            "sessionDurationSec": round((utc_now() - session.created_at).total_seconds(), 2),
            "csvExportPath": csv_path,
        }

        with self._lock:
            if self._active_session is not None and self._active_session.session_id == session_id:
                self._active_session = None

        return summary

    def _export_session_csv(self, session_id: str) -> str:
        from pathlib import Path

        base_dir = Path(__file__).resolve().parents[1]
        exports_dir = base_dir / "exports"
        exports_dir.mkdir(parents=True, exist_ok=True)
        out_path = str(exports_dir / f"session_{session_id}.csv")
        # Query all cached events and filter by session_id inside payload_json.
        conn = self._sheets_logger._conn  # type: ignore[attr-defined]
        cur = conn.execute("SELECT payload_json FROM events_cache WHERE status IN ('sent','pending')")
        rows = cur.fetchall()
        events: List[Dict[str, Any]] = []
        for (payload_json,) in rows:
            payload = json.loads(payload_json)
            if payload.get("session_id") == session_id:
                events.append(payload)

        with open(out_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(self._sheets_logger.REQUIRED_COLUMNS)
            for e in events:
                # This payload contains datetime objects serialized as strings by logger.
                # We keep columns consistent with REQUIRED_COLUMNS order.
                writer.writerow(
                    [
                        e.get("timestamp_utc"),
                        e.get("session_id"),
                        e.get("track_id"),
                        e.get("vehicle_type"),
                        e.get("direction"),
                        e.get("confidence"),
                        e.get("camera_type"),
                        e.get("camera_name"),
                        e.get("entry_zone"),
                        e.get("exit_zone"),
                        e.get("frame_number"),
                        e.get("snapshot_path"),
                        e.get("event_id"),
                    ]
                )
        return out_path

    def get_active_session_id(self) -> Optional[str]:
        with self._lock:
            if self._active_session is None or self._active_session.stopped:
                return None
            return self._active_session.session_id

    def process_frame(
        self,
        *,
        session_id: str,
        frame_bgr,
        frame_number: int,
    ) -> Dict[str, Any]:
        with self._lock:
            if self._active_session is None or self._active_session.session_id != session_id or self._active_session.stopped:
                raise RuntimeError("Session not found or stopped.")
            session = self._active_session

        # Ensure consistent processing resolution.
        frame_bgr = cv2.resize(frame_bgr, (self._settings.proc_width, self._settings.proc_height))

        tracks = session.tracker.update(frame_bgr)
        fps = session.update_fps()

        active_counts_by_type: Dict[str, int] = {}
        for t in tracks:
            active_counts_by_type[t.vehicle_type] = active_counts_by_type.get(t.vehicle_type, 0) + 1

        direction_counts = session.direction_classifier.get_logged_direction_counts()

        session.frame_number = frame_number
        session.last_logged_event = None

        # Update direction state per track.
        for t in tracks:
            event = session.direction_classifier.update(t.track_id, t.centroid_xy)
            if event is None:
                session.track_last_seen_frame[t.track_id] = frame_number
                continue

            event_id = make_event_id(
                session_id=session.session_id,
                track_id=t.track_id,
                vehicle_type=t.vehicle_type,
                direction=event.direction,
                entry_zone=event.vehicle_entry_zone,
                exit_zone=event.vehicle_exit_zone,
            )

            # Blend detection confidence with direction confirmation confidence.
            confidence = float(0.7 * t.confidence + 0.3 * event.direction_confidence)

            # Persist + de-dup in logger.
            session.sheets_logger.append_event(
                frame_bgr=frame_bgr,
                bbox_xyxy=t.bbox_xyxy,
                event_id=event_id,
                timestamp_utc=utc_now(),
                session_id=session.session_id,
                track_id=t.track_id,
                vehicle_type=t.vehicle_type,
                direction=event.direction,
                confidence=confidence,
                camera_type=session.camera_type,
                camera_name=session.camera_name,
                entry_zone=event.vehicle_entry_zone,
                exit_zone=event.vehicle_exit_zone,
                frame_number=frame_number,
            )

            session.direction_by_track[t.track_id] = event.direction
            session.track_last_seen_frame[t.track_id] = frame_number
            session.last_logged_event = {
                "eventId": event_id,
                "trackId": t.track_id,
                "vehicleType": t.vehicle_type,
                "direction": event.direction,
                "confidence": confidence,
                "entryZone": event.vehicle_entry_zone,
                "exitZone": event.vehicle_exit_zone,
                "timestampUtc": session.created_at.isoformat(),
                "frameNumber": frame_number,
            }

        # Clean up direction labels for tracks that vanished.
        # TTL in frames: conservative to avoid flicker.
        ttl_frames = 25
        to_remove = []
        for track_id, last_seen in session.track_last_seen_frame.items():
            if frame_number - last_seen > ttl_frames:
                to_remove.append(track_id)
        for track_id in to_remove:
            session.direction_by_track.pop(track_id, None)
            session.track_last_seen_frame.pop(track_id, None)

        return {
            "type": "update",
            "frameNumber": frame_number,
            "fps": fps,
            "procWidth": self._settings.proc_width,
            "procHeight": self._settings.proc_height,
            "activeCountsByType": active_counts_by_type,
            "loggedCountsByDirection": direction_counts,
            "totalLogged": _sum_direction_counts(direction_counts),
            "tracks": [
                {
                    "trackId": t.track_id,
                    "bbox": [t.bbox_xyxy[0], t.bbox_xyxy[1], t.bbox_xyxy[2], t.bbox_xyxy[3]],
                    "vehicleType": t.vehicle_type,
                    "confidence": t.confidence,
                }
                for t in tracks
            ],
            "directionByTrack": {str(k): v for k, v in session.direction_by_track.items()},
            "lastLoggedEvent": session.last_logged_event,
            "session": {
                "status": "running",
                "elapsedSec": round((utc_now() - session.created_at).total_seconds(), 2),
            },
        }

