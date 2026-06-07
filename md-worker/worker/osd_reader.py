"""
OSD (On-Screen Display) reader — extracts timestamp and camera name from the
first clear frame of a security camera video using OCR.

Strategy:
  1. Crop the bottom 12% of the frame (where OSD text lives on most cameras).
  2. Scale up 3x and convert to grayscale for better tesseract accuracy.
  3. Run tesseract with --psm 11 (sparse text — handles mixed layouts).
  4. Split result into lines. Last non-empty line that looks like a camera
     name is used as camera_name. Lines are searched for a timestamp.
  5. Timestamp parsing is fuzzy — normalises common OCR digit-merge errors
     before applying strptime.

Returns OSDResult(camera_name, recorded_at, raw_text). All fields nullable.
"""
import re
from dataclasses import dataclass
from datetime import datetime

import cv2
import numpy as np
import structlog

log = structlog.get_logger()

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _clean_ocr_digits(text: str) -> str:
    """
    Normalise common tesseract digit-merge errors so downstream regex can match.
    e.g. "05/72672026" → "05/26/2026"  (OCR merged "2" with adjacent chars)
         "11:359:19"  → "11:35:19"     (extra digit in minutes or seconds field)
         "_" between date and time → space
    """
    # Replace underscore between date-like and time-like sections with space
    text = re.sub(r'(\d)[_](\d)', r'\1 \2', text)
    # Remove am/pm/AM/PM and day names — extract and handle separately
    text = re.sub(r'\b(am|pm|AM|PM)\b', '', text)
    text = re.sub(r'\b(MON|TUE|WED|THU|FRI|SAT|SUN)\b', '', text, flags=re.IGNORECASE)
    text = text.strip()
    return text


def _try_parse_date_time(text: str) -> datetime | None:
    """
    Try to parse a datetime from OCR'd text. Uses multiple format attempts
    and normalises separators before strptime.
    """
    # Strip noise characters (keep digits, / - . : space)
    cleaned = re.sub(r'[^0-9/:.\- ]', '', _clean_ocr_digits(text)).strip()
    cleaned = re.sub(r'\s+', ' ', cleaned)

    # Candidate patterns (regex, strptime fmt)
    patterns = [
        # 2024-01-15 13:45:22  or  2024/01/15 13:45:22
        (r'(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\s+(\d{1,2}):(\d{2}):(\d{2})', 'ymd'),
        # 01/15/2024 13:45:22  (month/day/year)
        (r'(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})\s+(\d{1,2}):(\d{2}):(\d{2})', 'mdy'),
        # 01/15/24 13:45:22  (2-digit year)
        (r'(\d{1,2})[-/.](\d{1,2})[-/.](\d{2})\s+(\d{1,2}):(\d{2}):(\d{2})', 'mdy2'),
    ]

    for pat, order in patterns:
        m = re.search(pat, cleaned)
        if not m:
            continue
        g = m.groups()
        try:
            if order == 'ymd':
                yr, mo, dy, hh, mm, ss = int(g[0]), int(g[1]), int(g[2]), int(g[3]), int(g[4]), int(g[5])
            elif order == 'mdy':
                mo, dy, yr, hh, mm, ss = int(g[0]), int(g[1]), int(g[2]), int(g[3]), int(g[4]), int(g[5])
            else:  # mdy2
                mo, dy, yr2, hh, mm, ss = int(g[0]), int(g[1]), int(g[2]), int(g[3]), int(g[4]), int(g[5])
                yr = 2000 + yr2 if yr2 < 70 else 1900 + yr2

            if not (1 <= mo <= 12 and 1 <= dy <= 31 and 0 <= hh <= 23 and 0 <= mm <= 59 and 0 <= ss <= 59):
                continue
            return datetime(yr, mo, dy, hh, mm, ss)
        except (ValueError, OverflowError):
            continue

    return None


def _looks_like_camera_name(text: str) -> bool:
    """
    Heuristic: a camera name has letters, is not mostly digits, and
    is not a timestamp line.
    """
    if not text or len(text) < 2:
        return False
    letter_ratio = sum(c.isalpha() for c in text) / max(len(text), 1)
    if letter_ratio < 0.2:          # mostly digits → probably a timestamp
        return False
    if re.search(r'\d{4}.*:\d{2}', text):  # contains year + time → timestamp
        return False
    return True


@dataclass
class OSDResult:
    camera_name: str | None
    recorded_at: datetime | None
    raw_text: str


def _preprocess_strip(strip: np.ndarray) -> np.ndarray:
    """
    Scale up and binarise a bottom-strip crop to maximise tesseract accuracy.
    """
    h, w = strip.shape[:2]
    large = cv2.resize(strip, (w * 3, h * 3), interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(large, cv2.COLOR_BGR2GRAY)

    # Invert if text is light-on-dark (common for camera OSD)
    mean_val = float(gray.mean())
    if mean_val < 128:
        gray = cv2.bitwise_not(gray)

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
        log.warning("pytesseract_not_installed")
        return OSDResult(None, None, "")

    h, w = frame.shape[:2]
    strip_h = max(40, int(h * 0.12))
    strip = frame[h - strip_h:h, :]

    processed = _preprocess_strip(strip)

    # --psm 11 = sparse text, good for overlaid OSD
    config = '--psm 11 --oem 1'
    try:
        raw_text = pytesseract.image_to_string(processed, config=config).strip()
    except Exception as exc:
        log.warning("osd_ocr_failed", error=str(exc))
        return OSDResult(None, None, "")

    if not raw_text:
        return OSDResult(None, None, "")

    # Split into non-empty lines and work line by line
    lines = [ln.strip() for ln in raw_text.splitlines() if ln.strip()]

    recorded_at: datetime | None = None
    camera_name: str | None = None

    # Try each line as a timestamp candidate; grab camera name from lines that
    # don't parse as timestamps — prefer the LAST such line (camera name is
    # usually bottom-right, timestamp is bottom-left on most DVR/NVR systems).
    name_candidates = []
    for line in lines:
        dt = _try_parse_date_time(line)
        if dt and recorded_at is None:
            recorded_at = dt
        elif _looks_like_camera_name(line):
            name_candidates.append(line[:80])

    # Last candidate is most likely the camera name (furthest from timestamp)
    if name_candidates:
        camera_name = name_candidates[-1]

    log.info("osd_parsed",
             raw_text=raw_text,
             lines=lines,
             recorded_at=recorded_at.isoformat() if recorded_at else None,
             camera_name=camera_name)

    return OSDResult(camera_name=camera_name, recorded_at=recorded_at, raw_text=raw_text)
