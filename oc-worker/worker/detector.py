"""YOLO + BoT-SORT tracking on full frames.

Pipeline:
  MD worker sends full frame when MOG2 detects motion.
  OC worker runs model.track() on the full frame — YOLO detects objects and
  BoT-SORT assigns consistent track IDs across frames within a job.

Advantages over the previous crop-based approach:
  - Full frame context → better detection accuracy
  - BoT-SORT appearance features → consistent IDs across gaps/occlusions
  - Single model call per frame instead of one call per MOG2 region
"""
import numpy as np
import structlog
from ultralytics import YOLO

from worker.config import get_settings

log = structlog.get_logger()

_model: YOLO | None = None
_allowed_class_ids: list[int] | None = None


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


def reset_tracker():
    """Reset BoT-SORT state between jobs so track IDs restart cleanly."""
    model = get_model()
    try:
        if hasattr(model, 'predictor') and model.predictor is not None:
            if hasattr(model.predictor, 'trackers') and model.predictor.trackers:
                model.predictor.trackers[0].reset()
    except Exception:
        pass


def track_full_frame(
    full_frame: np.ndarray,
    video_fps: float = 30.0,
) -> list[dict]:
    """
    Run YOLO + BoT-SORT on a full frame.
    Returns list of dicts: {track_id, class_label, confidence, bbox}
    where bbox is {x, y, w, h} in full-frame pixel coordinates.
    """
    s = get_settings()
    model = get_model()

    results = model.track(
        full_frame,
        persist=True,
        tracker=s.tracker_config,
        conf=s.oc_confidence_threshold,
        iou=s.oc_iou_threshold,
        classes=_allowed_class_ids,
        verbose=False,
    )

    if not results or results[0].boxes is None:
        return []

    boxes = results[0].boxes
    if boxes.id is None:
        return []

    detections = []
    for i in range(len(boxes)):
        track_id  = int(boxes.id[i])
        class_id  = int(boxes.cls[i])
        conf      = float(boxes.conf[i])
        x1, y1, x2, y2 = boxes.xyxy[i].tolist()
        detections.append({
            "track_id":    track_id,
            "class_label": model.names[class_id],
            "confidence":  conf,
            "bbox": {
                "x": int(x1),
                "y": int(y1),
                "w": int(x2 - x1),
                "h": int(y2 - y1),
            },
        })

    return detections
