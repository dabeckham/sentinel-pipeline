"""
Unit tests for the governor's pure decision function.

No host, no Docker, no broker — just the logic. Run:
    cd node-agent && python -m pytest tests/ -q
or without pytest:
    cd node-agent && python tests/test_decide.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.config import Settings
from agent.budget import Budget
from agent.policy import decide
from agent.resources import Resources, Gpu


def _res(load1, physical=8, swap=0.0, ram_avail=16000):
    return Resources(
        physical_cores=physical, logical_cores=physical * 2,
        load1=load1, load5=load1, load15=load1,
        ram_total_mb=32000, ram_available_mb=ram_avail,
        swap_used_pct=swap, swap_in_bytes=0,
        gpus=[Gpu(1, "RTX 3060", 12000, 11000, 5.0)],
    )

# Convenience: most tests have no swap-in activity.
NO_SWAP = 0.0
THRASH = 50.0   # MB/s, well above swap_in_high_mb_s


# Reserve 3 of 8 cores → core_pool 5; cost 1.5/worker → ~3 workers max.
S = Settings(reserve_cores=3, oc_min=1, md_min=1, load_high=0.90, load_low=0.65, swap_in_high_mb_s=5.0)
B = Budget(core_pool=5.0, oc_cap=8, md_cap=8)


def test_load_high_parks():
    d = decide(S, _res(load1=8.0), B, {"oc": 5, "md": 5}, {"oc": 2, "md": 1}, NO_SWAP)
    assert d.action == "park", d
    assert not d.emergency


def test_swap_distress_is_emergency_and_can_go_below_min():
    # Only 1 oc (== min) but actively paging in → still parks (ignores min), emergency.
    d = decide(S, _res(load1=2.0), B, {"oc": 0, "md": 0}, {"oc": 1, "md": 0}, THRASH)
    assert d.action == "park" and d.worker_type == "oc"
    assert d.emergency


def test_full_but_idle_swap_does_not_block_starts():
    # The observe-mode bug: swap 82% FULL but no paging activity → must NOT be
    # treated as pressure; with headroom + below-min it should START.
    d = decide(S, _res(load1=0.4, swap=82.0), B, {"oc": 0, "md": 0}, {"oc": 0, "md": 0}, NO_SWAP)
    assert d.action == "start", d


def test_below_min_starts_even_without_demand():
    d = decide(S, _res(load1=0.5), B, {"oc": 0, "md": 0}, {"oc": 0, "md": 0}, NO_SWAP)
    assert d.action == "start"
    assert d.worker_type in ("md", "oc")


def test_headroom_plus_demand_starts_most_wanted():
    # Both at min (1), headroom, OC has more demand → start OC.
    d = decide(S, _res(load1=1.0), B, {"oc": 50, "md": 2}, {"oc": 1, "md": 1}, NO_SWAP)
    assert d.action == "start" and d.worker_type == "oc", d


def test_core_pool_not_oversubscribed():
    # committed = 2*1.5 + 1*1.5 = 4.5; adding 1.5 → 6.0 > core_pool 5 → must hold.
    # This is the session-16 guard: never exceed physical-minus-reserve cores.
    d = decide(S, _res(load1=1.0), B, {"oc": 99, "md": 99}, {"oc": 2, "md": 1}, NO_SWAP)
    assert d.action == "hold", d


def test_no_demand_at_min_holds():
    d = decide(S, _res(load1=1.0), B, {"oc": 0, "md": 0}, {"oc": 1, "md": 1}, NO_SWAP)
    assert d.action == "hold", d


def test_unknown_demand_does_not_scale_up():
    # broker unreachable (None) → don't guess; hold at min.
    d = decide(S, _res(load1=0.5), B, {"oc": None, "md": None}, {"oc": 1, "md": 1}, NO_SWAP)
    assert d.action == "hold", d


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\n{len(fns)} passed")
