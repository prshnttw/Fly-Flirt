import itertools
import math
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
TYPICAL = [p(warmth=0.5, humor=0.25, reciprocity=0.5, curiosity=0.35, disclosure=0.2, energy=0.4, tension=0.05)] * 20


def hub_series(seq, cfg):
    eng = RoomEngine(conn, cfg)
    hubs = []
    for params in seq:
        eng.step(params)
        hubs.append(eng.hub_level())
    return eng, hubs


def jaccard(a, b):
    return len(a & b) / max(len(a | b), 1)


rows = []
for gain, theta in itertools.product((3.5, 4.0, 4.5), (0.06, 0.10, 0.14, 0.18)):
    cfg = EngineConfig(gain=gain, threshold=theta)
    _, warm_h = hub_series([WARM] * 20, cfg)
    if warm_h[9] <= 1e-4:
        continue
    scale = warm_h[9] / math.atanh(0.65)
    def meters(seq):
        _, h = hub_series(seq, cfg)
        return [math.tanh(v / scale) for v in h]
    mixed = meters(MIXED[:20])
    low = meters([LOWKEY] * 20)
    cold = meters([COLD] * 20)
    typ = meters(TYPICAL)
    sets = {}
    for label, params in (("funny", FUNNY), ("curious", CURIOUS), ("sharing", SHARING)):
        eng = RoomEngine(conn, cfg)
        for _ in range(4):
            eng.step(params)
        sets[label] = set(np.where(np.abs(eng.a) >= 0.12)[0].tolist())
    jac = np.mean([jaccard(sets["funny"], sets["curious"]), jaccard(sets["funny"], sets["sharing"]), jaccard(sets["curious"], sets["sharing"])])
    rows.append((gain, theta, scale, mixed[14], low[14], typ[14], cold[19], warm_h[7] / scale, jac))
    print(f"gain={gain} theta={theta:.2f} scale={scale:.3f} | @15 msgs: typical={typ[14]:.2f} mixed={mixed[14]:.2f} lowkey={low[14]:.2f} | cold@20={cold[19]:.2f} | style-footprint jaccard={jac:.2f}")
