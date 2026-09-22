import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np  # noqa: E402

from flyflirt.config import Config  # noqa: E402
from flyflirt.connectome import load_connectome  # noqa: E402
from flyflirt.engine import EngineConfig, RoomEngine  # noqa: E402
from tools.calibrate import COLD, LOWKEY, MIXED, WARM, p  # noqa: E402

conn = load_connectome(Config.CONNECTOME_PATH)
FUNNY = p(warmth=0.3, humor=0.9, reciprocity=0.4, curiosity=0.2, disclosure=0.1, energy=0.8)
CURIOUS = p(warmth=0.4, humor=0.1, reciprocity=0.6, curiosity=0.9, disclosure=0.1, energy=0.3)
SHARING = p(warmth=0.6, humor=0.1, reciprocity=0.5, curiosity=0.2, disclosure=0.9, energy=0.2)


def meters(seq, cfg):
    eng = RoomEngine(conn, cfg)
    out = []
    for params in seq:
        out.append(eng.step(params).trace.meter)
    return eng, out


def overlap(cfg, thr=0.12):
    sets = {}
    for label, params in (("funny", FUNNY), ("curious", CURIOUS), ("sharing", SHARING)):
        eng = RoomEngine(conn, cfg)
        for _ in range(4):
            eng.step(params)
        sets[label] = set(np.where(np.abs(eng.a) >= thr)[0].tolist())
    a, b, c = sets["funny"], sets["curious"], sets["sharing"]
    return {k: len(v) for k, v in sets.items()}, len(a & b), len(a & c), len(b & c)


for gain in (3.0, 4.0):
    for theta in (0.0, 0.1, 0.2, 0.3):
        cfg = EngineConfig(gain=gain, threshold=theta)
        print(f"=== gain {gain} theta {theta}")
        for label, seq in (("warm", [WARM] * 20), ("mixed", MIXED[:20]), ("lowkey", [LOWKEY] * 20), ("cold", [COLD] * 20)):
            eng, m = meters(seq, cfg)
            act = int((eng.peak_abs >= 0.10).sum())
            edges = int((eng.peak_flux >= 0.03).sum())
            print(f"{label:>7}: " + " ".join(f"{v:.2f}" for v in m[::2]) + f" | cells>=0.10: {act:4d} edges>=0.03: {edges:5d}")
        print("   footprint sizes, overlaps (funny/curious, funny/sharing, curious/sharing):", overlap(cfg))
