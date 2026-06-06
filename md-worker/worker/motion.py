"""MOG2-based motion detection. Returns per-frame results with crops."""
from dataclasses import dataclass, field
import cv2
import numpy as np
from worker.config import get_settings


@dataclass
class MotionFrame:
    frame_index: int
    timestamp_ms: int
    bounding_boxes: list[dict]   # [{x, y, w, h}, ...]
    crops: list[np.ndarray]      # one numpy array per bbox


def detect_motion(video_path: str) -> list[MotionFrame]:
    s = get_settings()
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    fgbg = cv2.createBackgroundSubtractorMOG2(
        history=s.mog2_history,
        varThreshold=s.mog2_var_threshold,
        detectShadows=s.mog2_detect_shadows,
    )

    results: list[MotionFrame] = []
    frame_index = 0

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                break

            if s.motion_frame_skip > 0 and frame_index % (s.motion_frame_skip + 1) != 0:
                frame_index += 1
                continue

            fgmask = fgbg.apply(frame)
            # Remove shadows (value 127) — keep only foreground (255)
            _, fgmask = cv2.threshold(fgmask, 200, 255, cv2.THRESH_BINARY)

            contours, _ = cv2.findContours(fgmask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

            boxes = []
            crops = []
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
                crops.append(frame[y:y + h, x:x + w].copy())

            if boxes:
                timestamp_ms = int(frame_index * 1000 / fps)
                results.append(MotionFrame(
                    frame_index=frame_index,
                    timestamp_ms=timestamp_ms,
                    bounding_boxes=boxes,
                    crops=crops,
                ))

            frame_index += 1
    finally:
        cap.release()

    return results
