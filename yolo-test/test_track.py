"""
YOLO built-in tracking test — ByteTrack on full video.

Usage:
    python test_track.py /videos/clip.mp4
    python test_track.py /videos/clip.mp4 --tracker botrack
    python test_track.py /videos/  # process all .mp4 in directory
"""

import argparse
import sys
import time
import os
from collections import defaultdict
from pathlib import Path

import cv2
import torch
from ultralytics import YOLO

MODEL_PATH = os.environ.get("YOLO_MODEL", "/models/yolo11s.pt")
ALLOWED_CLASSES = (
    "person,bicycle,car,motorcycle,airplane,bus,train,truck,boat,"
    "bird,cat,dog,horse,sheep,cow,elephant,bear,zebra,giraffe"
)


def get_allowed_ids(model):
    allowed = {c.strip().lower() for c in ALLOWED_CLASSES.split(",") if c.strip()}
    return [cid for cid, name in model.names.items() if name.lower() in allowed]


def process_video(video_path: str, tracker: str = "bytetrack", conf: float = 0.5, verbose: bool = False):
    print(f"\n{'='*60}")
    print(f"Video   : {video_path}")
    print(f"Tracker : {tracker}")
    print(f"Model   : {MODEL_PATH}")
    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    print(f"Device  : {device}")
    print(f"{'='*60}")

    # ── GPU memory before ────────────────────────────────────────
    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()
        mem_before = torch.cuda.memory_allocated() / 1024**2

    model = YOLO(MODEL_PATH)
    model.to(device)
    allowed_ids = get_allowed_ids(model)
    print(f"Watching {len(allowed_ids)} classes: {', '.join(sorted(model.names[i] for i in allowed_ids))}\n")

    # ── Video metadata ────────────────────────────────────────────
    cap = cv2.VideoCapture(video_path)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps         = cap.get(cv2.CAP_PROP_FPS) or 30.0
    width       = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height      = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    duration_s  = total_frames / fps
    cap.release()
    print(f"Resolution : {width}x{height}")
    print(f"Frames     : {total_frames}  ({fps:.1f} fps  →  {duration_s:.1f}s)")

    # ── Run tracking ──────────────────────────────────────────────
    track_frames: dict[int, list[int]]  = defaultdict(list)  # track_id → [frame_idxs]
    track_classes: dict[int, str]       = {}                  # track_id → class label
    track_confs: dict[int, list[float]] = defaultdict(list)   # track_id → confidences
    detections_per_frame: list[int]     = []
    frames_with_detections = 0

    t_start = time.perf_counter()
    frame_idx = 0

    results = model.track(
        source=video_path,
        tracker=f"{tracker}.yaml",
        conf=conf,
        classes=allowed_ids,
        stream=True,       # memory-efficient generator
        verbose=False,
        persist=True,      # maintain track state across frames
    )

    for r in results:
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
            elapsed = time.perf_counter() - t_start
            fps_so_far = frame_idx / elapsed
            print(f"  frame {frame_idx:5d}/{total_frames}  {fps_so_far:5.1f} fps  tracks_so_far={len(track_frames)}")

    elapsed = time.perf_counter() - t_start

    # ── GPU memory peak ───────────────────────────────────────────
    if torch.cuda.is_available():
        mem_peak = torch.cuda.max_memory_allocated() / 1024**2
    else:
        mem_peak = 0

    # ── Summary ───────────────────────────────────────────────────
    proc_fps      = frame_idx / elapsed if elapsed > 0 else 0
    speedup       = proc_fps / fps
    unique_tracks = len(track_frames)
    total_dets    = sum(detections_per_frame)
    avg_dets      = total_dets / frame_idx if frame_idx else 0

    # per-class breakdown
    class_counts: dict[str, int] = defaultdict(int)
    class_track_ids: dict[str, set] = defaultdict(set)
    for tid, label in track_classes.items():
        class_counts[label] += 1
        class_track_ids[label].add(tid)

    # track lifespan stats
    lifespans = [len(v) for v in track_frames.values()]
    avg_lifespan  = sum(lifespans) / len(lifespans) if lifespans else 0
    long_tracks   = sum(1 for l in lifespans if l >= 10)

    print(f"\n{'─'*60}")
    print(f"RESULTS")
    print(f"{'─'*60}")
    print(f"Frames processed : {frame_idx} / {total_frames}")
    print(f"Elapsed          : {elapsed:.1f}s  (video duration: {duration_s:.1f}s)")
    print(f"Throughput       : {proc_fps:.1f} fps  ({speedup:.1f}x real-time)")
    print(f"GPU peak memory  : {mem_peak:.0f} MB")
    print(f"")
    print(f"Unique tracks    : {unique_tracks}")
    print(f"  ≥10 frame life : {long_tracks}  (likely real objects)")
    print(f"  avg lifespan   : {avg_lifespan:.1f} frames")
    print(f"Total detections : {total_dets}  ({avg_dets:.2f}/frame avg)")
    print(f"Frames w/ dets   : {frames_with_detections} / {frame_idx}  ({100*frames_with_detections/frame_idx:.1f}%)")
    print(f"")
    print(f"Per-class breakdown:")
    for label, count in sorted(class_counts.items(), key=lambda x: -x[1]):
        print(f"  {label:<16} {count:4d} tracks")

    # top tracks by lifespan
    top = sorted(track_frames.items(), key=lambda x: -len(x[1]))[:10]
    print(f"\nTop {min(10, len(top))} tracks by lifespan:")
    print(f"  {'ID':>6}  {'class':<14}  {'frames':>8}  {'avg_conf':>8}  {'first_frame':>12}  {'last_frame':>10}")
    for tid, frames in top:
        label = track_classes[tid]
        confs = track_confs[tid]
        print(f"  {tid:>6}  {label:<14}  {len(frames):>8}  {sum(confs)/len(confs):>8.3f}  {frames[0]:>12}  {frames[-1]:>10}")

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


def main():
    parser = argparse.ArgumentParser(description="Test YOLO built-in tracking on video(s)")
    parser.add_argument("source", help="Path to .mp4 file or directory of .mp4 files")
    parser.add_argument("--tracker", default="bytetrack", choices=["bytetrack", "botrack"],
                        help="Tracker to use (default: bytetrack)")
    parser.add_argument("--conf", type=float, default=0.5, help="Confidence threshold (default: 0.5)")
    parser.add_argument("--verbose", action="store_true", help="Print progress every 100 frames")
    args = parser.parse_args()

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

    summaries = []
    for v in videos:
        s = process_video(str(v), tracker=args.tracker, conf=args.conf, verbose=args.verbose)
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
