import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np  # noqa: E402

from flyflirt.config import Config  # noqa: E402
from flyflirt.connectome import load_connectome  # noqa: E402

conn = load_connectome(Config.CONNECTOME_PATH)
W = conn.W.astype(np.float64)
ev = np.linalg.eigvals(W)
print("n", conn.n)
print("max Re(lambda):", ev.real.max(), " min Re:", ev.real.min())
print("max |lambda|:", np.abs(ev).max())
order = np.argsort(-np.abs(ev))[:8]
print("top |lambda|:", [(round(float(ev[i].real), 3), round(float(ev[i].imag), 3)) for i in order])
order = np.argsort(-ev.real)[:5]
print("top Re(lambda):", [(round(float(ev[i].real), 3), round(float(ev[i].imag), 3)) for i in order])

# what fraction of weight mass is inhibitory, and where does it come from
inh = conn.edge_w < 0
print("inhibitory edges:", int(inh.sum()), "of", conn.n_edges)
for role in conn.roles:
    mask = conn.role[conn.src] == conn.roles.index(role)
    print(f"  edges from {role:12s}: {int(mask.sum()):6d}  inhibitory share of |w|: {float(conn.edge_abs[mask & inh].sum() / max(conn.edge_abs[mask].sum(), 1e-9)):.2f}")

# hub connectivity: how much of the accumulator's input comes from inputs vs context vs itself
acc = set(conn.accumulator.tolist())
into = np.isin(conn.dst, list(acc))
for role in conn.roles:
    m = into & (conn.role[conn.src] == conn.roles.index(role))
    print(f"  into accumulator from {role:12s}: {int(m.sum()):6d} edges, sum|w|={float(conn.edge_abs[m].sum()):.1f}, net w={float(conn.edge_w[m].sum()):.1f}")
