"""
Worker budget.

Translates a resource snapshot into "how many workers of each type may this
machine run." MD and OC workers compete for the SAME physical cores, so the
budget is a shared core pool plus per-type RAM/VRAM ceilings — the governor
decides the actual mix within that pool.

This is where the session-16 lesson is encoded: the committed cores of all
running workers must never exceed (physical_cores - reserve_cores). Reserve
leaves room for the OS and co-tenants (Frigate, Ollama).
"""
from __future__ import annotations

from dataclasses import dataclass

from agent.config import Settings
from agent.resources import Resources


@dataclass
class Budget:
    core_pool: float        # cores available to Sentinel workers (shared MD+OC)
    oc_cap: int             # max OC workers (RAM/VRAM/config bound, before core check)
    md_cap: int             # max MD workers (RAM/config bound, before core check)

    def explain(self) -> dict:
        return {"core_pool": round(self.core_pool, 1), "oc_cap": self.oc_cap, "md_cap": self.md_cap}


def compute_budget(res: Resources, s: Settings) -> Budget:
    core_pool = max(0.0, res.physical_cores - s.reserve_cores)

    # OC ceiling: RAM, VRAM, and configured hard max.
    oc_by_ram = res.ram_available_mb // s.oc_cost_ram_mb if s.oc_cost_ram_mb else s.oc_max
    if res.gpus:
        free_vram = max(g.mem_free_mb for g in res.gpus)
        oc_by_vram = free_vram // s.oc_cost_vram_mb if s.oc_cost_vram_mb else s.oc_max
    else:
        oc_by_vram = 0  # no GPU → no GPU OC workers on this node
    oc_cap = min(s.oc_max, int(oc_by_ram), int(oc_by_vram))

    # MD ceiling: RAM and configured hard max.
    md_by_ram = res.ram_available_mb // s.md_cost_ram_mb if s.md_cost_ram_mb else s.md_max
    md_cap = min(s.md_max, int(md_by_ram))

    return Budget(core_pool=core_pool, oc_cap=oc_cap, md_cap=md_cap)
