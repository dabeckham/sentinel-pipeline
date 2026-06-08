"""YOLO detection + ByteTrack tracking — TRT FP16 + CPU pipeline decode.

Architecture (issue #39):
  One OC worker owns one job end-to-end.  The worker receives a job-descriptor
  message containing the video path and the list of motion frame indices
  identified by the MD worker.  It opens the video locally, decodes motion
  frames on a background thread (CPU pipeline), and runs TRT FP16 inference
  with Ultralytics built-in ByteTrack on the main thread.

  Because one worker always processes all frames of a job in order, ByteTrack
  sees a continuous frame sequence and produces correct, stable track IDs.

Decode strategy: TRT + CPU pipeline
  CPU H.265 decode ceiling  : ~47 fps  (OpenCV, single thread, 1280px)
  TRT FP16 inference ceiling : ~46 fps  (GPU, no I/O)
  Both are nearly equal → pipeline (overlapped threads) reaches ~42 fps.
  TRT releases the GIL during its C++ kernel, so the CPU decode thread
  genuinely runs in parallel.  NVDEC was tested and rejected: subprocess pipe
  overhead makes file-based NVDEC 18% slower than OpenCV, and scale_cuda
  running alongside TRT causes GPU context thrashing (5 fps end-to-end).
  See decode_inference_research.md for full benchmark results.

TRT engine:
  First call auto-exports yolo11s.pt → yolo11s.engine (FP16, ~4 min).
  Engine is cached on the shared yolo-models volume — instant on subsequent
  restarts.  Engine is GPU-architecture specific (compiled for this GPU).
"""

import queue
import threading
import time
from pathlib import Path

import cv2
import structlog
from ultralytics import YOLO

from worker.config import get_settings

log = structlog.get_logger()

_model: YOLO | None = None
_allowed_class_ids: list[int] | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Model singleton — TRT auto-export + load
# ─────────────────────────────────────────────────────────────────────────────

def get_model() -> YOLO:
    """Return the YOLO model singleton, loading/exporting on first call."""
    global _model, _allowed_class_ids
    if _model is not None:
        return _model

    s = get_settings()
    pt_path    = Path(s.yolo_model_path)
    engine_path = pt_path.with_suffix(".engine")

    if s.oc_use_gpu and engine_path.exists():
        log.info("trt_engine_loading", engine=str(engine_path))
        _model = YOLO(str(engine_path), task="detect")
        log.info("trt_engine_ready", engine=str(engine_path))

    elif s.oc_use_gpu and not engine_path.exists():
        log.info("trt_engine_exporting",
                 pt=str(pt_path), engine=str(engine_path),
                 note="FP16 export takes ~4 min on first run")
        t0 = time.perf_counter()
        tmp = YOLO(str(pt_path))
        exported = tmp.export(
            format="engine",
            imgsz=s.yolo_imgsz,
            device=0,
            half=True,
            simplify=True,
        )
        elapsed = time.perf_counter() - t0
        log.info("trt_engine_exported", path=str(exported), elapsed_s=round(elapsed, 1))
        _model = YOLO(str(exported), task="detect")

    else:
        # CPU or GPU without TRT — plain PyTorch
        log.info("yolo_model_loading", model=str(pt_path))
        _model = YOLO(str(pt_path))
        device = "cuda:0" if s.oc_use_gpu else "cpu"
        _model.to(device)
        log.info("yolo_model_ready", device=device)

    # Build allowed-class filter
    allowed_names = {c.strip().lower() for c in s.oc_allowed_classes.split(",") if c.strip()}
    if allowed_names:
        _allowed_class_ids = [
            cid for cid, name in _model.names.items()
            if name.lower() in allowed_names
        ]
        log.info("yolo_class_filter",
                 allowed_ids=_allowed_class_ids,
                 allowed_names=sorted(allowed_names))
    else:
        _allowed_class_ids = None

    return _model


# ─────────────────────────────────────────────────────────────────────────────
# CPU pipeline decode source
# ─────────────────────────────────────────────────────────────────────────────

def _pipeline_source(video_path: str, frame_indices: list[int],
                     decode_width: int = 1280, buffer_size: int = 8):
    """
    Yield decoded BGR frames for each index in frame_indices, in order.
    A background thread seeks to each frame and decodes it; the main thread
    consumes from the queue while running TRT inference — they overlap.

    Yields (frame_index, bgr_frame) tuples.
    """
    fps, width, height = _video_meta(video_path)
    scale = decode_width / width
    out_w = decode_width
    out_h = int(height * scale)

    frame_q: queue.Queue = queue.Queue(maxsize=buffer_size)
    _DONE = object()

    def _decode_worker():
        cap = cv2.VideoCapture(video_path)
        try:
            for idx in frame_indices:
                cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
                ok, frame = cap.read()
                if not ok:
                    log.warning("pipeline_frame_read_failed",
                                video=video_path, frame_index=idx)
                    continue
                resized = cv2.resize(frame, (out_w, out_h),
                                     interpolation=cv2.INTER_LINEAR)
                frame_q.put((idx, resized))
        finally:
            cap.release()
            frame_q.put(_DONE)

    t = threading.Thread(target=_decode_worker, daemon=True)
    t.start()

    while True:
        item = frame_q.get()
        if item is _DONE:
            break
        yield item

    t.join()


def _video_meta(video_path: str) -> tuple[float, int, int]:
    cap = cv2.VideoCapture(video_path)
    fps    = cap.get(cv2.CAP_PROP_FPS) or 30.0
    width  = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    return fps, width, height


# ─────────────────────────────────────────────────────────────────────────────
# Job processing
# ─────────────────────────────────────────────────────────────────────────────

def process_job_video(
    video_path: str,
    motion_frame_indices: list[int],
) -> list[dict]:
    """
    Process all motion frames of a single job.

    Opens the video, decodes motion frames via CPU pipeline, runs TRT FP16
    inference with ByteTrack (persist=True) on each frame in order.

    Returns a flat list of detection dicts:
        {track_id, class_label, confidence, bbox, frame_index, timestamp_ms}
    where bbox = {x, y, w, h} in original video pixel coordinates.
    """
    if not motion_frame_indices:
        return []

    s     = get_settings()
    model = get_model()

    fps, orig_w, orig_h = _video_meta(video_path)

    # Scale factor: detections come back in the decoded (resized) frame space;
    # we need to map them back to original pixel coordinates.
    decode_width = 1280
    scale_x = orig_w / decode_width
    scale_y = orig_h / int(orig_h * (decode_width / orig_w))

    all_detections: list[dict] = []

    log.info("oc_job_processing_start",
             video=video_path,
             motion_frames=len(motion_frame_indices))

    t0 = time.perf_counter()

    for frame_index, frame in _pipeline_source(video_path, motion_frame_indices):
        timestamp_ms = int((frame_index / fps) * 1000)

        results = model.track(
            source=frame,
            tracker="bytetrack.yaml",
            conf=s.oc_confidence_threshold,
            iou=s.oc_iou_threshold,
            imgsz=s.yolo_imgsz,
            classes=_allowed_class_ids,
            persist=True,       # keep ByteTrack state across per-frame calls
            verbose=False,
        )

        if not results:
            continue

        r = results[0]
        if r.boxes is None or r.boxes.id is None:
            continue

        boxes = r.boxes
        for i in range(len(boxes)):
            x1, y1, x2, y2 = boxes.xyxy[i].tolist()
            # Map back to original frame coordinates
            x1 = int(x1 * scale_x);  x2 = int(x2 * scale_x)
            y1 = int(y1 * scale_y);  y2 = int(y2 * scale_y)
            w = max(1, x2 - x1)
            h = max(1, y2 - y1)

            all_detections.append({
                "track_id":    int(boxes.id[i]),
                "class_label": model.names[int(boxes.cls[i])],
                "confidence":  round(float(boxes.conf[i]), 4),
                "bbox":        {"x": x1, "y": y1, "w": w, "h": h},
                "frame_index": frame_index,
                "timestamp_ms": timestamp_ms,
            })

    elapsed = time.perf_counter() - t0
    log.info("oc_job_processing_done",
             video=video_path,
             motion_frames=len(motion_frame_indices),
             detections=len(all_detections),
             elapsed_s=round(elapsed, 2),
             fps=round(len(motion_frame_indices) / elapsed, 1) if elapsed > 0 else 0)

    return all_detections
