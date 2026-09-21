import json
import os

import numpy as np

CIRCUIT_PATH = os.path.join(os.path.dirname(__file__), "circuit.json")
STATE_PATH = os.path.join(os.path.dirname(__file__), "circuit_state.json")

DECAY = 0.85
GAIN = 80.0

NT_SIGN = {"acetylcholine": 1.0, "gaba": -1.0, "glutamate": -1.0}


def _load_circuit(path=CIRCUIT_PATH):
    with open(path) as f:
        circuit = json.load(f)

    nodes = circuit["nodes"]
    node_ids = [n["id"] for n in nodes]
    index = {node_id: i for i, node_id in enumerate(node_ids)}
    n = len(nodes)

    sign = np.array([NT_SIGN.get(node.get("neurotransmitter"), 1.0) for node in nodes])

    raw = np.zeros((n, n))
    for edge in circuit["edges"]:
        j = index[edge["from"]]
        i = index[edge["to"]]
        raw[j, i] += edge["weight"]

    max_weight = raw.max() if raw.max() > 0 else 1.0
    weights = (raw / max_weight) * sign[:, None]

    sensory_idx = [index[node["id"]] for node in nodes if node["role"] == "sensory"]
    accumulator_idx = [index[node["id"]] for node in nodes if node["role"] == "accumulator"]
    output_idx = [index[node["id"]] for node in nodes if node["role"] == "output"]

    return {
        "nodes": nodes,
        "node_ids": node_ids,
        "index": index,
        "raw": raw,
        "weights": weights,
        "sensory_idx": sensory_idx,
        "accumulator_idx": accumulator_idx,
        "output_idx": output_idx,
    }


def _load_state(n):
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH) as f:
                arr = np.array(json.load(f), dtype=float)
            if arr.shape == (n,):
                return arr
        except (json.JSONDecodeError, ValueError):
            pass
    return np.zeros(n)


def _save_state():
    with open(STATE_PATH, "w") as f:
        json.dump(_state.tolist(), f)


_circuit = _load_circuit()
_state = _load_state(len(_circuit["node_ids"]))


def reset():
    global _state
    _state = np.zeros(len(_circuit["node_ids"]))
    _save_state()


def last_meter():
    return float(np.clip(np.mean(_state[_circuit["accumulator_idx"]]), 0.0, 1.0))


def last_snapshot():
    return dict(zip(_circuit["node_ids"], _state.tolist()))


def top_node(role):
    """The single most active real neuron type in this role right now, by |activation|."""
    idx_list = _circuit[f"{role}_idx"]
    best_idx = max(idx_list, key=lambda i: abs(_state[i]))
    return _circuit["node_ids"][best_idx], float(_state[best_idx])


def raw_synapse_count(from_id, to_id):
    """Real total synapse count from one neuron type to another, straight from circuit.json."""
    index = _circuit["index"]
    if from_id not in index or to_id not in index:
        return 0
    return int(_circuit["raw"][index[from_id], index[to_id]])


def step(scores):
    global _state

    avg_score = (scores["warmth"] + scores["humor"] + scores["reciprocity"]) / 3.0

    # mAL neurons are real, GABAergic (inhibitory) upstream partners of pC1 in
    # MaleCNS — there's no excitatory "sensory" neuron feeding this circuit, so
    # good chemistry is modeled as disinhibition: it suppresses mAL's tone,
    # which releases pC1 from inhibition instead of driving it directly.
    input_vec = np.zeros(len(_state))
    for idx in _circuit["sensory_idx"]:
        input_vec[idx] = -avg_score

    recurrent = _state @ _circuit["weights"]
    _state = DECAY * _state + (1 - DECAY) * np.tanh(input_vec + GAIN * recurrent)
    _save_state()

    accumulator_activity = float(np.mean(_state[_circuit["accumulator_idx"]]))
    output_activity = float(np.mean(_state[_circuit["output_idx"]]))
    meter = float(np.clip(accumulator_activity, 0.0, 1.0))

    return {
        "meter": meter,
        "p1_activity": accumulator_activity,
        "output_activity": output_activity,
        "node_activity": dict(zip(_circuit["node_ids"], _state.tolist())),
    }
