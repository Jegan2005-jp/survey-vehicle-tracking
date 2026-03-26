import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from dotenv import load_dotenv


def _to_bool(value: Optional[str], default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "t", "yes", "y", "on"}


def _parse_camera_source(value: str) -> Union[int, str]:
    v = value.strip()
    if v.isdigit():
        return int(v)
    return v


@dataclass(frozen=True)
class ZoneConfig:
    name: str
    points_normalized: Tuple[Tuple[float, float], ...]


@dataclass(frozen=True)
class DirectionRule:
    entry_zone: str
    exit_zone: str
    direction: str


@dataclass(frozen=True)
class DirectionLogicConfig:
    mode: str
    outside_zone_name: str
    zone_enter_confirm_frames: int
    zone_exit_confirm_frames: int
    zones: Tuple[ZoneConfig, ...]
    direction_rules: Tuple[DirectionRule, ...]


@dataclass(frozen=True)
class DirectionConfig:
    camera_name: str
    logic: DirectionLogicConfig


@dataclass(frozen=True)
class YoloSettings:
    model_path: str
    conf: float
    iou: float


@dataclass(frozen=True)
class SheetsSettings:
    enabled: bool
    service_account_json: str
    spreadsheet_id: str
    raw_sheet_name: str
    summary_sheet_name: Optional[str]


@dataclass(frozen=True)
class AppSettings:
    log_level: str
    host: str
    port: int

    proc_width: int
    proc_height: int

    yolo: YoloSettings

    tracker_cfg: str

    camera_direction_config_path: str
    camera_name: str

    sqlite_path: str

    snapshot_dir: str
    snapshot_jpeg_quality: int

    van_aspect_ratio_threshold: float
    van_min_area_pixels: int

    sheets: SheetsSettings


def _load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _build_direction_logic(camera_direction_json: Dict[str, Any]) -> DirectionLogicConfig:
    mode = camera_direction_json.get("mode", "zones")
    outside_zone_name = camera_direction_json.get("outside_zone_name", "OUTSIDE")
    zone_enter_confirm_frames = int(camera_direction_json.get("zone_enter_confirm_frames", 2))
    zone_exit_confirm_frames = int(camera_direction_json.get("zone_exit_confirm_frames", 1))

    zones: List[ZoneConfig] = []
    for z in camera_direction_json.get("zones", []):
        name = str(z["name"])
        pts = z.get("points_normalized")
        if pts is None:
            raise ValueError(f"Zone '{name}' must have points_normalized")
        points_normalized = tuple((float(x), float(y)) for x, y in pts)
        zones.append(ZoneConfig(name=name, points_normalized=points_normalized))

    direction_rules: List[DirectionRule] = []
    for r in camera_direction_json.get("direction_rules", []):
        direction_rules.append(
            DirectionRule(
                entry_zone=str(r["entry_zone"]),
                exit_zone=str(r["exit_zone"]),
                direction=str(r["direction"]),
            )
        )

    return DirectionLogicConfig(
        mode=mode,
        outside_zone_name=str(outside_zone_name),
        zone_enter_confirm_frames=zone_enter_confirm_frames,
        zone_exit_confirm_frames=zone_exit_confirm_frames,
        zones=tuple(zones),
        direction_rules=tuple(direction_rules),
    )


def load_direction_config(config_path: str, camera_name: str) -> DirectionConfig:
    data = _load_json(config_path)
    cameras = data.get("cameras", [])
    for cam in cameras:
        if cam.get("name") == camera_name:
            logic_json = cam.get("direction_logic", {})
            logic = _build_direction_logic(logic_json)
            return DirectionConfig(camera_name=camera_name, logic=logic)
    raise ValueError(f"Camera '{camera_name}' not found in {config_path}")


def load_settings() -> AppSettings:
    load_dotenv()
    base_dir = Path(__file__).resolve().parents[1]

    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    host = os.getenv("HOST", "0.0.0.0").strip()
    port = int(os.getenv("PORT", "8000"))

    proc_width = int(os.getenv("PROC_WIDTH", "640"))
    proc_height = int(os.getenv("PROC_HEIGHT", "360"))

    model_path = os.getenv("YOLO_MODEL", "yolov8n.pt")
    yolo_conf = float(os.getenv("YOLO_CONF", "0.40"))
    yolo_iou = float(os.getenv("YOLO_IOU", "0.50"))
    yolo = YoloSettings(model_path=model_path, conf=yolo_conf, iou=yolo_iou)

    tracker_cfg = os.getenv("TRACKER_CFG", "bytetrack.yaml").strip()

    camera_name = os.getenv("CAMERA_NAME", "FrontCam")
    camera_direction_config_path = os.getenv(
        "CAMERA_DIRECTION_CONFIG_PATH", str(base_dir / "config" / "cameras.example.json")
    )
    if not Path(camera_direction_config_path).is_absolute():
        camera_direction_config_path = str(base_dir / camera_direction_config_path)

    snapshot_dir = os.getenv("SNAPSHOT_DIR", str(base_dir / "logs" / "snapshots"))
    snapshot_jpeg_quality = int(os.getenv("SNAPSHOT_JPEG_QUALITY", "90"))

    sqlite_path = os.getenv("SQLITE_PATH", str(base_dir / "logs" / "events_cache.db"))
    if not Path(sqlite_path).is_absolute():
        sqlite_path = str(base_dir / sqlite_path)

    if not Path(snapshot_dir).is_absolute():
        snapshot_dir = str(base_dir / snapshot_dir)

    van_aspect_ratio_threshold = float(os.getenv("VAN_ASPECT_RATIO_THRESHOLD", "1.80"))
    van_min_area_pixels = int(os.getenv("VAN_MIN_AREA_PIXELS", "20000"))

    sheets_enabled = _to_bool(os.getenv("GOOGLE_SHEETS_ENABLED"), True)
    service_account_json = os.getenv(
        "GOOGLE_SERVICE_ACCOUNT_JSON", str(base_dir / "secrets" / "service_account.json")
    )
    if not Path(service_account_json).is_absolute():
        service_account_json = str(base_dir / service_account_json)
    spreadsheet_id = os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID", "")
    raw_sheet_name = os.getenv("GOOGLE_SHEETS_RAW_SHEET_NAME", "RawLogs")
    summary_sheet_name = os.getenv("GOOGLE_SHEETS_SUMMARY_SHEET_NAME", "SummaryCounts")
    sheets = SheetsSettings(
        enabled=sheets_enabled,
        service_account_json=service_account_json,
        spreadsheet_id=spreadsheet_id,
        raw_sheet_name=raw_sheet_name,
        summary_sheet_name=summary_sheet_name or None,
    )

    return AppSettings(
        log_level=log_level,
        host=host,
        port=port,
        proc_width=proc_width,
        proc_height=proc_height,
        yolo=yolo,
        tracker_cfg=tracker_cfg,
        camera_direction_config_path=camera_direction_config_path,
        camera_name=camera_name,
        sqlite_path=sqlite_path,
        snapshot_dir=snapshot_dir,
        snapshot_jpeg_quality=snapshot_jpeg_quality,
        van_aspect_ratio_threshold=van_aspect_ratio_threshold,
        van_min_area_pixels=van_min_area_pixels,
        sheets=sheets,
    )


def setup_logging(log_level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def ensure_dir(path: str) -> None:
    Path(path).mkdir(parents=True, exist_ok=True)

