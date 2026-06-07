"""YOLO inference + ByteTrack tracking across frames, keyed by job_id.

Pipeline order (correct):
  MOG2 candidate regions → YOLO classify+detect within crop → ByteTrack on YOLO bboxes

ByteTrack receives YOLO-detected bounding boxes (translated to full-frame coords),
NOT the raw MOG2 contour blobs. This is the standard design for this type of tracker.
"""
import numpy as np
import structlog
from ultralytics import YOLO
import supervision as sv

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
            log.info("yolo_class_filter_disabled")

        log.info("yolo_model_ready", device=device)
    return _model


# Per-job ByteTrack state — keyed by job_id
_trackers: dict[int, sv.ByteTrack] = {}


def get_tracker(job_id: int) -> sv.ByteTrack:
    if job_id not in _trackers:
        s = get_settings()
        _trackers[job_id] = sv.ByteTrack(
            minimum_matching_threshold=s.bytetrack_match_threshold,
            lost_track_buffer=s.bytetrack_lost_buffer,
            minimum_consecutive_frames=s.bytetrack_min_hits,
        )
    return _trackers[job_id]


def release_tracker(job_id: int):
    _trackers.pop(job_id, None)


def _detect_in_crop(
    crop: np.ndarray,
    mog_bbox: dict,
) -> list[dict]:
    """
    Run YOLO on a single crop. Returns list of detections, each with:
      {class_label, confidence, bbox (full-frame coords), class_id}
    Returns empty list if no qualifying detection.
    """
    s = get_settings()
    model = get_model()
    results = model(crop, verbose=False, conf=s.oc_confidence_threshold,
                    classes=_allowed_class_ids)
    if not results or len(results[0].boxes) == 0:
        return []

    boxes = results[0].boxes
    detections = []
    ox, oy = mog_bbox["x"], mog_bbox["y"]

    for i in range(len(boxes)):
        conf = float(boxes.conf[i])
        if conf < s.oc_confidence_threshold:
            continue
        class_id = int(boxes.cls[i])
        label = model.names[class_id]

        # Translate YOLO bbox (crop-relative) → full-frame coordinates
        x1, y1, x2, y2 = boxes.xyxy[i].tolist()
        full_bbox = {
            "x": int(ox + x1),
            "y": int(oy + y1),
            "w": int(x2 - x1),
            "h": int(y2 - y1),
        }
        detections.append({
            "class_label": label,
            "confidence":  conf,
            "bbox":        full_bbox,
            "class_id":   class_id,
        })

    return detections


def _iou(a, b) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    if inter == 0:
        return 0.0
    area_a = (ax2 - ax1) * (ay2 - ay1)
    area_b = (bx2 - bx1) * (by2 - by1)
    return inter / (area_a + area_b - inter)


def track_frame(
    job_id: int,
    bboxes: list[dict],
    crops: list[np.ndarray],
) -> list[dict]:
    """
    For each MOG2 candidate region:
      1. Run YOLO on the crop → detections with bbox in full-frame coords
      2. Feed all full-frame YOLO bboxes to ByteTrack
      3. Match ByteTrack output back to detections by IoU
      4. Return per-track results with assigned track_id

    Returns list of dicts: {track_id, class_label, confidence, bbox, crop_idx}
    """
    # Step 1: YOLO detections across all crops
    all_detections: list[tuple[int, dict]] = []  # (crop_idx, det)
    for i, (crop, mog_bbox) in enumerate(zip(crops, bboxes)):
        for det in _detect_in_crop(crop, mog_bbox):
            all_detections.append((i, det))

    if not all_detections:
        return []

    # Step 2: build supervision Detections from YOLO full-frame bboxes
    xyxy = np.array([
        [d["bbox"]["x"], d["bbox"]["y"],
         d["bbox"]["x"] + d["bbox"]["w"], d["bbox"]["y"] + d["bbox"]["h"]]
        for _, d in all_detections
    ], dtype=float)
    conf_arr  = np.array([d["confidence"] for _, d in all_detections], dtype=float)
    class_arr = np.array([d["class_id"]   for _, d in all_detections], dtype=int)

    sv_dets = sv.Detections(xyxy=xyxy, confidence=conf_arr, class_id=class_arr)
    tracker = get_tracker(job_id)
    tracked = tracker.update_with_detections(sv_dets)

    if tracked.tracker_id is None or len(tracked) == 0:
        return []

    # Step 3: match each tracked box back to the best-matching YOLO detection by IoU
    # (ByteTrack may reorder or drop boxes — positional index is not reliable)
    results = []
    for j in range(len(tracked)):
        tid    = int(tracked.tracker_id[j])
        t_box  = tracked.xyxy[j]

        best_k, best_iou = 0, -1.0
        for k, (crop_idx, det) in enumerate(all_detections):
            b = det["bbox"]
            d_box = [b["x"], b["y"], b["x"] + b["w"], b["y"] + b["h"]]
            iou = _iou(t_box, d_box)
            if iou > best_iou:
                best_iou = iou
                best_k = k

        crop_idx, det = all_detections[best_k]
        results.append({
            "track_id":    tid,
            "class_label": det["class_label"],
            "confidence":  det["confidence"],
            "bbox":        det["bbox"],
            "crop_idx":    crop_idx,
        })

    return results
