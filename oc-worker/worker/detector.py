"""YOLO detection + Norfair tracking with Frigate's scale-normalized distance function.

Pipeline:
  MD worker sends full frame when weighted-average motion detector fires.
  OC worker runs YOLO on the full frame for detections, then feeds them to
  Norfair which uses a Kalman filter + Frigate's custom distance metric.

Why Norfair over BoT-SORT:
  Frigate's distance function normalizes position change by the object's
  own bounding-box size.  A car moving 200px looks the same as a pedestrian
  moving 40px relative to their respective sizes.  This dramatically reduces
  ID switches and track fragmentation on security camera footage where
  objects vary widely in apparent size and speed.
"""
import numpy as np
import structlog
from ultralytics import YOLO
from norfair import Detection, Tracker
from norfair.filter import OptimizedKalmanFilterFactory

from worker.config import get_settings

log = structlog.get_logger()

_model: YOLO | None = None
_tracker: Tracker | None = None
_allowed_class_ids: list[int] | None = None


# ---------------------------------------------------------------------------
# Frigate-style scale-normalized distance
# ---------------------------------------------------------------------------

def _frigate_distance_raw(detection_pts: np.ndarray, estimate_pts: np.ndarray) -> float:
    """
    Measure how far a detection is from a tracker estimate, normalised by the
    estimated object's own dimensions.  Matches Frigate's norfair_tracker.py.

    detection_pts / estimate_pts: shape (2, 2) — [[x1,y1],[x2,y2]]
    Returns: euclidean norm of a 4-component change vector
    """
    estimate_dim   = np.diff(estimate_pts, axis=0).flatten()   # [w, h]
    detection_dim  = np.diff(detection_pts, axis=0).flatten()  # [w, h]

    # Bottom-centre as the positional anchor
    detection_pos = np.array([
        np.average(detection_pts[:, 0]),
        np.max(detection_pts[:, 1]),
    ])
    estimate_pos = np.array([
        np.average(estimate_pts[:, 0]),
        np.max(estimate_pts[:, 1]),
    ])

    # Position delta normalised by estimated width/height
    pos_delta = (detection_pos - estimate_pos).astype(float)
    # Guard against zero-size estimates
    if estimate_dim[0] > 0:
        pos_delta[0] /= estimate_dim[0]
    if estimate_dim[1] > 0:
        pos_delta[1] /= estimate_dim[1]

    # Size ratio change (1.0 = same size, 0.0 = identical)
    widths  = np.sort([abs(estimate_dim[0]), abs(detection_dim[0])])
    heights = np.sort([abs(estimate_dim[1]), abs(detection_dim[1])])
    width_ratio  = (widths[1]  / widths[0]  - 1.0) if widths[0]  > 0 else 0.0
    height_ratio = (heights[1] / heights[0] - 1.0) if heights[0] > 0 else 0.0

    change = np.array([pos_delta[0], pos_delta[1], width_ratio, height_ratio])
    return float(np.linalg.norm(change))


def _norfair_distance(detection: Detection, tracked_object) -> float:
    return _frigate_distance_raw(detection.points, tracked_object.estimate)


# ---------------------------------------------------------------------------
# Model + tracker singletons
# ---------------------------------------------------------------------------

def get_model() -> YOLO:
    global _model, _allowed_class_ids
    if _model is None:
        s = get_settings()
        log.info("yolo_model_loading", model=s.yolo_model_path)
        _model = YOLO(s.yolo_model_path)
        device = "cuda:0" if s.oc_use_gpu else "cpu"
        _model.to(device)

        allowed_names = {c.strip().lower() for c in s.oc_allowed_classes.split(",") if c.strip()}
        if allowed_names:
            _allowed_class_ids = [
                cid for cid, name in _model.names.items()
                if name.lower() in allowed_names
            ]
            log.info("yolo_class_filter_active",
                     allowed_ids=_allowed_class_ids,
                     allowed_names=sorted(allowed_names))
        else:
            _allowed_class_ids = None

        log.info("yolo_model_ready", device=device)
    return _model


def _get_tracker() -> Tracker:
    global _tracker
    if _tracker is None:
        s = get_settings()
        _tracker = Tracker(
            distance_function=_norfair_distance,
            distance_threshold=s.tracker_distance_threshold,
            initialization_delay=s.tracker_initialization_delay,
            hit_counter_max=s.tracker_hit_counter_max,
            filter_factory=OptimizedKalmanFilterFactory(R=3.4),
        )
        log.info("norfair_tracker_created",
                 distance_threshold=s.tracker_distance_threshold,
                 hit_counter_max=s.tracker_hit_counter_max)
    return _tracker


def reset_tracker():
    """Reset Norfair tracker between jobs so track IDs restart cleanly."""
    global _tracker
    _tracker = None
    log.info("norfair_tracker_reset")


# ---------------------------------------------------------------------------
# Per-frame tracking
# ---------------------------------------------------------------------------

def track_full_frame(
    full_frame: np.ndarray,
    video_fps: float = 30.0,
    frame_index: int = 0,
) -> list[dict]:
    """
    Run YOLO detection on a full frame, feed detections to Norfair tracker.
    Returns list of {track_id, class_label, confidence, bbox} — only for tracks
    that had a live YOLO detection this frame (not Kalman-predicted positions).
    bbox is {x, y, w, h} in full-frame pixel coordinates.
    """
    s = get_settings()
    model = get_model()
    tracker = _get_tracker()

    # YOLO inference — detect only, no built-in tracking
    results = model(
        full_frame,
        conf=s.oc_confidence_threshold,
        iou=s.oc_iou_threshold,
        classes=_allowed_class_ids,
        verbose=False,
    )

    if not results or results[0].boxes is None or len(results[0].boxes) == 0:
        # Tick the tracker with no detections so Kalman state advances;
        # don't emit anything — no real detection happened.
        tracker.update(detections=[])
        return []

    boxes = results[0].boxes
    norfair_detections = []

    for i in range(len(boxes)):
        x1, y1, x2, y2 = boxes.xyxy[i].tolist()
        class_id = int(boxes.cls[i])
        conf     = float(boxes.conf[i])
        label    = model.names[class_id]

        points = np.array([[x1, y1], [x2, y2]])
        det = Detection(
            points=points,
            label=label,
            data={"confidence": conf, "frame_index": frame_index},
        )
        norfair_detections.append(det)

    tracked = tracker.update(detections=norfair_detections)
    return _extract_active(tracked, frame_index)


def _extract_active(tracked_objects, frame_index: int) -> list[dict]:
    """
    Convert Norfair tracked objects to detection dicts.
    Only returns tracks that were matched to a live detection this frame —
    ignores tracks being kept alive by Kalman prediction only.
    """
    detections = []
    for t in tracked_objects:
        if t.last_detection is None:
            continue
        # Skip tracks whose last real detection was from a previous frame
        if t.last_detection.data.get("frame_index") != frame_index:
            continue
        pts = t.estimate.flatten()
        x1, y1, x2, y2 = int(pts[0]), int(pts[1]), int(pts[2]), int(pts[3])
        w = max(1, x2 - x1)
        h = max(1, y2 - y1)
        detections.append({
            "track_id":    int(t.global_id),
            "class_label": t.last_detection.label,
            "confidence":  t.last_detection.data.get("confidence", 0.0),
            "bbox": {"x": x1, "y": y1, "w": w, "h": h},
        })
    return detections
