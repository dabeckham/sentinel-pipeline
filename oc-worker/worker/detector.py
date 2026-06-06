"""YOLO inference + ByteTrack tracking across frames, keyed by job_id."""
import numpy as np
import structlog
from ultralytics import YOLO
import supervision as sv

from worker.config import get_settings

log = structlog.get_logger()

_model: YOLO | None = None


def get_model() -> YOLO:
    global _model
    if _model is None:
        s = get_settings()
        log.info("yolo_model_loading", model=s.yolo_model_path)
        _model = YOLO(s.yolo_model_path)
        device = "cuda:0" if s.oc_use_gpu else "cpu"
        _model.to(device)
        log.info("yolo_model_ready", device=device)
    return _model


# Per-job ByteTrack state — keyed by job_id
_trackers: dict[int, sv.ByteTrack] = {}


def get_tracker(job_id: int) -> sv.ByteTrack:
    if job_id not in _trackers:
        _trackers[job_id] = sv.ByteTrack()
    return _trackers[job_id]


def release_tracker(job_id: int):
    _trackers.pop(job_id, None)


def classify_crop(crop: np.ndarray) -> tuple[str, float]:
    """Run YOLO on a single crop. Returns (class_label, confidence)."""
    s = get_settings()
    model = get_model()
    results = model(crop, verbose=False, conf=s.oc_confidence_threshold)
    if not results or len(results[0].boxes) == 0:
        return "unknown", 0.0
    boxes = results[0].boxes
    best_idx = int(boxes.conf.argmax())
    class_id = int(boxes.cls[best_idx])
    confidence = float(boxes.conf[best_idx])
    class_label = model.names[class_id]
    return class_label, confidence


def track_frame(
    job_id: int,
    bboxes: list[dict],
    crops: list[np.ndarray],
) -> list[dict]:
    """
    Classify each crop, feed all detections to ByteTrack, return per-detection
    results with assigned track_id.

    Returns list of dicts: {track_id, class_label, confidence, bbox, crop_idx}
    """
    s = get_settings()

    class_labels = []
    confidences = []
    for crop in crops:
        label, conf = classify_crop(crop)
        class_labels.append(label)
        confidences.append(conf)

    # Filter out low-confidence / unknown detections before tracking
    valid = [
        i for i, (label, conf) in enumerate(zip(class_labels, confidences))
        if label != "unknown" and conf >= s.oc_confidence_threshold
    ]
    if not valid:
        return []

    xyxy = np.array([
        [bboxes[i]["x"], bboxes[i]["y"],
         bboxes[i]["x"] + bboxes[i]["w"], bboxes[i]["y"] + bboxes[i]["h"]]
        for i in valid
    ], dtype=float)

    conf_arr = np.array([confidences[i] for i in valid], dtype=float)

    # Map class label to integer id using model's name dict
    model = get_model()
    name_to_id = {v: k for k, v in model.names.items()}
    class_id_arr = np.array(
        [name_to_id.get(class_labels[i], 0) for i in valid], dtype=int
    )

    detections = sv.Detections(xyxy=xyxy, confidence=conf_arr, class_id=class_id_arr)
    tracker = get_tracker(job_id)
    tracked = tracker.update_with_detections(detections)

    if tracked.tracker_id is None or len(tracked) == 0:
        return []

    results = []
    for j in range(len(tracked)):
        # ByteTrack may return fewer items than input; map by index up to bounds
        orig_idx = valid[j] if j < len(valid) else valid[-1]
        results.append({
            "track_id": int(tracked.tracker_id[j]),
            "class_label": class_labels[orig_idx],
            "confidence": confidences[orig_idx],
            "bbox": bboxes[orig_idx],
            "crop_idx": orig_idx,
        })
    return results
