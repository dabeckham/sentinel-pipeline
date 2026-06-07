"""Frigate-style motion detection using weighted frame averaging.

Key differences from MOG2:
- Running average background (avg_frame) instead of adaptive GMM
- Temporal delta smoothing (avg_delta) eliminates single-frame noise
  (rain drops, compression artifacts, insects) that plague MOG2
- Background only absorbs moving objects after 10 consecutive motion frames,
  preventing slow-moving objects from disappearing into the background
- Contrast normalization via percentile stretch improves sensitivity in
  poorly-lit scenes without amplifying uniform noise
"""
import base64
import os
from dataclasses import dataclass
import cv2
import numpy as np
from worker.config import get_settings


def _merge_boxes(boxes: list[tuple], merge_dist: int) -> list[tuple]:
    """Greedily merge (x, y, w, h) boxes within merge_dist pixels of each other."""
    if not boxes:
        return []
    boxes = list(boxes)
    changed = True
    while changed:
        changed = False
        merged = []
        used = [False] * len(boxes)
        for i in range(len(boxes)):
            if used[i]:
                continue
            x1, y1, w1, h1 = boxes[i]
            for j in range(i + 1, len(boxes)):
                if used[j]:
                    continue
                x2, y2, w2, h2 = boxes[j]
                if (x1 - merge_dist < x2 + w2 and x1 + w1 + merge_dist > x2 and
                        y1 - merge_dist < y2 + h2 and y1 + h1 + merge_dist > y2):
                    nx = min(x1, x2)
                    ny = min(y1, y2)
                    x1, y1 = nx, ny
                    w1 = max(x1 + w1, x2 + w2) - nx
                    h1 = max(y1 + h1, y2 + h2) - ny
                    used[j] = True
                    changed = True
            merged.append((x1, y1, w1, h1))
        boxes = merged
    return boxes


@dataclass
class MotionFrame:
    frame_index: int
    timestamp_ms: int
    bounding_boxes: list[dict]   # [{x, y, w, h}, ...] in original resolution
    crops_b64: list[str]         # base64-encoded JPEG per bbox
    frame_b64: str               # base64-encoded JPEG of the full original frame


def detect_motion(video_path: str) -> list[MotionFrame]:
    s = get_settings()
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_width  = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    # Compute motion-detection frame size (same aspect ratio, fixed height)
    motion_h = s.motion_frame_height
    motion_w = max(1, motion_h * frame_width // frame_height)
    # Scale factor to map motion-frame coordinates back to original resolution
    resize_factor = frame_height / motion_h

    # Weighted running averages — the core of Frigate's motion algorithm
    avg_frame = np.zeros((motion_h, motion_w), np.float32)
    avg_delta = np.zeros((motion_h, motion_w), np.float32)
    motion_frame_count = 0  # consecutive frames with motion
    frame_counter = 0       # frames seen (for calibration gate)

    results: list[MotionFrame] = []

    # Debug video
    debug_writer = None
    if s.md_debug_video:
        try:
            os.makedirs(s.md_debug_output_dir, exist_ok=True)
            basename = os.path.splitext(os.path.basename(video_path))[0]
            out_path = os.path.join(s.md_debug_output_dir, f"{basename}_debug.mp4")
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            debug_writer = cv2.VideoWriter(out_path, fourcc, fps, (motion_w, motion_h))
        except OSError as e:
            import structlog
            structlog.get_logger().warning(
                "md_debug_video_dir_unavailable",
                path=s.md_debug_output_dir,
                error=str(e),
            )

    frame_index = 0
    last_known_boxes: list[dict] = []

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                break

            skip = s.motion_frame_skip > 0 and frame_index % (s.motion_frame_skip + 1) != 0
            motion_boxes_this_frame: list[dict] = []

            if not skip:
                # Grayscale + resize to motion frame size
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                resized = cv2.resize(gray, (motion_w, motion_h), interpolation=cv2.INTER_LINEAR)

                # Contrast normalization: stretch 4th–96th percentile to 0–255
                if s.motion_improve_contrast:
                    lo = np.percentile(resized, 4)
                    hi = np.percentile(resized, 96)
                    if lo < hi:
                        resized = np.clip(resized, lo, hi)
                        resized = ((resized - lo) / (hi - lo) * 255).astype(np.uint8)

                resized_f = resized.astype(np.float32)

                if frame_counter < 30:
                    # Calibration: initialize background average
                    cv2.accumulateWeighted(resized_f, avg_frame, 1.0)
                    frame_counter += 1
                else:
                    frame_counter += 1

                    # Absolute difference from current background estimate
                    frame_delta = cv2.absdiff(resized, cv2.convertScaleAbs(avg_frame))

                    # Smooth the delta over time — a single raindrop won't persist
                    cv2.accumulateWeighted(
                        frame_delta.astype(np.float32), avg_delta, s.motion_delta_alpha
                    )

                    # Threshold current frame delta
                    _, current_thresh = cv2.threshold(
                        frame_delta, s.motion_threshold, 255, cv2.THRESH_BINARY
                    )

                    # Only use smoothed delta where the current frame also shows change
                    avg_delta_img = cv2.convertScaleAbs(avg_delta)
                    avg_delta_img = cv2.bitwise_and(avg_delta_img, current_thresh)

                    # Final threshold on the intersection
                    _, thresh = cv2.threshold(
                        avg_delta_img, s.motion_threshold, 255, cv2.THRESH_BINARY
                    )

                    # Dilate to fill holes, then find contours
                    thresh_dil = cv2.dilate(thresh, None, iterations=2)
                    contours, _ = cv2.findContours(
                        thresh_dil, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
                    )

                    raw_boxes_scaled = []
                    for c in contours:
                        if cv2.contourArea(c) < s.motion_min_contour_area:
                            continue
                        x, y, w, h = cv2.boundingRect(c)
                        raw_boxes_scaled.append((x, y, w, h))

                    # Update background average
                    if raw_boxes_scaled:
                        motion_frame_count += 1
                        if motion_frame_count >= 10:
                            # Only absorb after 10 consecutive motion frames
                            # prevents moving objects from being erased into background
                            cv2.accumulateWeighted(resized_f, avg_frame, s.motion_frame_alpha)
                    else:
                        cv2.accumulateWeighted(resized_f, avg_frame, s.motion_frame_alpha)
                        motion_frame_count = 0

                    if raw_boxes_scaled:
                        fh, fw = frame.shape[:2]
                        merged_scaled = _merge_boxes(raw_boxes_scaled, s.motion_merge_dist)

                        boxes = []
                        crops_b64 = []
                        for sx, sy, sw, sh in merged_scaled:
                            # Scale back to original resolution
                            x = int(sx * resize_factor)
                            y = int(sy * resize_factor)
                            w = int(sw * resize_factor)
                            h = int(sh * resize_factor)
                            x, y = max(0, x), max(0, y)
                            w, h = min(w, fw - x), min(h, fh - y)
                            if w < 4 or h < 4:
                                continue
                            boxes.append({"x": x, "y": y, "w": w, "h": h})
                            crop = frame[y:y + h, x:x + w]
                            ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
                            if ok:
                                crops_b64.append(base64.b64encode(buf.tobytes()).decode("ascii"))

                        if boxes:
                            timestamp_ms = int(frame_index * 1000 / fps)
                            ok_f, buf_f = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                            frame_b64 = base64.b64encode(buf_f.tobytes()).decode("ascii") if ok_f else ""
                            results.append(MotionFrame(
                                frame_index=frame_index,
                                timestamp_ms=timestamp_ms,
                                bounding_boxes=boxes,
                                crops_b64=crops_b64,
                                frame_b64=frame_b64,
                            ))
                            motion_boxes_this_frame = boxes
                            last_known_boxes = boxes

            if debug_writer is not None:
                gray_small = cv2.cvtColor(
                    cv2.resize(frame, (motion_w, motion_h), interpolation=cv2.INTER_LINEAR),
                    cv2.COLOR_BGR2GRAY,
                )
                dbg = cv2.cvtColor(gray_small, cv2.COLOR_GRAY2BGR)
                draw_boxes = motion_boxes_this_frame or (last_known_boxes if skip else [])
                color = (0, 255, 0) if motion_boxes_this_frame else (0, 255, 255)
                if draw_boxes:
                    for box in draw_boxes:
                        scale_x = motion_w / frame.shape[1]
                        scale_y = motion_h / frame.shape[0]
                        sx = int(box["x"] * scale_x)
                        sy = int(box["y"] * scale_y)
                        sw = int(box["w"] * scale_x)
                        sh = int(box["h"] * scale_y)
                        cv2.rectangle(dbg, (sx, sy), (sx + sw, sy + sh), color, 2)
                debug_writer.write(dbg)

            frame_index += 1

    finally:
        cap.release()
        if debug_writer is not None:
            debug_writer.release()

    return results
