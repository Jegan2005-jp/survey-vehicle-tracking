from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import numpy as np

from backend.config import DirectionLogicConfig
from backend.utils import utc_now


@dataclass(frozen=True)
class DirectionEvent:
    vehicle_entry_zone: str
    vehicle_exit_zone: str
    direction: str
    direction_confidence: float


@dataclass
class _TrackZoneState:
    confirmed_zone: Optional[str] = None
    confirmed_depth: float = 0.0

    pending_zone: Optional[str] = None
    pending_depth: float = 0.0
    pending_frames: int = 0


class DirectionClassifier:
    """
    Direction by configured zone transitions.

    Practical behavior:
    - A track must be inside a zone for `zone_enter_confirm_frames` consecutive frames
      before a zone transition is confirmed.
    - When a confirmed zone changes (entry -> exit), we emit exactly one event
      according to `direction_rules`.
    """

    def __init__(self, direction_logic: DirectionLogicConfig):
        self._logic = direction_logic
        self._track_states: Dict[int, _TrackZoneState] = {}
        self._logged_event_keys: set[str] = set()

        self._zones_polygons_xy: Optional[Tuple[Tuple[str, np.ndarray], ...]] = None
        self._frame_max_dim: Optional[int] = None

    def prepare_frame(self, frame_width: int, frame_height: int) -> None:
        if self._zones_polygons_xy is not None:
            return

        self._frame_max_dim = max(frame_width, frame_height)
        zones_polygons = []
        # Compute pixel polygons from normalized coordinates.
        for z in self._logic.zones:
            pts = []
            for xn, yn in z.points_normalized:
                pts.append([int(round(xn * frame_width)), int(round(yn * frame_height))])
            poly = np.array(pts, dtype=np.int32)
            zones_polygons.append((z.name, poly))

        self._zones_polygons_xy = tuple(zones_polygons)

    def get_zone_polygons(self) -> Dict[str, np.ndarray]:
        """
        Get zone polygons in pixel coordinates for drawing.

        Returns:
          Mapping of zone name -> Nx2 array (int32).
        """
        if self._zones_polygons_xy is None:
            return {}
        return {name: poly for name, poly in self._zones_polygons_xy}

    def _point_in_polygon(self, point_xy: Tuple[float, float], poly: np.ndarray) -> Tuple[bool, float]:
        # OpenCV pointPolygonTest distance to nearest edge.
        import cv2

        px, py = float(point_xy[0]), float(point_xy[1])
        res = cv2.pointPolygonTest(poly, (px, py), measureDist=True)
        inside = res >= 0
        return inside, float(res)

    def _best_zone_for_point(self, point_xy: Tuple[float, float]) -> Tuple[Optional[str], float]:
        assert self._zones_polygons_xy is not None
        best_zone: Optional[str] = None
        best_dist = -1e9
        for name, poly in self._zones_polygons_xy:
            inside, dist = self._point_in_polygon(point_xy, poly)
            if inside and dist > best_dist:
                best_zone = name
                best_dist = dist
        return best_zone, best_dist

    def _compute_direction_confidence(self, entry_depth: float, exit_depth: float) -> float:
        if self._frame_max_dim is None or self._frame_max_dim <= 0:
            return 0.5
        denom = 0.35 * self._frame_max_dim
        raw = (entry_depth + exit_depth) / (2.0 * denom + 1e-6)
        return float(max(0.0, min(1.0, raw)))

    def update(self, track_id: int, centroid_xy: Tuple[float, float]) -> Optional[DirectionEvent]:
        """
        Update zone state for one track and emit a DirectionEvent when a configured transition occurs.
        """
        if self._zones_polygons_xy is None:
            raise RuntimeError("DirectionClassifier.prepare_frame() must be called first.")

        state = self._track_states.get(track_id)
        if state is None:
            state = _TrackZoneState()
            self._track_states[track_id] = state

        raw_zone, raw_depth = self._best_zone_for_point(centroid_xy)

        # Stable detection via "confirm consecutive frames" for zone transitions.
        if raw_zone == state.confirmed_zone:
            state.pending_zone = raw_zone
            state.pending_depth = raw_depth
            state.pending_frames = 0
            return None

        if raw_zone != state.pending_zone:
            state.pending_zone = raw_zone
            state.pending_depth = raw_depth
            state.pending_frames = 1
        else:
            state.pending_frames += 1
            state.pending_depth = max(state.pending_depth, raw_depth)

        required = self._logic.zone_enter_confirm_frames if raw_zone is not None else self._logic.zone_exit_confirm_frames
        if state.pending_frames < required:
            return None

        # Confirm transition.
        old_zone = state.confirmed_zone
        new_zone = state.pending_zone
        entry_depth = state.confirmed_depth
        exit_depth = state.pending_depth

        state.confirmed_zone = new_zone
        state.confirmed_depth = exit_depth
        state.pending_frames = 0

        if old_zone is None:
            # Allow logging entry from outside
            pass

        exit_zone = new_zone if new_zone is not None else self._logic.outside_zone_name

        for rule in self._logic.direction_rules:
            if rule.entry_zone == old_zone and rule.exit_zone == exit_zone:
                key = f"{track_id}:{rule.entry_zone}->{rule.exit_zone}:{rule.direction}"
                if key in self._logged_event_keys:
                    return None
                self._logged_event_keys.add(key)
                direction_conf = self._compute_direction_confidence(entry_depth, exit_depth)
                return DirectionEvent(
                    vehicle_entry_zone=rule.entry_zone,
                    vehicle_exit_zone=exit_zone,
                    direction=rule.direction,
                    direction_confidence=direction_conf,
                )

        return None

    def get_logged_direction_counts(self) -> Dict[str, int]:
        """
        Counts of logged directions during this session.

        We infer counts from the unique transition keys we already store.
        """
        counts: Dict[str, int] = {}
        for key in self._logged_event_keys:
            # key format: "{track_id}:{entry_zone}->{exit_zone}:{direction}"
            direction = key.split(":")[-1]
            counts[direction] = counts.get(direction, 0) + 1
        return counts

