"""ffmpeg invocation: source clip → H.264 rendition at a target height.

Decodes HEVC (software by default, optional NVDEC) and encodes H.264 with
NVENC on the GPU the container is pinned to. Never upscales past the source
(`min(ih,H)`), keeps aspect, forces an even width, and writes a faststart
(progressive-download) MP4 so the browser can start playing before the whole
file is fetched.
"""
from __future__ import annotations

import subprocess

import structlog

log = structlog.get_logger()


def build_cmd(src: str, dst: str, height: int, bitrate_k: int,
              preset: str, hwaccel_decode: bool) -> list[str]:
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    if hwaccel_decode:
        cmd += ["-hwaccel", "cuda"]
    maxrate = int(bitrate_k * 1.5)
    bufsize = int(bitrate_k * 2)
    cmd += [
        "-i", src,
        # Downscale only — never enlarge a source smaller than the target rung.
        "-vf", f"scale=-2:'min(ih,{height})'",
        "-c:v", "h264_nvenc",
        "-preset", preset,
        "-profile:v", "high",
        "-b:v", f"{bitrate_k}k",
        "-maxrate", f"{maxrate}k",
        "-bufsize", f"{bufsize}k",
        "-pix_fmt", "yuv420p",      # widest browser compatibility
        "-movflags", "+faststart",
        "-an",                       # source clips carry no audio
        dst,
    ]
    return cmd


def transcode(src: str, dst: str, height: int, bitrate_k: int,
              preset: str, hwaccel_decode: bool, timeout_s: int) -> None:
    """Run ffmpeg, raising on non-zero exit or timeout."""
    cmd = build_cmd(src, dst, height, bitrate_k, preset, hwaccel_decode)
    log.info("transcode_start", height=height, bitrate_k=bitrate_k, dst=dst)
    proc = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout_s
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed (rc={proc.returncode}): {proc.stderr.strip()[:500]}"
        )
