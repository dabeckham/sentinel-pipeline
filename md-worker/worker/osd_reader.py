"""
OSD (On-Screen Display) reader — extracts timestamp and camera name from the
first clear frame of a security camera video using OCR.

Strategy:
  1. Crop the bottom 12% of the frame (where OSD text lives on most cameras).
  2. Scale up 3x and convert to grayscale for better tesseract accuracy.
  3. Run tesseract with --psm 11 (sparse text — handles mixed layouts).
  4. Parse timestamp with common security camera date/time regex patterns.
  5. Remaining text after stripping the timestamp = camera name.

Returns OSDResult(camera_name, recorded_at, raw_text). All fields nullable —
callers must handle None gracefully if OCR finds nothing useful.
"""
import re
from dataclasses import dataclass
from datetime import datetime

import cv2
import numpy as np
import structlog

log = structlog.get_logger()

# --------------------------------------------------------------------------- #
# Timestamp patterns — order matters: try most specific first
# --------------------------------------------------------------------------- #
_TS_PATTERNS = [
    # 2024-01-15 13:45:22  or  2024/01/15 13:45:22
    (r'(\d{4})[/\-](\d{2})[/\-](\d{2})\s+(\d{2}):(\d{2}):(\d{2})', '%Y-%m-%d %H:%M:%S'),
    # 01/15/2024 13:45:22  or  01-15-2024 13:45:22
    (r'(\d{2})[/\-](\d{2})[/\-](\d{4})\s+(\d{2}):(\d{2}):(\d{2})', '%m/%d/%Y %H:%M:%S'),
    # 01/15/24 13:45:22
    (r'(\d{2})[/\-](\d{2})[/\-](\d{2})\s+(\d{2}):(\d{2}):(\d{2})', '%m/%d/%y %H:%M:%S'),
    # 2024.01.15 13:45:22
    (r'(\d{4})\.(\d{2})\.(\d{2})\s+(\d{2}):(\d{2}):(\d{2})', '%Y.%m.%d %H:%M:%S'),
]

# Combined pattern just for stripping timestamps out of the raw text
_TS_STRIP_RE = re.compile(
    r'\d{2,4}[/\-\.]\d{2}[/\-\.]\d{2,4}\s+\d{2}:\d{2}:\d{2}'
)


@dataclass
class OSDResult:
    camera_name: str | None
    recorded_at: datetime | None
    raw_text: str


def _parse_timestamp(text: str) -> tuple[datetime | None, str]:
    """
    Try each timestamp pattern against text.
    Returns (parsed_datetime, text_with_timestamp_removed).
    """
    for pattern, fmt in _TS_PATTERNS:
        m = re.search(pattern, text)
        if m:
            ts_str = m.group(0)
            # Normalise separators for strptime
            ts_normalised = re.sub(r'[/\-\.]', lambda c, i=0: '-' if c.start() < 10 else c.group(), ts_str)
            # Simpler: just replace all date separators then parse
            ts_normalised = re.sub(r'[/\-\.](?=\d)', '-', ts_str)
            # Fix: fmt may use / so normalise fmt to match
            try:
                dt = datetime.strptime(ts_normalised, fmt.replace('/', '-').replace('.', '-'))
                cleaned = re.sub(re.escape(ts_str), '', text)
                return dt, cleaned
            except ValueError:
                continue
    return None, text


def _parse_camera_name(text: str) -> str | None:
    """
    Extract a camera name from OCR'd text that has had the timestamp stripped.
    Looks for a non-trivial alphanumeric token (at least 2 chars).
    """
    for chunk in re.split(r'[\n\r|,]+', text):
        chunk = chunk.strip()
        # Must have at least 2 alphanumeric characters, not pure punctuation/whitespace
        if len(chunk) >= 2 and re.search(r'[A-Za-z0-9]{2,}', chunk):
            return chunk[:80]
    return None


def _preprocess_strip(strip: np.ndarray) -> np.ndarray:
    """
    Scale up and binarise a bottom-strip crop to maximise tesseract accuracy.
    Tries both light-text-on-dark and dark-text-on-light.
    Returns the version that tesseract is likely to prefer (light text inverted to black-on-white).
    """
    h, w = strip.shape[:2]
    # Scale up 3x — tesseract prefers at least 300dpi equivalent
    large = cv2.resize(strip, (w * 3, h * 3), interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(large, cv2.COLOR_BGR2GRAY)

    # Detect if text is light-on-dark (common for camera OSD) and invert
    mean_val = float(gray.mean())
    if mean_val < 128:
        # Dark background — invert so text is dark on white for tesseract
        gray = cv2.bitwise_not(gray)

    # Slight blur to remove compression noise, then threshold
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    _, binary = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return binary


def read_osd(frame: np.ndarray) -> OSDResult:
    """
    OCR the bottom strip of a video frame to extract OSD timestamp + camera name.
    Returns OSDResult — all fields may be None if OCR finds nothing useful.
    """
    try:
        import pytesseract
    except ImportError:
        log.warning("pytesseract_not_installed",
                    msg="Add pytesseract to requirements and tesseract-ocr to Dockerfile")
        return OSDResult(None, None, "")

    h, w = frame.shape[:2]
    strip_h = max(40, int(h * 0.12))
    strip = frame[h - strip_h:h, :]

    processed = _preprocess_strip(strip)

    # --psm 11 = sparse text (find text anywhere, good for overlaid OSD)
    config = '--psm 11 --oem 1 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789:/.-_ '
    try:
        raw_text = pytesseract.image_to_string(processed, config=config).strip()
    except Exception as exc:
        log.warning("osd_ocr_failed", error=str(exc))
        return OSDResult(None, None, "")

    if not raw_text:
        log.debug("osd_no_text_found")
        return OSDResult(None, None, "")

    recorded_at, remaining = _parse_timestamp(raw_text)
    camera_name = _parse_camera_name(remaining)

    log.info("osd_parsed",
             raw_text=raw_text,
             recorded_at=recorded_at.isoformat() if recorded_at else None,
             camera_name=camera_name)

    return OSDResult(camera_name=camera_name, recorded_at=recorded_at, raw_text=raw_text)
