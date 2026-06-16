"""
The load governor — the autonomous control loop (mechanism).

Each cycle it probes local load, computes the budget, reads demand, asks the
pure `policy.decide()` what to do, and applies at most ONE action — with a
cooldown so it never flaps. The machine governs itself: no central scheduler
dictates counts (the "node decides, orchestrator advises" model — the only
orchestrator input is the advisory queue-depth demand signal).
"""
from __future__ import annotations

import time

import structlog

from agent.budget import compute_budget
from agent.config import Settings
from agent import broker, resources
from agent.policy import decide
from agent.supervisor import Supervisor

log = structlog.get_logger()


class Governor:
    def __init__(self, s: Settings):
        self._s = s
        self._sup = Supervisor(s)
        self._last_action_ts = 0.0
        self._last_swap_in: int | None = None
        self._last_swap_ts: float = 0.0

    def _cooldown_ok(self) -> bool:
        return (time.time() - self._last_action_ts) >= self._s.action_cooldown_s

    def _swap_in_rate(self, res) -> float:
        """MB/s swapped IN since the last tick (0 on the first tick)."""
        now = time.time()
        rate = 0.0
        if self._last_swap_in is not None and now > self._last_swap_ts:
            delta = res.swap_in_bytes - self._last_swap_in
            if delta > 0:
                rate = (delta / (now - self._last_swap_ts)) / (1024 * 1024)
        self._last_swap_in = res.swap_in_bytes
        self._last_swap_ts = now
        return rate

    def tick(self) -> None:
        self._sup.reap()                 # clear exited (crashed/parked) workers first
        self._sup.ensure_transcode()     # keep the always-on playback transcoder alive
        res = resources.probe()
        b = compute_budget(res, self._s)
        dem = broker.demand(self._s)
        counts = self._sup.counts()
        swap_in_rate = self._swap_in_rate(res)

        d = decide(self._s, res, b, dem, counts, swap_in_rate)

        log.info("governor_tick",
                 load_per_core=round(res.load_per_core, 2),
                 swap_in_mb_s=round(swap_in_rate, 1),
                 swap_pct=round(res.swap_used_pct, 0),
                 ram_avail_mb=res.ram_available_mb,
                 budget=b.explain(), demand=dem, counts=counts,
                 decision=d.action, worker_type=d.worker_type, reason=d.reason,
                 dry_run=self._s.dry_run)

        if d.action == "hold":
            return
        if not (self._cooldown_ok() or d.emergency):
            log.info("governor_cooldown", suppressed=d.action, worker_type=d.worker_type)
            return

        if d.action == "start":
            self._sup.start(d.worker_type)
        elif d.action == "park":
            self._sup.park(d.worker_type)
        self._last_action_ts = time.time()

    def run(self) -> None:
        log.info("governor_starting", node=self._s.node_name,
                 interval_s=self._s.governor_interval_s, dry_run=self._s.dry_run)
        # Adopt any workers already running (survive an agent restart).
        log.info("governor_adopted", counts=self._sup.counts())
        while True:
            try:
                self.tick()
            except Exception:
                log.exception("governor_tick_error")
            time.sleep(self._s.governor_interval_s)
