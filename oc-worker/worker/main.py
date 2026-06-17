"""OC Worker — Object Classification + Tracking (TRT FP16 + ByteTrack)

Architecture (issue #39):
  Receives one job-descriptor message per job from the motion_results queue.
  Message schema:
    {
      "job_id":          int,
      "video_path":      str,       # absolute path on the shared ingest mount
      "motion_frames":   [int, ...],# frame indices from MD worker
      "osd_camera_name": str | null,
      "osd_recorded_at": str | null,# ISO-8601
      "video_fps":       float
    }
  The worker opens the video locally, runs TRT+ByteTrack, and publishes
  per-detection results to oc_results (same schema as before).
  One worker owns one job — ByteTrack state is never split across workers.
"""
import json
import os
import signal
import socket
import time
from concurrent.futures import ThreadPoolExecutor

import cv2
import pika
import setproctitle
import structlog

from worker.config import get_settings
from worker.detector import process_job_video, get_model
from worker.minio_client import upload_snapshot, upload_jpeg

log = structlog.get_logger()

WORKER_ID = f"{socket.gethostname()}-oc-{os.getpid()}"

# Background thread pool for async MinIO uploads (best-shot + per-detection frames)
_upload_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="minio-upload")

# Per-detection scrub frames: for a MOVING object we save up to this many full
# frames (evenly sampled across its detections) so the UI slider scrubs real
# frames of the object — each auto-zooming into its bbox — instead of sliding a
# box over one still. Stationary tracks don't move, so one best-shot is enough.
PER_TRACK_SNAP_CAP = 24
# Mirror the orchestrator's _classify_tracks default (settings.tracker_min_displacement)
# so "moving" here matches the track_type the user sees.
MOVING_THRESHOLD = 0.3


def _connect(settings) -> tuple[pika.BlockingConnection, any]:
    for attempt in range(20):
        try:
            conn = pika.BlockingConnection(settings.rabbitmq_params())
            ch = conn.channel()
            # prefetch_count=1: with the new per-job architecture each message
            # represents a full video (several seconds of work).  Only pull one
            # at a time so RabbitMQ can distribute remaining jobs to other workers.
            ch.basic_qos(prefetch_count=1)
            log.info("oc_worker_amqp_connected")
            return conn, ch
        except pika.exceptions.AMQPConnectionError as exc:
            wait = min(2 ** attempt, 30)
            log.warning("oc_worker_amqp_retry",
                        attempt=attempt + 1, wait=wait, error=str(exc))
            time.sleep(wait)
    raise RuntimeError("Could not connect to RabbitMQ")


def _upload_best_shot(bucket: str, name: str, frame):
    """Fire-and-forget MinIO upload of a raw frame — runs in background pool."""
    try:
        upload_snapshot(bucket, name, frame)
    except Exception:
        log.exception("oc_best_shot_upload_error", name=name)


def _upload_jpeg(bucket: str, name: str, data: bytes):
    """Fire-and-forget MinIO upload of pre-encoded JPEG bytes."""
    try:
        upload_jpeg(bucket, name, data)
    except Exception:
        log.exception("oc_snapshot_upload_error", name=name)


def _is_moving(dts: list[dict]) -> bool:
    """Same rule as the orchestrator's _classify_tracks: normalized straight-line
    displacement between the first and last detection's centroid (÷ average bbox
    width) ≥ threshold. `dts` must be in ascending frame order."""
    if len(dts) < 2:
        return False
    fb, lb = dts[0]["bbox"], dts[-1]["bbox"]
    fcx, fcy = fb["x"] + fb["w"] / 2.0, fb["y"] + fb["h"] / 2.0
    lcx, lcy = lb["x"] + lb["w"] / 2.0, lb["y"] + lb["h"] / 2.0
    disp = ((lcx - fcx) ** 2 + (lcy - fcy) ** 2) ** 0.5
    avg_w = (fb["w"] + lb["w"]) / 2.0
    return (disp / avg_w if avg_w > 0 else 0.0) >= MOVING_THRESHOLD


def _sample_evenly(items: list, cap: int) -> list:
    """Up to `cap` items evenly spaced across the list (always incl. first/last)."""
    n = len(items)
    if n <= cap:
        return items
    idxs = sorted({round(i * (n - 1) / (cap - 1)) for i in range(cap)})
    return [items[i] for i in idxs]


def process_job(msg: dict, ch, method):
    """
    Process a complete job — open video, run TRT+ByteTrack on motion frames,
    publish per-detection results, publish final marker, update best shots.
    """
    settings        = get_settings()
    job_id          = msg["job_id"]
    video_path      = msg["video_path"]
    motion_frames   = msg.get("motion_frames", [])
    osd_camera_name = msg.get("osd_camera_name")
    osd_recorded_at = msg.get("osd_recorded_at")
    video_fps       = msg.get("video_fps", 30.0)

    log.info("oc_job_start",
             job_id=job_id, video=video_path,
             motion_frames=len(motion_frames), worker=WORKER_ID)

    try:
        # Immediately notify orchestrator that this worker has taken the job
        ch.basic_publish(
            exchange="",
            routing_key=settings.queue_oc_results,
            body=json.dumps({"job_id": job_id, "status": "oc_processing", "worker_id": WORKER_ID}),
            properties=pika.BasicProperties(delivery_mode=2, content_type="application/json"),
        )

        # ── Run TRT + ByteTrack ───────────────────────────────────────────────
        detections = process_job_video(video_path, motion_frames)

        # ── Best-shot selection ───────────────────────────────────────────────
        # Per track, pick the most representative frame for the thumbnail: a
        # large, fully-in-frame, confident view of the object. The old metric
        # scored only vertical-centeredness, which is degenerate for a high-
        # mounted camera — objects never reach mid-frame, so it picked whichever
        # frame the object sat lowest, often jammed against an edge while
        # entering/exiting. Reward area + confidence; penalise edge-touching.
        cap = cv2.VideoCapture(video_path)
        frame_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 1080
        frame_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 1920
        cap.release()

        _mx, _my = 0.02 * frame_w, 0.02 * frame_h        # 2% edge margin
        _cx0, _cy0 = frame_w / 2.0, frame_h / 2.0

        def _shot_quality(det) -> float:
            # Pick the frame where the object is best framed: fully in-frame and as
            # close to centre as possible. Vehicles cross the frame HORIZONTALLY and
            # never reach vertical centre on a high-mounted camera, so the vertical
            # offset is ~constant and must be down-weighted — otherwise it dominates
            # the distance and the pick drifts off the horizontal centre. NO
            # confidence term: every kept detection is already above threshold, and
            # weighting by confidence let a slightly-more-confident off-centre frame
            # beat the centred one (the reported job-22467 mis-pick).
            b = det["bbox"]
            x, y, w, h = b["x"], b["y"], b["w"], b["h"]
            cx, cy = x + w / 2.0, y + h / 2.0
            ndx = (cx - _cx0) / _cx0                 # 0 at centre, ±1 at a left/right edge
            ndy = (cy - _cy0) / _cy0 * 0.35          # vertical weighted ~1/3 of horizontal
            centred = 1.0 - min(1.0, (ndx * ndx + ndy * ndy) ** 0.5)
            touches = ((x <= _mx) + (y <= _my)
                       + (x + w >= frame_w - _mx) + (y + h >= frame_h - _my))
            in_frame = 1.0 if touches == 0 else 0.4 ** touches   # heavy edge penalty
            return in_frame * centred

        best_candidates: dict[int, dict] = {}   # track_id → best detection
        _best_quality: dict[int, float] = {}
        for det in detections:
            if not det.get("bbox"):
                continue
            tid = det["track_id"]
            q = _shot_quality(det)
            if q > _best_quality.get(tid, -1.0):
                _best_quality[tid] = q
                best_candidates[tid] = det

        # ── Snapshot frames to save ───────────────────────────────────────────
        # best-shot per track + per-detection scrub frames for MOVING tracks.
        # Build frame_index → [object_name, …] so a single sequential read
        # uploads every needed frame; encode each frame to JPEG once and reuse
        # the bytes across names (a full-res 11MP frame is ~34MB raw — queueing
        # one copy per detection would blow the worker's memory).
        dets_by_track: dict[int, list[dict]] = {}
        for det in detections:
            if det.get("bbox"):
                dets_by_track.setdefault(det["track_id"], []).append(det)

        frame_saves: dict[int, list[str]] = {}
        # Several tracks can share their best frame (multiple objects most-centred
        # on the same frame) — map to a LIST so a collision doesn't drop a best-shot.
        for tid, det in best_candidates.items():
            frame_saves.setdefault(det["frame_index"], []).append(
                f"{job_id}/track_{tid:06d}_best.jpg")

        # Per-detection scrub frames (moving tracks only); annotate crop_path on
        # the detection so the result consumer persists it and the UI scrubs the
        # real frames. The best detection reuses its already-saved best-shot frame.
        for tid, dts in dets_by_track.items():
            if not _is_moving(dts):
                continue
            for det in _sample_evenly(dts, PER_TRACK_SNAP_CAP):
                if best_candidates.get(tid) is det:
                    det["crop_path"] = f"{job_id}/track_{tid:06d}_best.jpg"
                else:
                    name = f"{job_id}/track_{tid:06d}_det_{det['frame_index']:06d}.jpg"
                    det["crop_path"] = name
                    frame_saves.setdefault(det["frame_index"], []).append(name)

        upload_futures = []
        if frame_saves:
            max_fi = max(frame_saves)
            cap = cv2.VideoCapture(video_path)
            current = 0
            while current <= max_fi:
                ok, frame = cap.read()
                if not ok:
                    break
                names = frame_saves.get(current)
                if names:
                    enc_ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
                    if enc_ok:
                        data = buf.tobytes()
                        for name in names:
                            upload_futures.append(_upload_pool.submit(
                                _upload_jpeg, settings.minio_bucket_snapshots, name, data))
                current += 1
            cap.release()

        # ── Always keep at least one snapshot per clip (dwell substrate) ──────
        # If nothing was detected, save one scene keyframe (middle motion frame)
        # so even empty triggers leave an image to answer "how long has that
        # unclassified thing been there?".
        scene_snapshot_path = None
        if not detections:
            # Middle motion frame, or frame 0 when MD found no motion at all
            # (a false trigger) — those are exactly the clips we still want a
            # scene image for (dwell on unclassified objects).
            target = motion_frames[len(motion_frames) // 2] if motion_frames else 0
            cap = cv2.VideoCapture(video_path)
            current = 0
            keyframe = None
            while current <= target:
                ok, f = cap.read()
                if not ok:
                    break
                if current == target:
                    keyframe = f
                    break
                current += 1
            cap.release()
            if keyframe is not None:
                scene_snapshot_path = f"{job_id}/scene.jpg"
                upload_futures.append(_upload_pool.submit(
                    _upload_best_shot,
                    settings.minio_bucket_snapshots,
                    scene_snapshot_path,
                    keyframe.copy(),
                ))

        # Confirm every snapshot upload has landed BEFORE publishing "done", so a
        # worker killed/recycled mid-job can't leave the DB referencing a snapshot
        # that never uploaded. Until the final message is published+acked the job
        # stays unacknowledged and RabbitMQ redelivers it — no silent snapshot loss.
        for fut in upload_futures:
            try:
                fut.result(timeout=60)
            except Exception:
                log.warning("oc_snapshot_upload_incomplete", job_id=job_id)

        # ── Publish ONE message with all detections bundled ───────────────────
        # Annotate each detection with its best-shot path.
        best_shot_map = {
            tid: f"{job_id}/track_{tid:06d}_best.jpg"
            for tid in best_candidates
        }
        for det in detections:
            det["snapshot_path"] = best_shot_map.get(det["track_id"])
            # snapshot_bbox must be the bbox of the BEST frame (the one the snapshot
            # shows), set on that detection ONLY. The old `tid in best_candidates`
            # test was true for every detection, so the result consumer's
            # last-write-wins left the track's snapshot_bbox at the LAST position —
            # the card then auto-zoomed to where the object isn't in the best-shot.
            det["snapshot_bbox"] = (det["bbox"]
                                    if best_candidates.get(det["track_id"]) is det
                                    else None)

        ch.basic_publish(
            exchange="",
            routing_key=settings.queue_oc_results,
            body=json.dumps({
                "job_id":             job_id,
                "is_final":           True,
                "detections":         detections,
                "worker_id":          WORKER_ID,
                "osd_camera_name":    osd_camera_name,
                "osd_recorded_at":    osd_recorded_at,
                "scene_snapshot_path": scene_snapshot_path,
            }),
            properties=pika.BasicProperties(
                delivery_mode=2, content_type="application/json"),
        )

        log.info("oc_job_complete",
                 job_id=job_id, detections=len(detections), worker=WORKER_ID)
        ch.basic_ack(delivery_tag=method.delivery_tag)

    except Exception as exc:
        log.exception("oc_job_error", job_id=job_id)
        # Notify orchestrator so the job is marked failed in the DB
        try:
            ch.basic_publish(
                exchange="",
                routing_key=settings.queue_oc_results,
                body=json.dumps({
                    "job_id":    job_id,
                    "status":    "failed",
                    "worker_id": WORKER_ID,
                    "error":     str(exc),
                }),
                properties=pika.BasicProperties(delivery_mode=2, content_type="application/json"),
            )
        except Exception:
            pass
        try:
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
        except Exception:
            pass


def main():
    setproctitle.setproctitle(f"sentinel-oc-worker [{WORKER_ID}]")
    settings = get_settings()
    log.info("oc_worker_starting",
             worker_id=WORKER_ID,
             rabbitmq_host=settings.rabbitmq_host,
             queue=settings.queue_motion_results,
             gpu=settings.oc_use_gpu,
             model=settings.yolo_model_path)

    # Pre-load model (TRT export happens here on first run if needed)
    get_model()

    conn, ch = _connect(settings)

    # Worker lifecycle events (separate pika connection, own heartbeat thread)
    from worker.worker_events import WorkerEventPublisher
    device = "gpu" if settings.oc_use_gpu else "cpu"
    events = WorkerEventPublisher(WORKER_ID, "oc", device, settings)
    events.online()

    _shutdown = False

    def _handle_sigterm(signum, frame):
        nonlocal _shutdown
        log.info("oc_worker_sigterm_received", worker_id=WORKER_ID)
        _shutdown = True
        events.offline()
        try:
            ch.stop_consuming()
        except Exception:
            pass

    signal.signal(signal.SIGTERM, _handle_sigterm)
    signal.signal(signal.SIGINT, _handle_sigterm)

    def on_message(ch, method, _props, body):
        try:
            msg = json.loads(body)
            process_job(msg, ch, method)
        except Exception:
            log.exception("oc_message_parse_error")
            try:
                ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            except Exception:
                pass

    ch.basic_consume(queue=settings.queue_motion_results,
                     on_message_callback=on_message)
    log.info("oc_worker_consuming",
             queue=settings.queue_motion_results, worker_id=WORKER_ID)

    while not _shutdown:
        try:
            ch.start_consuming()
        except (pika.exceptions.AMQPConnectionError, pika.exceptions.AMQPError,
                pika.exceptions.StreamLostError, pika.exceptions.ChannelWrongStateError):
            if _shutdown:
                break
            log.warning("oc_worker_reconnecting", worker_id=WORKER_ID)
            time.sleep(5)
            try:
                conn.close()
            except Exception:
                pass
            conn, ch = _connect(settings)
            ch.basic_consume(queue=settings.queue_motion_results,
                             on_message_callback=on_message)

    log.info("oc_worker_stopped", worker_id=WORKER_ID)
    _upload_pool.shutdown(wait=True)
    try:
        conn.close()
    except Exception:
        pass


if __name__ == "__main__":
    main()
