"""
One-off backfill: re-run track classification on all completed jobs.

Historical tracks were mislabeled "stationary" because _classify_tracks ran
against an autoflush=False session before the Detection rows were flushed
(see fix in result_consumer.py). All detections are now committed, so a fresh
session re-running the same classification logic produces correct results.

Run inside the orchestrator container:
    docker exec -i sentinel-orchestrator bash -c 'cd /app && python -' < scripts/backfill_classify.py
"""
from app.db import SessionLocal
from app.models.job import Job, JobStatus
from app.services.result_consumer import _classify_tracks


def main():
    db = SessionLocal()
    try:
        job_ids = [
            j.id for j in db.query(Job.id)
            .filter(Job.status == JobStatus.completed)
            .order_by(Job.id)
            .all()
        ]
    finally:
        db.close()

    print(f"backfill: {len(job_ids)} completed jobs to re-classify")

    done = 0
    for jid in job_ids:
        db = SessionLocal()
        try:
            _classify_tracks(db, jid)
            db.commit()
            done += 1
            if done % 50 == 0:
                print(f"  ...{done}/{len(job_ids)} jobs")
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            print(f"  job {jid} FAILED: {exc}")
        finally:
            db.close()

    print(f"backfill complete: {done}/{len(job_ids)} jobs re-classified")


if __name__ == "__main__":
    main()
