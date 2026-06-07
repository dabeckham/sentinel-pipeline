"""MOG2-based motion detection. Returns per-frame results with base64-encoded crops."""
import base64
import os
from dataclasses import dataclass
import cv2
import numpy as np
from worker.config import get_settings


def _merge_boxes(boxes: list[tuple], merge_dist: int) -> list[tuple]:
    """
    Greedily merge (sx, sy, sw, sh) boxes that are within merge_dist pixels
    of each other. Repeat until no further merges are possible.
    Produces one stable whole-object bbox per cluster of motion regions.
    """
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
                # Check if bboxes are within merge_dist of each other
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
    crops_b64: list[str]         # base64-encoded JPEG per bbox — used by OC for classification
    frame_b64: str               # base64-encoded JPEG of the full original frame — saved as snapshot


def detect_motion(video_path: str) -> list[MotionFrame]:
    s = get_settings()
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_width  = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    # Scale factor for MOG2 — detect on smaller frame, crop from original
    scale = s.motion_scale
    small_w = max(1, int(frame_width * scale))
    small_h = max(1, int(frame_height * scale))
    inv = 1.0 / scale

    fgbg = cv2.createBackgroundSubtractorMOG2(
        history=s.mog2_history,
        varThreshold=s.mog2_var_threshold,
        detectShadows=s.mog2_detect_shadows,
    )

    results: list[MotionFrame] = []

    # Debug video — written at scaled resolution (small_w x small_h) to keep encoding fast
    debug_writer = None
    if s.md_debug_video:
        try:
            os.makedirs(s.md_debug_output_dir, exist_ok=True)
            basename = os.path.splitext(os.path.basename(video_path))[0]
            out_path = os.path.join(s.md_debug_output_dir, f"{basename}_debug.mp4")
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            debug_writer = cv2.VideoWriter(out_path, fourcc, fps, (small_w, small_h))
        except OSError as e:
            import structlog
            structlog.get_logger().warning(
                "md_debug_video_dir_unavailable",
                path=s.md_debug_output_dir,
                error=str(e),
            )

    frame_index = 0
    motion_boxes_this_frame: list[dict] = []
    last_known_boxes: list[dict] = []  # carried forward to skipped frames in debug video

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                break

            skip = s.motion_frame_skip > 0 and frame_index % (s.motion_frame_skip + 1) != 0
            motion_boxes_this_frame = []

            if not skip:
                # Resize for MOG2 — much faster on smaller frames
                small = cv2.resize(frame, (small_w, small_h), interpolation=cv2.INTER_LINEAR) \
                    if scale < 1.0 else frame

                fgmask = fgbg.apply(small)
                _, fgmask = cv2.threshold(fgmask, 200, 255, cv2.THRESH_BINARY)

                contours, _ = cv2.findContours(fgmask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

                # Collect raw bboxes in scaled-frame coordinates, filter tiny noise
                raw_boxes = []
                for cnt in contours:
                    if cv2.contourArea(cnt) < s.motion_min_contour_area:
                        continue
                    raw_boxes.append(cv2.boundingRect(cnt))  # (sx, sy, sw, sh)

                # Merge nearby boxes into whole-object bboxes so ByteTrack gets
                # a stable position to match across frames
                merged_scaled = _merge_boxes(raw_boxes, s.motion_merge_dist)

                boxes = []
                crops_b64 = []
                fh, fw = frame.shape[:2]

                for sx, sy, sw, sh in merged_scaled:
                    # Scale back to original resolution
                    x = int(sx * inv)
                    y = int(sy * inv)
                    w = int(sw * inv)
                    h = int(sh * inv)

                    # Clamp to frame bounds
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
                    # Encode full original frame — OC worker saves this as the detection snapshot
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

            # Write debug frame at scaled resolution — fast encode, low NFS I/O
            if debug_writer is not None:
                dbg_frame = small if not skip else cv2.resize(
                    frame, (small_w, small_h), interpolation=cv2.INTER_LINEAR)

                # Green = detected this frame, Yellow = carried forward from last detection
                draw_boxes = motion_boxes_this_frame or (last_known_boxes if skip else [])
                color = (0, 255, 0) if motion_boxes_this_frame else (0, 255, 255)

                if draw_boxes:
                    annotated = dbg_frame.copy()
                    for box in draw_boxes:
                        sx = int(box["x"] * scale)
                        sy = int(box["y"] * scale)
                        sw = int(box["w"] * scale)
                        sh = int(box["h"] * scale)
                        cv2.rectangle(annotated, (sx, sy), (sx + sw, sy + sh), color, 2)
                        cv2.putText(annotated, f"f{frame_index}", (sx, max(sy - 4, 10)),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1)
                    debug_writer.write(annotated)
                else:
                    debug_writer.write(dbg_frame)

            frame_index += 1

    finally:
        cap.release()
        if debug_writer is not None:
            debug_writer.release()

    return results
