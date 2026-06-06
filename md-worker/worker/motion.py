"""MOG2-based motion detection. Returns per-frame results with base64-encoded crops."""
import base64
import os
from dataclasses import dataclass, field
import cv2
import numpy as np
from worker.config import get_settings


@dataclass
class MotionFrame:
    frame_index: int
    timestamp_ms: int
    bounding_boxes: list[dict]   # [{x, y, w, h}, ...]
    crops_b64: list[str]         # base64-encoded JPEG per bbox (no MinIO round-trip)


def detect_motion(video_path: str) -> list[MotionFrame]:
    s = get_settings()
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_width  = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    fgbg = cv2.createBackgroundSubtractorMOG2(
        history=s.mog2_history,
        varThreshold=s.mog2_var_threshold,
        detectShadows=s.mog2_detect_shadows,
    )

    results: list[MotionFrame] = []

    # Debug video setup — only if MD_DEBUG_VIDEO=true
    debug_writer = None
    debug_motion_boxes: dict[int, list[dict]] = {}  # frame_index → boxes
    all_frames: list[np.ndarray] = []

    if s.md_debug_video:
        os.makedirs(s.md_debug_output_dir, exist_ok=True)

    frame_index = 0

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                break

            if s.md_debug_video:
                all_frames.append(frame.copy())

            if s.motion_frame_skip > 0 and frame_index % (s.motion_frame_skip + 1) != 0:
                frame_index += 1
                continue

            fgmask = fgbg.apply(frame)
            # Remove shadows (value 127) — keep only foreground (255)
            _, fgmask = cv2.threshold(fgmask, 200, 255, cv2.THRESH_BINARY)

            contours, _ = cv2.findContours(fgmask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

            boxes = []
            crops_b64 = []
            for cnt in contours:
                if cv2.contourArea(cnt) < s.motion_min_contour_area:
                    continue
                x, y, w, h = cv2.boundingRect(cnt)
                # Clamp to frame bounds
                fh, fw = frame.shape[:2]
                x, y = max(0, x), max(0, y)
                w, h = min(w, fw - x), min(h, fh - y)
                if w < 4 or h < 4:
                    continue
                boxes.append({"x": x, "y": y, "w": w, "h": h})

                # Encode crop as JPEG and base64 — no MinIO upload needed
                crop = frame[y:y + h, x:x + w]
                ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
                if ok:
                    crops_b64.append(base64.b64encode(buf.tobytes()).decode("ascii"))

            if boxes:
                timestamp_ms = int(frame_index * 1000 / fps)
                results.append(MotionFrame(
                    frame_index=frame_index,
                    timestamp_ms=timestamp_ms,
                    bounding_boxes=boxes,
                    crops_b64=crops_b64,
                ))
                if s.md_debug_video:
                    debug_motion_boxes[frame_index] = boxes

            frame_index += 1

    finally:
        cap.release()

    # Render debug video if enabled
    if s.md_debug_video and all_frames:
        _write_debug_video(video_path, all_frames, debug_motion_boxes, fps,
                           frame_width, frame_height, s.md_debug_output_dir)

    return results


def _write_debug_video(
    source_path: str,
    frames: list[np.ndarray],
    motion_boxes: dict[int, list[dict]],
    fps: float,
    width: int,
    height: int,
    output_dir: str,
) -> None:
    """Write a copy of the video with green bounding boxes on motion frames."""
    basename = os.path.splitext(os.path.basename(source_path))[0]
    out_path = os.path.join(output_dir, f"{basename}_debug.mp4")

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(out_path, fourcc, fps, (width, height))

    try:
        for idx, frame in enumerate(frames):
            annotated = frame.copy()
            for box in motion_boxes.get(idx, []):
                x, y, w, h = box["x"], box["y"], box["w"], box["h"]
                cv2.rectangle(annotated, (x, y), (x + w, y + h), (0, 255, 0), 2)
                cv2.putText(annotated, f"f{idx}", (x, max(y - 4, 10)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0), 1)
            writer.write(annotated)
    finally:
        writer.release()
