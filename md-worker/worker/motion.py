"""MOG2-based motion detection. Returns per-frame results with base64-encoded crops."""
import base64
import os
from dataclasses import dataclass
import cv2
import numpy as np
from worker.config import get_settings


@dataclass
class MotionFrame:
    frame_index: int
    timestamp_ms: int
    bounding_boxes: list[dict]   # [{x, y, w, h}, ...] in original resolution
    crops_b64: list[str]         # base64-encoded JPEG per bbox (no MinIO round-trip)


def detect_motion(video_path: str) -> list[MotionFrame]:
    s = get_settings()
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_width  = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    # Scale factor for MOG2 — detect on a smaller frame, crop from original
    scale = s.motion_scale
    small_w = max(1, int(frame_width * scale))
    small_h = max(1, int(frame_height * scale))

    fgbg = cv2.createBackgroundSubtractorMOG2(
        history=s.mog2_history,
        varThreshold=s.mog2_var_threshold,
        detectShadows=s.mog2_detect_shadows,
    )

    results: list[MotionFrame] = []

    # Debug video setup — only if MD_DEBUG_VIDEO=true and dir is writable
    debug_enabled = False
    debug_motion_boxes: dict[int, list[dict]] = {}
    all_frames: list[np.ndarray] = []

    if s.md_debug_video:
        try:
            os.makedirs(s.md_debug_output_dir, exist_ok=True)
            debug_enabled = True
        except OSError as e:
            import structlog
            structlog.get_logger().warning(
                "md_debug_video_dir_unavailable",
                path=s.md_debug_output_dir,
                error=str(e),
            )

    frame_index = 0

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                break

            if debug_enabled:
                all_frames.append(frame.copy())

            if s.motion_frame_skip > 0 and frame_index % (s.motion_frame_skip + 1) != 0:
                frame_index += 1
                continue

            # Resize for MOG2 — much faster on smaller frames
            if scale < 1.0:
                small = cv2.resize(frame, (small_w, small_h), interpolation=cv2.INTER_LINEAR)
            else:
                small = frame

            fgmask = fgbg.apply(small)
            # Remove shadows (value 127) — keep only foreground (255)
            _, fgmask = cv2.threshold(fgmask, 200, 255, cv2.THRESH_BINARY)

            contours, _ = cv2.findContours(fgmask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

            boxes = []
            crops_b64 = []
            inv = 1.0 / scale  # scale coords back to original resolution

            for cnt in contours:
                if cv2.contourArea(cnt) < s.motion_min_contour_area:
                    continue

                sx, sy, sw, sh = cv2.boundingRect(cnt)

                # Scale bbox back to original resolution
                x = int(sx * inv)
                y = int(sy * inv)
                w = int(sw * inv)
                h = int(sh * inv)

                # Clamp to original frame bounds
                fh, fw = frame.shape[:2]
                x, y = max(0, x), max(0, y)
                w, h = min(w, fw - x), min(h, fh - y)
                if w < 4 or h < 4:
                    continue

                boxes.append({"x": x, "y": y, "w": w, "h": h})

                # Crop from original full-res frame — no quality loss
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
                if debug_enabled:
                    debug_motion_boxes[frame_index] = boxes

            frame_index += 1

    finally:
        cap.release()

    # Render debug video if enabled and output dir is writable
    if debug_enabled and all_frames:
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
