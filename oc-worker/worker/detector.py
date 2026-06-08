"""YOLO detection + Norfair tracking.

Pipeline:
  MD worker sends full frame when weighted-average motion detector fires.
  OC worker runs YOLO on the full frame for detections, then feeds them to
  Norfair which uses a Kalman filter + vectorized IoU distance function.

Distance function: "iou_opt" (string)
  Pass as string so Norfair resolves its own vectorized built-in — passing
  the imported function object bypasses Norfair's internal type check and
  silently falls back to the scalar path (triggering a WARNING). The string
  form uses the numpy-batch path (~10-50x faster than scalar).

  Threshold is IoU-based: 0.0 = perfect overlap, 1.0 = no overlap.
  distance_threshold=0.7 means associate if IoU > 0.3.
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
            distance_function="iou_opt",           # string — Norfair resolves vectorized built-in
            distance_threshold=s.tracker_distance_threshold,
            initialization_delay=s.tracker_initialization_delay,
            hit_counter_max=s.tracker_hit_counter_max,
            filter_factory=OptimizedKalmanFilterFactory(R=3.4),
        )
        log.info("norfair_tracker_created",
                 distance_function="iou_opt",
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
