import json
import logging
import os
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

import cv2
import gspread
from google.auth.exceptions import GoogleAuthError

from backend.config import SheetsSettings
from backend.utils import ensure_relative_snapshot_path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LoggedEvent:
    event_id: str
    timestamp_utc: datetime
    session_id: str
    track_id: int
    vehicle_type: str
    direction: str
    confidence: float
    camera_type: str
    camera_name: str
    entry_zone: str
    exit_zone: str
    frame_number: int
    snapshot_path: str


class GoogleSheetsLogger:
    """
    Append confirmed survey events to Google Sheets.

    Behavior:
    - Primary destination is Google Sheets when enabled.
    - If Sheets fails (auth/network), events are cached to SQLite for later replay.
    - De-duplication is based on `event_id`.
    """

    REQUIRED_COLUMNS = [
        "Timestamp",
        "Session ID",
        "Track ID",
        "Vehicle Type",
        "Direction",
        "Confidence",
        "Camera Type",
        "Camera Name",
        "Entry Zone",
        "Exit Zone",
        "Frame Number",
        "Snapshot Path",
        "Event ID",
    ]

    def __init__(
        self,
        settings: SheetsSettings,
        sqlite_path: str,
        snapshot_dir: str,
        snapshot_jpeg_quality: int,
    ):
        self._settings = settings
        self._sqlite_path = sqlite_path
        self._snapshot_dir = snapshot_dir
        self._snapshot_jpeg_quality = int(snapshot_jpeg_quality)

        self._enabled = bool(settings.enabled)
        self._in_memory_event_ids: set[str] = set()

        self._lock = threading.Lock()
        # Ensure parent directory exists for SQLite database (critical for Render deployment).
        os.makedirs(os.path.dirname(self._sqlite_path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(self._sqlite_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS events_cache (
                event_id TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

        self._gc: Optional[gspread.Client] = None
        self._sheet: Optional[gspread.Spreadsheet] = None
        self._raw_ws: Optional[gspread.Worksheet] = None
        self._summary_ws: Optional[gspread.Worksheet] = None

        if self._enabled:
            self._init_sheets()

    def _init_sheets(self) -> None:
        if not self._settings.spreadsheet_id:
            logger.warning("GOOGLE_SHEETS_SPREADSHEET_ID missing; disabling Sheets logging.")
            self._enabled = False
            return
        service_account_path = self._settings.service_account_json
        if os.path.exists(service_account_path):
            try:
                self._gc = gspread.service_account(filename=service_account_path)
            except Exception as e:
                logger.exception("Failed to authenticate with service account file: %s", e)
                self._enabled = False
                return
        else:
            # Assume it's JSON content from env var
            try:
                creds_dict = json.loads(service_account_path)
                self._gc = gspread.service_account_from_dict(creds_dict)
            except (json.JSONDecodeError, Exception) as e:
                logger.exception("Service account json not found at %s and not valid JSON: %s; disabling Sheets logging.", service_account_path, e)
                self._enabled = False
                return

        try:
            self._sheet = self._gc.open_by_key(self._settings.spreadsheet_id)
            self._raw_ws = self._open_or_create_worksheet(self._settings.raw_sheet_name)
            self._summary_ws = None
            if self._settings.summary_sheet_name:
                try:
                    self._summary_ws = self._open_or_create_worksheet(self._settings.summary_sheet_name)
                except Exception:
                    logger.exception("Could not open summary sheet; continuing without it.")

            self._ensure_headers()
        except (GoogleAuthError, gspread.exceptions.APIError, FileNotFoundError):
            logger.exception("Google Sheets auth/init failed; caching events to SQLite.")
            self._enabled = False

    def _open_or_create_worksheet(self, sheet_name: str) -> gspread.Worksheet:
        assert self._sheet is not None
        try:
            return self._sheet.worksheet(sheet_name)
        except gspread.exceptions.WorksheetNotFound:
            return self._sheet.add_worksheet(title=sheet_name, rows=3000, cols=20)

    def _ensure_headers(self) -> None:
        if self._raw_ws is None:
            return
        values = self._raw_ws.get_all_values()
        if not values:
            self._raw_ws.append_row(self.REQUIRED_COLUMNS, value_input_option="USER_ENTERED")
            return
        header = values[0]
        if header[: len(self.REQUIRED_COLUMNS)] != self.REQUIRED_COLUMNS:
            # Append a fresh header row rather than rewriting to avoid permission issues.
            self._raw_ws.append_row(self.REQUIRED_COLUMNS, value_input_option="USER_ENTERED")

        self._ensure_summary_headers()

    def _ensure_summary_headers(self) -> None:
        if self._summary_ws is None:
            return
        values = self._summary_ws.get_all_values()
        summary_columns = [
            "Timestamp",
            "Session ID",
            "Session Duration Sec",
            "Total Logged",
            "Vehicle Type Counts (JSON)",
            "Direction Counts (JSON)",
        ]
        if not values:
            self._summary_ws.append_row(summary_columns, value_input_option="USER_ENTERED")
            return
        header = values[0]
        if header[: len(summary_columns)] != summary_columns:
            # Append a fresh header row rather than rewriting.
            self._summary_ws.append_row(summary_columns, value_input_option="USER_ENTERED")

    def _save_snapshot_crop(
        self,
        frame_bgr,
        bbox_xyxy: Tuple[float, float, float, float],
    ) -> str:
        x1, y1, x2, y2 = bbox_xyxy
        h, w = frame_bgr.shape[:2]
        x1i = max(0, min(w - 1, int(x1)))
        y1i = max(0, min(h - 1, int(y1)))
        x2i = max(0, min(w, int(x2)))
        y2i = max(0, min(h, int(y2)))
        if x2i <= x1i or y2i <= y1i:
            return ""
        crop = frame_bgr[y1i:y2i, x1i:x2i]

        os.makedirs(self._snapshot_dir, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"snapshot_{ts}.jpg"
        path = os.path.join(self._snapshot_dir, filename)
        params = [int(cv2.IMWRITE_JPEG_QUALITY), self._snapshot_jpeg_quality]
        ok = cv2.imwrite(path, crop, params)
        if not ok:
            return ""
        return ensure_relative_snapshot_path(path)

    def _append_row_to_sheet(self, event: LoggedEvent) -> None:
        assert self._raw_ws is not None
        timestamp_str = event.timestamp_utc.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        row = [
            timestamp_str,
            event.session_id,
            str(event.track_id),
            event.vehicle_type,
            event.direction,
            f"{event.confidence:.4f}",
            event.camera_type,
            event.camera_name,
            event.entry_zone,
            event.exit_zone,
            str(event.frame_number),
            event.snapshot_path,
            event.event_id,
        ]
        self._raw_ws.append_row(row, value_input_option="USER_ENTERED")

    def _cache_event_to_sqlite(self, event: LoggedEvent, status: str = "pending") -> None:
        import json as _json

        payload = _json.dumps(event.__dict__, default=str)
        created_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO events_cache(event_id, payload_json, status, created_at) VALUES(?, ?, ?, ?)",
                (event.event_id, payload, status, created_at),
            )
            self._conn.commit()

    def _flush_pending(self, max_to_send: int = 10) -> None:
        if not self._enabled or self._raw_ws is None:
            return
        with self._lock:
            cur = self._conn.execute("SELECT event_id, payload_json FROM events_cache WHERE status='pending' LIMIT ?", (max_to_send,))
            rows = cur.fetchall()
        if not rows:
            return

        import json as _json

        for event_id, payload_json in rows:
            try:
                payload = _json.loads(payload_json)
                event = LoggedEvent(
                    event_id=payload["event_id"],
                    timestamp_utc=datetime.fromisoformat(payload["timestamp_utc"].replace("Z", "+00:00")),
                    session_id=payload["session_id"],
                    track_id=int(payload["track_id"]),
                    vehicle_type=str(payload["vehicle_type"]),
                    direction=str(payload["direction"]),
                    confidence=float(payload["confidence"]),
                    camera_type=str(payload["camera_type"]),
                    camera_name=str(payload["camera_name"]),
                    entry_zone=str(payload["entry_zone"]),
                    exit_zone=str(payload["exit_zone"]),
                    frame_number=int(payload["frame_number"]),
                    snapshot_path=str(payload["snapshot_path"]),
                )
                self._append_row_to_sheet(event)
                with self._lock:
                    self._conn.execute("UPDATE events_cache SET status='sent' WHERE event_id=?", (event_id,))
                    self._conn.commit()
            except Exception:
                logger.exception("Failed to flush pending event_id=%s", event_id)
                # Stop flushing to avoid repeated failures.
                break

    def append_event(
        self,
        *,
        frame_bgr,
        bbox_xyxy: Tuple[float, float, float, float],
        event_id: str,
        timestamp_utc: datetime,
        session_id: str,
        track_id: int,
        vehicle_type: str,
        direction: str,
        confidence: float,
        camera_type: str,
        camera_name: str,
        entry_zone: str,
        exit_zone: str,
        frame_number: int,
    ) -> bool:
        if event_id in self._in_memory_event_ids:
            return False

        snapshot_path = self._save_snapshot_crop(frame_bgr, bbox_xyxy)
        event = LoggedEvent(
            event_id=event_id,
            timestamp_utc=timestamp_utc,
            session_id=session_id,
            track_id=track_id,
            vehicle_type=vehicle_type,
            direction=direction,
            confidence=float(confidence),
            camera_type=camera_type,
            camera_name=camera_name,
            entry_zone=entry_zone,
            exit_zone=exit_zone,
            frame_number=frame_number,
            snapshot_path=snapshot_path,
        )

        try:
            # Always cache locally for offline export.
            self._cache_event_to_sqlite(event, status="pending" if not (self._enabled and self._raw_ws is not None) else "sent")

            if self._enabled and self._raw_ws is not None:
                self._append_row_to_sheet(event)
                self._in_memory_event_ids.add(event_id)
                # Try to flush a few cached events opportunistically.
                self._flush_pending(max_to_send=5)
                with self._lock:
                    self._conn.execute("UPDATE events_cache SET status='sent' WHERE event_id=?", (event_id,))
                    self._conn.commit()
                return True
            else:
                self._in_memory_event_ids.add(event_id)
                return False
        except Exception:
            logger.exception("Sheets append failed; caching event_id=%s", event_id)
            self._cache_event_to_sqlite(event, status="pending")
            self._in_memory_event_ids.add(event_id)
            # Disable Sheets temporarily for safety.
            self._enabled = False
            return False

    def append_summary(
        self,
        *,
        timestamp_utc: datetime,
        session_id: str,
        session_duration_sec: float,
        total_logged: int,
        vehicle_type_counts: Dict[str, int],
        direction_counts: Dict[str, int],
    ) -> None:
        if not self._enabled or self._summary_ws is None:
            return
        import json as _json

        try:
            row = [
                timestamp_utc.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
                session_id,
                str(session_duration_sec),
                str(total_logged),
                _json.dumps(vehicle_type_counts, ensure_ascii=False),
                _json.dumps(direction_counts, ensure_ascii=False),
            ]
            self._summary_ws.append_row(row, value_input_option="USER_ENTERED")
        except Exception:
            logger.exception("Failed to append summary counts to Google Sheets.")

