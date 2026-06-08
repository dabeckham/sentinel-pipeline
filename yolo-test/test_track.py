"""
YOLO built-in tracking test — ByteTrack on full video.

Usage:
    python test_track.py /videos/clip.mp4
    python test_track.py /videos/clip.mp4 --trt            # TensorRT FP16
    python test_track.py /videos/clip.mp4 --trt --pipeline # TRT + threaded CPU decode
    python test_track.py /videos/clip.mp4 --trt --nvdec    # TRT + GPU decode (NVDEC)
    python test_track.py /videos/clip.mp4 --decode-only    # CPU decode speed only
    python test_track.py /videos/clip.mp4 --nvdec --decode-only  # NVDEC speed only
    python test_track.py /videos/  # process all .mp4 in directory

Decode modes (pick one):
  default    STREAM   — OpenCV CPU decode, frame-by-frame (Ultralytics owns I/O)
  --preload  PRELOAD  — OpenCV CPU: decode all to RAM first, then infer
  --pipeline PIPELINE — OpenCV CPU: background thread decode, inference overlaps
  --nvdec    NVDEC    — ffmpeg GPU: hevc_cuvid/h264_cuvid on NVDEC block,
                        scale_cuda resize, hwdownload to CPU RAM as NV12,
                        OpenCV NV12→BGR (trivial), inference on main thread

Combine with --trt to use TensorRT FP16 engine for inference.
Combine with --decode-only to skip inference and measure raw decode throughput.

TensorRT notes:
  - First run with --trt exports yolo11s.pt -> yolo11s.engine (FP16, ~4 min)
  - Engine cached on shared yolo-models volume, instant on subsequent runs
  - Engine is GPU-architecture specific (compiled for the GPU it runs on)
  - BBoxes always in original image coords (Ultralytics handles letterbox/unscale)
"""

import argparse
import subprocess
import sys
import threading
import queue as q_module
import time
import os
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np
import torch
from ultralytics import YOLO

MODEL_PATH = os.environ.get("YOLO_MODEL", "/models/yolo11s.pt")
IMGSZ = int(os.environ.get("YOLO_IMGSZ", "640"))
FFMPEG = os.environ.get("FFMPEG_BIN", "ffmpeg")
ALLOWED_CLASSES = (
    "person,bicycle,car,motorcycle,airplane,bus,train,truck,boat,"
    "bird,cat,dog,horse,sheep,cow,elephant,bear,zebra,giraffe"
)


# ─────────────────────────────────────────────────────────────────────────────
# Model helpers
# ─────────────────────────────────────────────────────────────────────────────

def resolve_model(use_trt: bool) -> str:
    """Return .engine path (exporting if needed) or .pt path."""
    pt_path = Path(MODEL_PATH)
    engine_path = pt_path.with_suffix(".engine")

    if not use_trt:
        print(f"Backend  : PyTorch (.pt)")
        return str(pt_path)

    if engine_path.exists():
        print(f"Backend  : TensorRT (.engine) — using cached {engine_path}")
        return str(engine_path)

    print(f"Backend  : TensorRT — exporting {pt_path} → {engine_path} (FP16, ~4 min) ...")
    t0 = time.perf_counter()
    tmp = YOLO(str(pt_path))
    exported = tmp.export(format="engine", imgsz=IMGSZ, device=0, half=True, simplify=True)
    print(f"Export done in {time.perf_counter()-t0:.1f}s  →  {exported}")
    return str(exported)


def get_allowed_ids(model):
    allowed = {c.strip().lower() for c in ALLOWED_CLASSES.split(",") if c.strip()}
    return [cid for cid, name in model.names.items() if name.lower() in allowed]


# ─────────────────────────────────────────────────────────────────────────────
# Decode sources
# ─────────────────────────────────────────────────────────────────────────────

def _video_meta(video_path: str):
    cap = cv2.VideoCapture(video_path)
    fps    = cap.get(cv2.CAP_PROP_FPS) or 30.0
    width  = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total  = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    return fps, width, height, total


def _preload_frames(video_path: str, decode_width: int = 1280):
    """
    Decode all frames to CPU RAM at decode_width, then return a generator factory.
    Returns (gen_factory, fps, orig_w, orig_h, decode_elapsed_s, frame_count).
    Factory yields one frame at a time so TRT static batch=1 stays satisfied.
    """
    fps, width, height, total = _video_meta(video_path)
    scale = decode_width / width
    out_w = decode_width
    out_h = int(height * scale)
    frame_mb = (out_w * out_h * 3) / (1024 ** 2)
    est_gb   = (frame_mb * total) / 1024
    print(f"Preloading {total} frames at {out_w}x{out_h}  (~{est_gb:.1f} GB RAM) ...")

    frames = []
    cap = cv2.VideoCapture(video_path)
    t0 = time.perf_counter()
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        frames.append(cv2.resize(frame, (out_w, out_h), interpolation=cv2.INTER_LINEAR))
    cap.release()

    elapsed  = time.perf_counter() - t0
    actual_gb = (frame_mb * len(frames)) / 1024
    print(f"Decode done: {len(frames)} frames in {elapsed:.2f}s  "
          f"({len(frames)/elapsed:.1f} fps)  {actual_gb:.2f} GB RAM used")

    def _gen():
        yield from frames

    return _gen, fps, width, height, elapsed, len(frames)


def _cpu_pipeline_source(video_path: str, decode_width: int = 1280, buffer_size: int = 8):
    """
    OpenCV CPU decode on a background thread; yields one BGR frame at a time.
    Inference and decode overlap in parallel threads.
    """
    fps, width, height, total = _video_meta(video_path)
    scale = decode_width / width
    out_w = decode_width
    out_h = int(height * scale)

    frame_q: q_module.Queue = q_module.Queue(maxsize=buffer_size)
    _DONE = object()
    decode_stats = {"frames": 0, "elapsed": 0.0, "fps": 0.0}

    def _decode_worker():
        cap = cv2.VideoCapture(video_path)
        t0 = time.perf_counter()
        count = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            frame_q.put(cv2.resize(frame, (out_w, out_h), interpolation=cv2.INTER_LINEAR))
            count += 1
            elapsed = time.perf_counter() - t0
            decode_stats.update({"frames": count, "elapsed": elapsed,
                                  "fps": count / elapsed if elapsed > 0 else 0.0})
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
    return fps, width, height, total, decode_stats


def _nvdec_source(video_path: str, decode_width: int = 1280, buffer_size: int = 8):
    """
    GPU decode via ffmpeg NVDEC (hevc_cuvid / h264_cuvid auto-selected).
    Pipeline:
      NVDEC block  → CUDA surface  (zero shader usage)
      scale_cuda   → 1280px CUDA   (tiny shader cost)
      hwdownload   → CPU RAM NV12  (PCIe transfer, ~1280*720*1.5 ≈ 1.3MB/frame)
      cvtColor     → BGR numpy     (CPU, trivial)

    Yields one BGR numpy frame at a time (batch=1 safe for TRT).
    Also returns a live stats dict: {"frames", "elapsed", "fps", "src_fps",
                                      "src_w", "src_h", "out_w", "out_h", "total"}
    """
    fps, width, height, total = _video_meta(video_path)
    scale = decode_width / width
    out_w = decode_width
    out_h = (int(height * scale) // 2) * 2  # must be even for NV12 chroma subsampling
    frame_bytes_nv12 = out_w * out_h * 3 // 2

    cmd = [
        FFMPEG, "-hide_banner", "-loglevel", "warning",
        "-hwaccel", "cuda",
        "-hwaccel_output_format", "cuda",
        "-i", video_path,
        "-vf", f"scale_cuda=w={out_w}:h={out_h},hwdownload,format=nv12",
        "-f", "rawvideo",
        "-pix_fmt", "nv12",
        "pipe:1",
    ]

    frame_q: q_module.Queue = q_module.Queue(maxsize=buffer_size)
    _DONE = object()
    decode_stats = {
        "frames": 0, "elapsed": 0.0, "fps": 0.0,
        "src_fps": fps, "src_w": width, "src_h": height,
        "out_w": out_w, "out_h": out_h, "total": total,
        "error": None,
    }

    def _decode_worker():
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=frame_bytes_nv12 * 4,
        )
        t0 = time.perf_counter()
        count = 0
        try:
            while True:
                raw = proc.stdout.read(frame_bytes_nv12)
                if len(raw) < frame_bytes_nv12:
                    break
                nv12 = np.frombuffer(raw, dtype=np.uint8).reshape((out_h * 3 // 2, out_w))
                bgr  = cv2.cvtColor(nv12, cv2.COLOR_YUV2BGR_NV12)
                frame_q.put(bgr)
                count += 1
                elapsed = time.perf_counter() - t0
                decode_stats.update({"frames": count, "elapsed": elapsed,
                                      "fps": count / elapsed if elapsed > 0 else 0.0})
        finally:
            proc.wait()
            stderr_txt = proc.stderr.read().decode(errors="replace").strip()
            if proc.returncode != 0:
                decode_stats["error"] = f"ffmpeg exit {proc.returncode}: {stderr_txt}"
            elif stderr_txt:
                # surface any warnings (e.g. fallback to SW decode)
                print(f"\n[NVDEC ffmpeg] {stderr_txt}", file=sys.stderr)
            frame_q.put(_DONE)

    t = threading.Thread(target=_decode_worker, daemon=True)
    t.start()

    def _gen():
        while True:
            item = frame_q.get()
            if item is _DONE:
                break
            yield item
        t.join()

    return _gen(), decode_stats


# ─────────────────────────────────────────────────────────────────────────────
# Decode-only benchmark (no inference)
# ─────────────────────────────────────────────────────────────────────────────

def benchmark_decode_only(video_path: str, use_nvdec: bool, decode_width: int = 1280):
    """
    Measure raw decode throughput without any inference.
    Reports fps, CPU/NVDEC, and what that means for inference concurrency.
    """
    fps, width, height, total = _video_meta(video_path)
    duration_s = total / fps

    decoder = "NVDEC (GPU)" if use_nvdec else "OpenCV (CPU)"
    print(f"\n{'='*60}")
    print(f"DECODE-ONLY benchmark")
    print(f"Video   : {video_path}")
    print(f"Decoder : {decoder}")
    print(f"Frames  : {total}  ({fps:.1f} fps  →  {duration_s:.1f}s)")
    print(f"{'='*60}")

    if use_nvdec:
        gen, stats = _nvdec_source(video_path, decode_width=decode_width)
    else:
        # Use preload path so we get clean timing
        stats = {"frames": 0, "elapsed": 0.0, "fps": 0.0}

    if use_nvdec:
        count = 0
        for _ in gen:
            count += 1
        decode_fps = stats["fps"]
        elapsed    = stats["elapsed"]
    else:
        cap = cv2.VideoCapture(video_path)
        scale = decode_width / width
        out_w = decode_width
        out_h = int(height * scale)
        t0 = time.perf_counter()
        count = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            cv2.resize(frame, (out_w, out_h), interpolation=cv2.INTER_LINEAR)
            count += 1
        cap.release()
        elapsed    = time.perf_counter() - t0
        decode_fps = count / elapsed if elapsed > 0 else 0

    print(f"\n{'─'*60}")
    print(f"DECODE RESULTS")
    print(f"{'─'*60}")
    print(f"Frames decoded  : {count}")
    print(f"Elapsed         : {elapsed:.2f}s  (video: {duration_s:.1f}s)")
    print(f"Decode fps      : {decode_fps:.1f}  ({decode_fps/fps:.1f}x real-time)")
    if use_nvdec:
        print(f"Output size     : {stats['out_w']}x{stats['out_h']} (NV12 → BGR via CPU)")
    else:
        scale = decode_width / width
        out_w = decode_width
        out_h = int(height * scale)
        print(f"Output size     : {out_w}x{out_h} (CPU resize)")
    print(f"\nImplication: at {decode_fps:.0f} fps decode + ~46 fps TRT inference,")
    trt_fps = 46.0
    if decode_fps >= trt_fps * 2:
        print(f"  decode is {decode_fps/trt_fps:.1f}x faster than inference →")
        print(f"  one decode thread could feed {decode_fps/trt_fps:.1f} inference workers")
        print(f"  (but tracking requires one worker per job — CPU savings matter more)")
    else:
        print(f"  decode and inference are roughly matched — pipeline mode is optimal")
    print(f"{'='*60}\n")


# ─────────────────────────────────────────────────────────────────────────────
# Main processing function
# ─────────────────────────────────────────────────────────────────────────────

def process_video(video_path: str, model_path: str, tracker: str = "bytetrack",
                  conf: float = 0.5, verbose: bool = False,
                  preload: bool = False, pipeline: bool = False,
                  nvdec: bool = False):

    if nvdec:
        mode = "NVDEC     — GPU decode (hevc_cuvid) + inference overlap"
    elif pipeline:
        mode = "PIPELINE  — CPU decode thread + inference overlap"
    elif preload:
        mode = "PRELOAD   — CPU decode all to RAM, then infer"
    else:
        mode = "STREAM    — CPU decode frame-by-frame (Ultralytics owns I/O)"

    print(f"\n{'='*60}")
    print(f"Video   : {video_path}")
    print(f"Tracker : {tracker}")
    print(f"Model   : {model_path}")
    print(f"Mode    : {mode}")
    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    print(f"Device  : {device}")
    print(f"{'='*60}")

    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()

    model = YOLO(model_path)
    if not model_path.endswith(".engine"):
        model.to(device)
    allowed_ids = get_allowed_ids(model)
    print(f"Watching {len(allowed_ids)} classes: "
          f"{', '.join(sorted(model.names[i] for i in allowed_ids))}\n")

    # ── Source setup ─────────────────────────────────────────────────────────
    decode_elapsed  = 0.0
    decode_fps_only = 0.0
    nvdec_stats     = None
    fps, width, height, total_frames = _video_meta(video_path)

    if nvdec:
        print(f"Starting NVDEC decode thread (buffer=8 frames) ...")
        source, nvdec_stats = _nvdec_source(video_path)
        # meta comes from nvdec_stats after decoding starts
    elif preload:
        gen_factory, fps, width, height, decode_elapsed, total_frames = \
            _preload_frames(video_path)
        source = gen_factory()
        decode_fps_only = total_frames / decode_elapsed if decode_elapsed > 0 else 0
        print(f"Decode time: {decode_elapsed:.2f}s  ({decode_fps_only:.1f} fps — CPU only)")
        print(f"--- inference-only timing starts now ---")
    elif pipeline:
        print(f"Starting CPU decode thread (buffer=8 frames) ...")
        source = _cpu_pipeline_source(video_path)
    else:
        source = video_path

    duration_s = total_frames / fps
    print(f"Resolution : {width}x{height}")
    print(f"Frames     : {total_frames}  ({fps:.1f} fps  →  {duration_s:.1f}s)")

    # ── Run tracking ─────────────────────────────────────────────────────────
    track_frames  : dict[int, list[int]]  = defaultdict(list)
    track_classes : dict[int, str]        = {}
    track_confs   : dict[int, list[float]] = defaultdict(list)
    detections_per_frame: list[int]        = []
    frames_with_detections = 0

    t_start   = time.perf_counter()
    frame_idx = 0

    def _iter_results():
        if preload or pipeline or nvdec:
            for frame in source:
                yield model.track(
                    source=frame,
                    tracker=f"{tracker}.yaml",
                    conf=conf,
                    imgsz=IMGSZ,
                    classes=allowed_ids,
                    verbose=False,
                    persist=True,
                )[0]
        else:
            yield from model.track(
                source=source,
                tracker=f"{tracker}.yaml",
                conf=conf,
                imgsz=IMGSZ,
                classes=allowed_ids,
                stream=True,
                verbose=False,
                persist=True,
            )

    for r in _iter_results():
        boxes = r.boxes
        n = 0
        if boxes is not None and boxes.id is not None:
            for i in range(len(boxes)):
                tid   = int(boxes.id[i])
                cid   = int(boxes.cls[i])
                c     = float(boxes.conf[i])
                label = model.names[cid]
                track_frames[tid].append(frame_idx)
                track_classes[tid] = label
                track_confs[tid].append(c)
                n += 1
        detections_per_frame.append(n)
        if n > 0:
            frames_with_detections += 1
        frame_idx += 1

        if verbose and frame_idx % 100 == 0:
            elapsed_so_far = time.perf_counter() - t_start
            fps_so_far = frame_idx / elapsed_so_far
            print(f"  frame {frame_idx:5d}/{total_frames}  "
                  f"{fps_so_far:5.1f} fps  tracks_so_far={len(track_frames)}")

    elapsed = time.perf_counter() - t_start

    # ── GPU memory peak ───────────────────────────────────────────────────────
    mem_peak = torch.cuda.max_memory_allocated() / 1024**2 if torch.cuda.is_available() else 0

    # ── Summary ──────────────────────────────────────────────────────────────
    proc_fps      = frame_idx / elapsed if elapsed > 0 else 0
    speedup       = proc_fps / fps
    unique_tracks = len(track_frames)
    total_dets    = sum(detections_per_frame)
    avg_dets      = total_dets / frame_idx if frame_idx else 0
    lifespans     = [len(v) for v in track_frames.values()]
    avg_lifespan  = sum(lifespans) / len(lifespans) if lifespans else 0
    long_tracks   = sum(1 for l in lifespans if l >= 10)

    class_counts: dict[str, int] = defaultdict(int)
    for tid, label in track_classes.items():
        class_counts[label] += 1

    print(f"\n{'─'*60}")
    print(f"RESULTS")
    print(f"{'─'*60}")
    print(f"Frames processed : {frame_idx} / {total_frames}")

    if decode_elapsed > 0:
        # preload: decode and inference are sequential — show both
        seq_fps     = frame_idx / (decode_elapsed + elapsed)
        overlap_fps = frame_idx / max(decode_elapsed, elapsed)
        print(f"Decode time      : {decode_elapsed:.2f}s  "
              f"({decode_fps_only:.1f} fps — CPU only)")
        print(f"Infer  time      : {elapsed:.2f}s  "
              f"({proc_fps:.1f} fps — GPU only, no I/O)")
        print(f"Sequential total : {decode_elapsed+elapsed:.2f}s  "
              f"({seq_fps:.1f} fps — if decode+infer in series)")
        print(f"Overlap estimate : {max(decode_elapsed,elapsed):.2f}s  "
              f"({overlap_fps:.1f} fps — if fully pipelined)")
    elif nvdec and nvdec_stats:
        if nvdec_stats.get("error"):
            print(f"NVDEC ERROR      : {nvdec_stats['error']}")
        else:
            d_fps = nvdec_stats["fps"]
            d_ela = nvdec_stats["elapsed"]
            print(f"NVDEC decode     : {nvdec_stats['frames']} frames in {d_ela:.2f}s  "
                  f"({d_fps:.1f} fps — NVDEC block, ~0 shader usage)")
            print(f"  Output size    : {nvdec_stats['out_w']}x{nvdec_stats['out_h']} "
                  f"(NV12→BGR on CPU)")
            print(f"Infer+total time : {elapsed:.1f}s  "
                  f"(decode+infer overlapped — video: {duration_s:.1f}s)")
    elif pipeline:
        print(f"Elapsed (total)  : {elapsed:.1f}s  "
              f"(decode+infer overlapped — video: {duration_s:.1f}s)")
    else:
        print(f"Elapsed          : {elapsed:.1f}s  (video: {duration_s:.1f}s)")

    print(f"Throughput       : {proc_fps:.1f} fps  ({speedup:.1f}x real-time)")
    print(f"GPU peak memory  : {mem_peak:.0f} MB")
    print(f"")
    print(f"Unique tracks    : {unique_tracks}")
    print(f"  ≥10 frame life : {long_tracks}  (likely real objects)")
    print(f"  avg lifespan   : {avg_lifespan:.1f} frames")
    print(f"Total detections : {total_dets}  ({avg_dets:.2f}/frame avg)")
    print(f"Frames w/ dets   : {frames_with_detections} / {frame_idx}  "
          f"({100*frames_with_detections/frame_idx:.1f}%)")
    print(f"")
    print(f"Per-class breakdown:")
    for label, count in sorted(class_counts.items(), key=lambda x: -x[1]):
        print(f"  {label:<16} {count:4d} tracks")

    top = sorted(track_frames.items(), key=lambda x: -len(x[1]))[:10]
    print(f"\nTop {min(10, len(top))} tracks by lifespan:")
    print(f"  {'ID':>6}  {'class':<14}  {'frames':>8}  {'avg_conf':>8}  "
          f"{'first_frame':>12}  {'last_frame':>10}")
    for tid, frames in top:
        label = track_classes[tid]
        confs = track_confs[tid]
        print(f"  {tid:>6}  {label:<14}  {len(frames):>8}  "
              f"{sum(confs)/len(confs):>8.3f}  {frames[0]:>12}  {frames[-1]:>10}")

    print(f"{'='*60}\n")
    return {
        "video": video_path,
        "frames": frame_idx,
        "elapsed_s": elapsed,
        "proc_fps": proc_fps,
        "speedup": speedup,
        "unique_tracks": unique_tracks,
        "long_tracks": long_tracks,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="YOLO tracking benchmark — multiple decode modes")
    parser.add_argument("source", help="Path to .mp4 file or directory of .mp4 files")
    parser.add_argument("--tracker", default="bytetrack", choices=["bytetrack", "botsort"])
    parser.add_argument("--conf", type=float, default=0.5)
    parser.add_argument("--trt", action="store_true",
                        help="Use TensorRT FP16 engine (export + cache if not found)")
    parser.add_argument("--preload", action="store_true",
                        help="CPU: decode all frames to RAM first, then infer")
    parser.add_argument("--pipeline", action="store_true",
                        help="CPU: background decode thread overlaps with inference")
    parser.add_argument("--nvdec", action="store_true",
                        help="GPU: ffmpeg hevc_cuvid/h264_cuvid via NVDEC block, "
                             "scale_cuda resize, hwdownload NV12→BGR on CPU")
    parser.add_argument("--decode-only", action="store_true",
                        help="Benchmark decode only (no inference). "
                             "Use with --nvdec for GPU decode, or alone for CPU decode.")
    parser.add_argument("--verbose", action="store_true", help="Progress every 100 frames")
    args = parser.parse_args()

    # Validate: at most one decode mode
    modes = sum([args.preload, args.pipeline, args.nvdec])
    if modes > 1:
        parser.error("--preload, --pipeline, and --nvdec are mutually exclusive")

    source = Path(args.source)
    if source.is_dir():
        videos = sorted(source.glob("**/*.mp4"))
        if not videos:
            print(f"No .mp4 files found in {source}")
            sys.exit(1)
        print(f"Found {len(videos)} video(s) in {source}")
    elif source.is_file():
        videos = [source]
    else:
        print(f"Path not found: {source}")
        sys.exit(1)

    if args.decode_only:
        for v in videos:
            benchmark_decode_only(str(v), use_nvdec=args.nvdec)
        return

    model_path = resolve_model(args.trt)

    summaries = []
    for v in videos:
        s = process_video(
            str(v),
            model_path=model_path,
            tracker=args.tracker,
            conf=args.conf,
            verbose=args.verbose,
            preload=args.preload,
            pipeline=args.pipeline,
            nvdec=args.nvdec,
        )
        summaries.append(s)

    if len(summaries) > 1:
        print(f"\n{'='*60}")
        print(f"BATCH SUMMARY ({len(summaries)} videos)")
        print(f"{'='*60}")
        total_frames = sum(s["frames"] for s in summaries)
        total_time   = sum(s["elapsed_s"] for s in summaries)
        avg_fps      = total_frames / total_time if total_time else 0
        print(f"Total frames : {total_frames}")
        print(f"Total time   : {total_time:.1f}s")
        print(f"Overall fps  : {avg_fps:.1f}")
        print(f"{'='*60}")


if __name__ == "__main__":
    main()
