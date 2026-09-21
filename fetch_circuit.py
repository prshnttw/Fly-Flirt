import json
import os

import numpy as np
from dotenv import load_dotenv
from neuprint import Client, NeuronCriteria as NC, fetch_adjacencies, fetch_custom, fetch_neurons

load_dotenv()

# Real MaleCNS v1.0 type names, confirmed against the live dataset (2026-09-20).
# The original spec's "PPN1" does not exist in this dataset — mAL_m1/mAL_m8
# are the real top upstream partners of pC1 (GABAergic, i.e. inhibitory), so
# they stand in as the sensory relay. "P1" is really "pC1" here, split into
# ~49 numbered subtypes; we keep the top 20 by pC1->pIP10 output weight to
# match the spec's "roughly 20 neurons" scale and keep the demo diagram legible.
SENSORY_TYPES = ["mAL_m1", "mAL_m8"]
ACCUMULATOR_TYPES = [
    "pC1_14a", "pC1_5b", "pC1_7b", "pC1_19", "pC1_7a", "pC1_11b", "pC1_14b",
    "pC1x_c", "pC1_5a", "pC1_16a", "pC1_13a", "pC1_15c", "pC1_17a", "pC1_15a",
    "pC1_10b", "pC1_13b", "pC1x_d", "pC1_15b", "pC1_6a", "pC1_2a",
]
OUTPUT_TYPES = ["pIP10"]
ALL_TYPES = SENSORY_TYPES + ACCUMULATOR_TYPES + OUTPUT_TYPES


def role_of(cell_type):
    if cell_type in SENSORY_TYPES:
        return "sensory"
    if cell_type in ACCUMULATOR_TYPES:
        return "accumulator"
    return "output"


def main():
    client = Client(
        "https://neuprint.janelia.org",
        dataset="male-cns:v1.0",
        token=os.environ["NEUPRINT_TOKEN"],
    )

    neurons, conns = fetch_adjacencies(NC(type=ALL_TYPES), NC(type=ALL_TYPES), client=client)
    neurons.to_csv("neurons.csv", index=False)
    conns.to_csv("connections.csv", index=False)

    type_list = ", ".join(f'"{t}"' for t in ALL_TYPES)
    nt_df = fetch_custom(
        f"""
        MATCH (n:Neuron)
        WHERE n.type IN [{type_list}]
        RETURN n.bodyId AS bodyId, n.predictedNt AS predictedNt
        """,
        client=client,
    )
    nt_by_body = dict(zip(nt_df["bodyId"], nt_df["predictedNt"]))
    neurons["predictedNt"] = neurons["bodyId"].map(nt_by_body)

    nt_by_type = (
        neurons.groupby("type")["predictedNt"]
        .agg(lambda s: s.mode().iat[0] if not s.mode().empty else None)
        .to_dict()
    )

    body_to_type = dict(zip(neurons["bodyId"], neurons["type"]))
    conns = conns.copy()
    conns["type_pre"] = conns["bodyId_pre"].map(body_to_type)
    conns["type_post"] = conns["bodyId_post"].map(body_to_type)
    edge_weights = (
        conns.groupby(["type_pre", "type_post"])["weight"].sum().reset_index()
    )

    nodes = [
        {"id": t, "type": t, "role": role_of(t), "neurotransmitter": nt_by_type.get(t)}
        for t in ALL_TYPES
    ]
    edges = [
        {"from": row.type_pre, "to": row.type_post, "weight": int(row.weight)}
        for row in edge_weights.itertuples()
    ]

    circuit = {"nodes": nodes, "edges": edges}
    with open("circuit.json", "w") as f:
        json.dump(circuit, f, indent=2)

    print(f"Wrote circuit.json with {len(nodes)} nodes and {len(edges)} edges")
    for node in nodes:
        print(f"  {node['id']:>10} role={node['role']:<11} nt={node['neurotransmitter']}")

    fetch_viz_data(client)


def fetch_viz_data(client, background_limit=400, max_edges=1200):
    """
    Per-neuron (not per-type) soma coordinates for the fly-brain 3D spectator
    page: the real individual cells making up our 23 simulated types, plus a
    one-hop neighborhood of real, non-simulated background neurons for visual
    density. Written to static/circuit_viz.json (separate from circuit.json,
    which stays type-level and drives the actual simulation in circuit_sim.py).
    """
    core_neurons, _ = fetch_neurons(NC(type=ALL_TYPES), client=client)
    core_neurons = core_neurons.dropna(subset=["somaLocation"]).reset_index(drop=True)

    type_list = ", ".join(f'"{t}"' for t in ALL_TYPES)
    neighbor_df = fetch_custom(
        f"""
        MATCH (core:Neuron)-[:ConnectsTo]-(nb:Neuron)
        WHERE core.type IN [{type_list}] AND NOT nb.type IN [{type_list}]
        RETURN DISTINCT nb.bodyId AS bodyId
        LIMIT {background_limit}
        """,
        client=client,
    )
    neighbor_ids = neighbor_df["bodyId"].tolist()
    if neighbor_ids:
        bg_neurons, _ = fetch_neurons(NC(bodyId=neighbor_ids), client=client)
        bg_neurons = bg_neurons.dropna(subset=["somaLocation"]).reset_index(drop=True)
    else:
        bg_neurons = core_neurons.iloc[0:0]

    all_coords = np.array(
        [list(loc) for loc in core_neurons["somaLocation"]]
        + [list(loc) for loc in bg_neurons["somaLocation"]]
    )
    center = all_coords.mean(axis=0)
    scale = np.abs(all_coords - center).max()

    def to_nodes(df, role_fn):
        out = []
        for _, row in df.iterrows():
            xyz = (np.array(row["somaLocation"]) - center) / scale
            out.append(
                {
                    "id": str(row["bodyId"]),
                    "type": row["type"],
                    "role": role_fn(row["type"]),
                    "x": float(xyz[0]),
                    "y": float(xyz[1]),
                    "z": float(xyz[2]),
                }
            )
        return out

    nodes = to_nodes(core_neurons, role_of) + to_nodes(bg_neurons, lambda t: "background")
    role_by_id = {n["id"]: n["role"] for n in nodes}

    all_body_ids = core_neurons["bodyId"].tolist() + bg_neurons["bodyId"].tolist()
    _, viz_conns = fetch_adjacencies(NC(bodyId=all_body_ids), NC(bodyId=all_body_ids), client=client)
    viz_conns = (
        viz_conns.groupby(["bodyId_pre", "bodyId_post"])["weight"].sum().reset_index()
        if len(viz_conns)
        else viz_conns
    )
    viz_conns = viz_conns.sort_values("weight", ascending=False).head(max_edges)

    edges = [
        {
            "from": str(row.bodyId_pre),
            "to": str(row.bodyId_post),
            "from_role": role_by_id.get(str(row.bodyId_pre), "background"),
            "to_role": role_by_id.get(str(row.bodyId_post), "background"),
            "weight": int(row.weight),
        }
        for row in viz_conns.itertuples()
    ]

    os.makedirs("static", exist_ok=True)
    with open("static/circuit_viz.json", "w") as f:
        json.dump({"nodes": nodes, "edges": edges}, f)

    print(
        f"Wrote static/circuit_viz.json with {len(nodes)} neurons "
        f"({len(core_neurons)} core + {len(bg_neurons)} background) "
        f"and {len(edges)} edges (top real synapse pairs by weight)"
    )


if __name__ == "__main__":
    main()
