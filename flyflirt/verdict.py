"""Builds the verdict: how many neurons / connections a chat recruited, and which
message was responsible for which ones."""
from __future__ import annotations

import time

import numpy as np

from .narrate import CHANNEL_INFO, CHANNEL_LEGEND

DISCLAIMER = (
    "This is a fruit-fly courtship cartoon sitting on your chat stats. It is not destiny. "
    "The connectome, its synapses and neurotransmitters are real (Janelia MaleCNS); how words map "
    "onto input neurons is our modelling choice."
)

HEADLINES = {
    "flirt": {
        "resonance": "Strong chemistry: the fly's courtship hub locked on.",
        "warming": "Warming up: the hub is listening and starting to respond.",
        "quiet": "Quiet circuit: friendly enough, but nothing has sparked yet.",
        "friction": "Friction detected: the fly kept its brake on.",
    },
    "friends": {
        "resonance": "Great rapport: your chat lit up the fly's social-arousal hub.",
        "warming": "Good vibes building: the hub is picking up the rhythm.",
        "quiet": "Easygoing and calm: the circuit stayed mostly at rest.",
        "friction": "A bit of friction: the fly's brake stayed engaged.",
    },
}

MAX_FOOTPRINT_CELLS = 250
MAX_FOOTPRINT_EDGES = 320


def _tier(meter: float, tension_avg: float) -> str:
    if tension_avg >= 0.45 and meter < 0.35:
        return "friction"
    if meter >= 0.6:
        return "resonance"
    if meter >= 0.3:
        return "warming"
    return "quiet"


def _pathways(conn, edge_idx: np.ndarray, flux: np.ndarray, top: int) -> list[dict]:
    if edge_idx.size == 0:
        return []
    groups: dict[tuple[int, int], list[float]] = {}
    ts, td = conn.type_idx[conn.src[edge_idx]], conn.type_idx[conn.dst[edge_idx]]
    for e, a, b in zip(edge_idx, ts, td):
        g = groups.setdefault((int(a), int(b)), [0, 0.0])
        g[0] += 1
        g[1] += float(flux[e])
    ranked = sorted(groups.items(), key=lambda kv: -kv[1][1])[:top]
    return [
        {"from": conn.types[a], "to": conn.types[b], "connections": int(c), "flux": round(f, 3)}
        for (a, b), (c, f) in ranked
    ]


def build_verdict(room, conn, ecfg) -> dict:
    eng = room.engine
    analysed = [m for m in room.messages if m.analysed]
    count = min(eng.count, len(analysed))
    cache = getattr(room, "_verdict_cache", None)
    if cache and cache[0] == count:
        return cache[1]

    acc = eng.attribution_matrix()[:count]
    total = acc.sum(axis=0)
    cell_on = eng.peak_abs >= ecfg.act_thr
    edge_on = eng.peak_flux >= ecfg.edge_thr
    resp = np.where(total > 0, acc.argmax(axis=0), -1)
    on_total = float(acc[:, cell_on].sum()) or 1.0

    by_role = {}
    for i, role in enumerate(conn.roles):
        mask = conn.role == i
        by_role[role] = {"active": int((cell_on & mask).sum()), "total": int(mask.sum())}

    type_active: dict[int, int] = {}
    for t in conn.type_idx[cell_on]:
        type_active[int(t)] = type_active.get(int(t), 0) + 1
    top_types = sorted(type_active.items(), key=lambda kv: -kv[1])[:10]
    top_types = [
        {"type": conn.types[t], "active": n, "total": int(conn.type_cells[t].size),
         "role": conn.roles[int(conn.role[conn.type_cells[t][0]])]}
        for t, n in top_types
    ]

    messages = []
    channel_totals: dict[str, float] = {}
    for m in range(count):
        msg, trace = analysed[m], eng.traces[m]
        for ch, s in trace.channels.items():
            channel_totals[ch] = channel_totals.get(ch, 0.0) + abs(s)
        cells = np.where(cell_on & (resp == m))[0]
        cells = cells[np.argsort(-acc[m, cells])] if cells.size else cells
        edges = np.where(edge_on & (resp[conn.src] == m))[0]
        edges = edges[np.argsort(-eng.peak_flux[edges])] if edges.size else edges
        who = room.participant(msg.slot)
        channels = sorted(((k, v) for k, v in trace.channels.items() if abs(v) >= 0.05), key=lambda kv: -abs(kv[1]))
        messages.append({
            "index": m, "seq": msg.seq, "slot": msg.slot,
            "nickname": who.nickname if who else "?", "label": who.label if who else "?",
            "text": msg.text, "params": msg.params, "topic": msg.topic, "meter": trace.meter,
            "channels": [{"channel": k, "strength": round(v, 2), "population": CHANNEL_INFO[k][1]} for k, v in channels[:3]],
            "share": round(float(acc[m, cell_on].sum()) / on_total, 4),
            "recruited_cells": trace.new_cells, "recruited_edges": trace.new_edges,
            "responsible_cells": int(cells.size), "responsible_edges": int(edges.size),
            "pathways": _pathways(conn, edges, eng.peak_flux, 4),
            "footprint": {"cells": cells[:MAX_FOOTPRINT_CELLS].astype(int).tolist(),
                          "edges": edges[:MAX_FOOTPRINT_EDGES].astype(int).tolist()},
        })

    tensions = [float((m.params or {}).get("tension", 0.0)) for m in analysed[:count]]
    tension_avg = sum(tensions) / len(tensions) if tensions else 0.0
    meter = eng.meter()
    tier = _tier(meter, tension_avg)
    mode = room.mode if room.mode in HEADLINES else "friends"
    edge_idx_on = np.where(edge_on)[0]
    ts = room.created_at
    verdict = {
        "room_id": room.id,
        "mode": room.mode,
        "participants": [p.public() for p in room.participants],
        "messages_analysed": count,
        "duration_s": int(max(0, (analysed[count - 1].ts - ts))) if count else 0,
        "generated_at": time.time(),
        "tier": tier,
        "headline": HEADLINES[mode][tier],
        "meter": round(meter, 3),
        "peak_meter": round(eng.peak_meter, 3),
        "hub_level": round(eng.hub_level(), 3),
        "output_firing": bool(eng.output_level() > 0.02),
        "totals": {
            "cells_active": int(cell_on.sum()), "cells_total": int(conn.n),
            "edges_active": int(edge_on.sum()), "edges_total": int(conn.n_edges),
            "types_active": len(type_active),
        },
        "by_role": by_role,
        "top_types": top_types,
        "signature_pathways": _pathways(conn, edge_idx_on, eng.peak_flux, 6),
        "channels": sorted(
            ({"channel": k, "population": CHANNEL_INFO[k][1], "total": round(v, 2)} for k, v in channel_totals.items()),
            key=lambda d: -d["total"],
        ),
        "channel_legend": CHANNEL_LEGEND,
        "messages": messages,
        "method": (
            "Each chat message is turned into 7 parameters (warmth, humor, reciprocity, curiosity, disclosure, "
            "energy, tension) which drive real input neuron populations of the MaleCNS connectome. Activity then "
            "propagates through the real synapses of a 1,350-cell subgraph. Because the update is exactly additive, "
            "every neuron's activity is split into per-message contributions; a connection is credited to the "
            "message that drove its presynaptic neuron."
        ),
        "disclaimer": DISCLAIMER,
    }
    room._verdict_cache = (count, verdict)  # type: ignore[attr-defined]
    return verdict


def public_card(verdict: dict, room, conn, ecfg) -> dict:
    """Shareable summary: numbers and neuron anatomy only, never any chat text."""
    eng = room.engine
    active = np.where(eng.peak_abs >= ecfg.act_thr)[0]
    strongest = active[np.argsort(-eng.peak_abs[active])][:900] if active.size else active
    return {
        "mode": verdict["mode"],
        "tier": verdict["tier"],
        "headline": verdict["headline"],
        "meter": verdict["meter"],
        "peak_meter": verdict["peak_meter"],
        "messages": verdict["messages_analysed"],
        "totals": verdict["totals"],
        "by_role": verdict["by_role"],
        "top_types": verdict["top_types"][:6],
        "signature_pathways": verdict["signature_pathways"][:5],
        "channels": verdict["channels"],
        "active_cells": strongest.astype(int).tolist(),
        "disclaimer": DISCLAIMER,
    }
