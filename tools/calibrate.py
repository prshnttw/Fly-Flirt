"""Inspect and calibrate the simulation on the real connectome.

    python tools/calibrate.py            # behaviour report with current defaults
    python tools/calibrate.py --sweep    # sweep SIM_GAIN and print the pacing table
"""
import argparse
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np  # noqa: E402

from flyflirt.config import Config  # noqa: E402
from flyflirt.connectome import load_connectome  # noqa: E402
from flyflirt.engine import EngineConfig, RoomEngine  # noqa: E402

PARAMS = ["warmth", "humor", "reciprocity", "curiosity", "disclosure", "energy", "tension"]


def p(**kw):
    base = {k: 0.0 for k in PARAMS}
    base.update(kw)
    return base


WARM = p(warmth=0.8, humor=0.7, reciprocity=0.8, curiosity=0.6, disclosure=0.5, energy=0.7, tension=0.05)
MIXED = [
    p(warmth=0.7, humor=0.5, reciprocity=0.6, curiosity=0.3, disclosure=0.2, energy=0.4),
    p(warmth=0.2, humor=0.0, reciprocity=0.2, curiosity=0.1, disclosure=0.0, energy=0.1, tension=0.1),
    p(warmth=0.8, humor=0.6, reciprocity=0.7, curiosity=0.7, disclosure=0.5, energy=0.6),
    p(warmth=0.4, humor=0.1, reciprocity=0.5, curiosity=0.2, disclosure=0.1, energy=0.2, tension=0.1),
] * 5
COLD = p(warmth=0.05, humor=0.0, reciprocity=0.05, curiosity=0.0, disclosure=0.0, energy=0.05, tension=0.6)
LOWKEY = p(warmth=0.35, humor=0.15, reciprocity=0.4, curiosity=0.2, disclosure=0.1, energy=0.2, tension=0.05)


def run(conn, cfg, seq, label, verbose=False):
    eng = RoomEngine(conn, cfg)
    meters = []
    t0 = time.time()
    for params in seq:
        r = eng.step(params)
        meters.append(r.trace.meter)
    dt = (time.time() - t0) / max(len(seq), 1) * 1000
    print(f"{label:>8}: " + " ".join(f"{m:.2f}" for m in meters[:20]) + f"   | {dt:.0f} ms/msg  cells={eng.traces[-1].cells_active} edges={eng.traces[-1].edges_active}")
    if verbose:
        r = eng.traces[-1]
        print("   top types:", r.top_types)
    return eng


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sweep", action="store_true")
    args = ap.parse_args()
    conn = load_connectome(Config.CONNECTOME_PATH)
    print("connectome:", conn.summary())
    print("channels:", {c.name: int(c.cells.size) for c in conn.channels})

    if args.sweep:
        for gain in (2.0, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0):
            for ig in (1.6,):
                cfg = EngineConfig(gain=gain, input_gain=ig)
                print(f"--- gain={gain} input_gain={ig}")
                run(conn, cfg, [WARM] * 20, "warm")
                run(conn, cfg, MIXED, "mixed")
                run(conn, cfg, [LOWKEY] * 20, "lowkey")
                run(conn, cfg, [COLD] * 20, "cold")
        return

    cfg = EngineConfig.from_config(Config)
    print(f"--- defaults gain={cfg.gain} threshold={cfg.threshold} meter_scale={cfg.meter_scale}")
    eng = run(conn, cfg, [WARM] * 20, "warm", verbose=True)
    run(conn, cfg, MIXED, "mixed")
    run(conn, cfg, [LOWKEY] * 20, "lowkey")
    run(conn, cfg, [COLD] * 20, "cold")

    # sparsity / magnitude of the live frames
    eng = RoomEngine(conn, cfg)
    for _ in range(3):
        res = eng.step(WARM)
    fr = res.frames[-1]
    print("last frame: cells", len(fr["cells"]), "edges", len(fr["edges"]), "max act", max(fr["acts"]) if fr["acts"] else 0)
    print("state snapshot cells:", len(res.state_idx), "max", max(res.state_val) if res.state_val else 0)


if __name__ == "__main__":
    main()
