"""
Scaling policy — the pure decision function (no Docker, no broker, no I/O).

Kept dependency-free on purpose so it is trivially unit-testable and so the
"what should we do" logic is separate from the "how do we do it" mechanism in
governor.py / supervisor.py.

Decision priority each cycle:
  1. Pressure (load high or swap high) → PARK one (relieve the host first).
  2. Below per-type minimum → START one (keep a puller alive so work can flow).
  3. Headroom + demand → START one of the most-wanted type that still fits the
     shared core pool.
  4. Otherwise hold.
"""
from __future__ import annotations

from dataclasses import dataclass

from agent.budget import Budget
from agent.config import Settings
from agent.resources import Resources


@dataclass
class Decision:
    action: str          # "start" | "park" | "hold"
    worker_type: str | None
    reason: str
    emergency: bool = False   # bypasses cooldown (swap distress)


def _cost(s: Settings, t: str) -> float:
    return s.oc_cost_cores if t == "oc" else s.md_cost_cores


def _cap(b: Budget, t: str) -> int:
    return b.oc_cap if t == "oc" else b.md_cap


def _min(s: Settings, t: str) -> int:
    return s.oc_min if t == "oc" else s.md_min


def decide(s: Settings, res: Resources, b: Budget,
           dem: dict[str, int | None], counts: dict[str, int]) -> Decision:
    load = res.load_per_core
    committed = counts["oc"] * s.oc_cost_cores + counts["md"] * s.md_cost_cores

    # 1. Pressure → park one. Swap distress is an emergency (bypasses cooldown).
    swap_distress = res.swap_used_pct >= s.swap_high_pct
    if swap_distress or load >= s.load_high:
        # Park the type with the least demand first; tie → OC (heaviest relief).
        order = sorted(("oc", "md"), key=lambda t: ((dem.get(t) or 0), 0 if t == "oc" else 1))
        for t in order:
            if counts[t] > 0 and (counts[t] > _min(s, t) or swap_distress):
                why = "swap_distress" if swap_distress else "load_high"
                return Decision("park", t,
                                f"{why} load/core={load:.2f} swap={res.swap_used_pct:.0f}%",
                                emergency=swap_distress)
        return Decision("hold", None, "pressure_but_at_minimum")

    # 2. Below minimum → ensure a puller exists (demand-independent).
    for t in ("md", "oc"):
        if counts[t] < _min(s, t) and counts[t] < _cap(b, t) and committed + _cost(s, t) <= b.core_pool:
            return Decision("start", t, f"below_min ({counts[t]}<{_min(s, t)})")

    # 3. Headroom + demand → start the most-wanted type that still fits the core pool.
    if load < s.load_low:
        wanted = sorted(("oc", "md"), key=lambda t: (dem.get(t) or 0), reverse=True)
        for t in wanted:
            d = dem.get(t)
            if d and d > 0 and counts[t] < _cap(b, t) and committed + _cost(s, t) <= b.core_pool:
                return Decision("start", t, f"demand={d} load/core={load:.2f} headroom")

    return Decision("hold", None, f"steady load/core={load:.2f}")
