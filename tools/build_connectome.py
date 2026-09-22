"""One-time offline build of the cell-level connectome used by the live app.

Run manually (needs NEUPRINT_TOKEN in .env). The running app never calls
neuPrint; it only reads static/connectome.json produced here.

    python tools/build_connectome.py

Selection (all real MaleCNS v1.0 cells):
  * accumulator : every pC1_* cell (the courtship arousal hub)
  * output      : pIP10, pMP2 (descending output neurons)
  * brake       : mAL_m1, mAL_m8 (GABAergic, strongest upstream partners of pC1)
  * input       : LC16 (top cells by synapses onto pC1), AVLP732m, AVLP733m,
                  LH003m, FLA001m, AN08B020 (excitatory upstream partners of pC1)
  * context     : the strongest one-hop partners of the above, so activity can
                  spread outward into real neighbouring circuitry
"""
import json
import os
import sys
import time

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from neuprint import Client, NeuronCriteria as NC, fetch_adjacencies, fetch_custom, fetch_neurons

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, ".env"))

# Two presets: the default subgraph (1,350 cells) and a bigger "full pathway" (more one-hop context).
#   python tools/build_connectome.py                       -> static/connectome.json
#   python tools/build_connectome.py --context 4000 --out static/connectome_full.json
import argparse

_ap = argparse.ArgumentParser()
_ap.add_argument("--context", type=int, default=1050, help="number of context cells to recruit")
_ap.add_argument("--out", default=os.path.join(ROOT, "static", "connectome.json"))
_args = _ap.parse_args()
OUT_PATH = _args.out
CACHE = os.path.join(ROOT, "tools", "cache")
os.makedirs(CACHE, exist_ok=True)

BRAKE_TYPES = ["mAL_m1", "mAL_m8"]
INPUT_TYPES = ["AVLP732m", "AVLP733m", "LH003m", "FLA001m", "AN08B020"]
LC16_KEEP = 80
OUTPUT_TYPES = ["pIP10", "pMP2"]
CONTEXT_KEEP = _args.context
MIN_EDGE_WEIGHT = 2
MAX_IN_EDGES_PER_CELL = 40

ROLES = ["context", "brake", "input", "accumulator", "output"]
NT_CODES = ["unknown", "acetylcholine", "gaba", "glutamate", "dopamine", "serotonin", "octopamine"]

# LLM parameter -> input populations. The mapping is a modelling choice (documented
# in the UI); the populations, their neurotransmitters and their synapses are real.
CHANNELS = [
    {"name": "brake_m1", "cells_of": ["mAL_m1"], "params": {"warmth": 1.0, "tension": -1.0}},
    {"name": "brake_m8", "cells_of": ["mAL_m8"], "params": {"reciprocity": 1.0, "tension": -1.0}},
    {"name": "banter", "cells_of": ["AVLP732m", "AVLP733m"], "params": {"humor": 1.0}},
    {"name": "attention", "cells_of": ["LC16"], "params": {"curiosity": 1.0}},
    {"name": "confiding", "cells_of": ["LH003m"], "params": {"disclosure": 1.0}},
    {"name": "vigor", "cells_of": ["FLA001m"], "params": {"energy": 1.0}},
    {"name": "feedback", "cells_of": ["AN08B020"], "params": {"reciprocity": 1.0}},
]


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def cypher_list(values):
    return "[" + ",".join(f'"{v}"' if isinstance(v, str) else str(int(v)) for v in values) + "]"


def main():
    client = Client("https://neuprint.janelia.org", dataset="male-cns:v1.0", token=os.environ["NEUPRINT_TOKEN"])

    log("resolving pC1 subtypes")
    pc1_types = fetch_custom(
        'MATCH (n:Neuron) WHERE n.type =~ "pC1.*" RETURN DISTINCT n.type AS type ORDER BY type', client=client
    )["type"].tolist()
    log(f"  {len(pc1_types)} pC1 subtypes")

    core_types = pc1_types + BRAKE_TYPES + INPUT_TYPES + OUTPUT_TYPES
    log("fetching core cells")
    core, _ = fetch_neurons(NC(type=core_types), client=client)
    core = core[["bodyId", "type", "somaLocation", "predictedNt"]].copy()

    log("selecting LC16 cells by synapses onto pC1")
    lc16 = fetch_custom(
        f"""
        MATCH (s:Neuron)-[e:ConnectsTo]->(t:Neuron)
        WHERE s.type = "LC16" AND t.type =~ "pC1.*"
        RETURN s.bodyId AS bodyId, sum(e.weight) AS w ORDER BY w DESC LIMIT {LC16_KEEP}
        """,
        client=client,
    )
    lc16_cells, _ = fetch_neurons(NC(bodyId=lc16["bodyId"].tolist()), client=client)
    lc16_cells = lc16_cells[["bodyId", "type", "somaLocation", "predictedNt"]].copy()
    core = pd.concat([core, lc16_cells], ignore_index=True).drop_duplicates("bodyId")
    core = core.dropna(subset=["somaLocation"])

    def role_of(t):
        if t in BRAKE_TYPES:
            return "brake"
        if t in INPUT_TYPES or t == "LC16":
            return "input"
        if t in OUTPUT_TYPES:
            return "output"
        return "accumulator"

    core["role"] = core["type"].map(role_of)
    core_ids = core["bodyId"].tolist()
    log(f"  core cells: {len(core_ids)}  " + str(core["role"].value_counts().to_dict()))

    log("finding strongest one-hop partners for context recruitment")
    partners = fetch_custom(
        f"""
        MATCH (s:Neuron)-[e:ConnectsTo]-(n:Neuron)
        WHERE s.bodyId IN {cypher_list(core_ids)} AND NOT n.bodyId IN {cypher_list(core_ids)}
        RETURN n.bodyId AS bodyId, sum(e.weight) AS w ORDER BY w DESC LIMIT {CONTEXT_KEEP * 2}
        """,
        client=client,
    )
    ctx_ids = partners["bodyId"].tolist()
    ctx, _ = fetch_neurons(NC(bodyId=ctx_ids), client=client)
    ctx = ctx[["bodyId", "type", "somaLocation", "predictedNt"]].copy()
    ctx = ctx.dropna(subset=["somaLocation"])
    strength = dict(zip(partners["bodyId"], partners["w"]))
    ctx["w"] = ctx["bodyId"].map(strength)
    ctx = ctx.sort_values("w", ascending=False).head(CONTEXT_KEEP).drop(columns="w")
    ctx["role"] = "context"
    log(f"  context cells: {len(ctx)}")

    cells = pd.concat([core, ctx], ignore_index=True).dropna(subset=["somaLocation"]).reset_index(drop=True)
    cells["type"] = cells["type"].fillna("unclassified")
    all_ids = cells["bodyId"].tolist()
    log(f"total cells with soma coordinates: {len(all_ids)}")

    adj_cache = os.path.join(CACHE, "adjacency.parquet" if CONTEXT_KEEP == 1050 else f"adjacency_{CONTEXT_KEEP}.parquet")
    if os.path.exists(adj_cache):
        log("using cached adjacency")
        conns = pd.read_parquet(adj_cache)
    else:
        log("fetching cell-level adjacency (this is the slow step)")
        _, conns = fetch_adjacencies(NC(bodyId=all_ids), NC(bodyId=all_ids), min_total_weight=1, client=client)
        conns = conns.groupby(["bodyId_pre", "bodyId_post"], as_index=False)["weight"].sum()
        conns.to_parquet(adj_cache)
    log(f"  raw cell pairs: {len(conns)}")

    conns = conns[(conns["weight"] >= MIN_EDGE_WEIGHT) & (conns["bodyId_pre"] != conns["bodyId_post"])]
    conns = (
        conns.sort_values("weight", ascending=False)
        .groupby("bodyId_post", group_keys=False)
        .head(MAX_IN_EDGES_PER_CELL)
        .reset_index(drop=True)
    )
    log(f"  kept edges: {len(conns)}")

    index = {bid: i for i, bid in enumerate(all_ids)}
    conns = conns[conns["bodyId_pre"].isin(index) & conns["bodyId_post"].isin(index)]

    coords = np.array([list(loc) for loc in cells["somaLocation"]], dtype=float)
    center = coords.mean(axis=0)
    scale = np.abs(coords - center).max()
    pos = ((coords - center) / scale).round(4)

    types = sorted(cells["type"].unique().tolist())
    type_idx = {t: i for i, t in enumerate(types)}
    nt_idx = {n: i for i, n in enumerate(NT_CODES)}

    channels_out = []
    for ch in CHANNELS:
        members = cells.index[cells["type"].isin(ch["cells_of"]) & cells["role"].isin(["brake", "input"])].tolist()
        channels_out.append({"name": ch["name"], "params": ch["params"], "cells": members})

    payload = {
        "version": 1,
        "meta": {
            "dataset": "male-cns:v1.0",
            "source": "neuPrint (Janelia FlyEM)",
            "generated": time.strftime("%Y-%m-%d"),
            "cells": len(cells),
            "edges": int(len(conns)),
            "min_edge_weight": MIN_EDGE_WEIGHT,
            "max_in_edges_per_cell": MAX_IN_EDGES_PER_CELL,
        },
        "roles": ROLES,
        "nts": NT_CODES,
        "types": types,
        "cells": {
            "id": [int(b) for b in cells["bodyId"]],
            "type": [type_idx[t] for t in cells["type"]],
            "role": [ROLES.index(r) for r in cells["role"]],
            "nt": [nt_idx.get(n, 0) if isinstance(n, str) else 0 for n in cells["predictedNt"]],
            "pos": pos.flatten().tolist(),
        },
        "edges": {
            "src": [index[b] for b in conns["bodyId_pre"]],
            "dst": [index[b] for b in conns["bodyId_post"]],
            "w": [int(w) for w in conns["weight"]],
        },
        "channels": channels_out,
    }
    with open(OUT_PATH, "w") as f:
        json.dump(payload, f, separators=(",", ":"))
    size_kb = os.path.getsize(OUT_PATH) / 1024
    log(f"wrote {OUT_PATH}  ({size_kb:.0f} KB)  cells={len(cells)} edges={len(conns)}")
    log("roles: " + str(cells["role"].value_counts().to_dict()))
    log("channels: " + ", ".join(f"{c['name']}={len(c['cells'])}" for c in channels_out))


if __name__ == "__main__":
    sys.exit(main())
