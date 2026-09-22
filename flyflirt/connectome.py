"""Loads the real MaleCNS cell-level connectome and builds the signed weight matrix.

The JSON is produced offline by ``tools/build_connectome.py`` from neuPrint. Every cell,
soma position, neurotransmitter prediction and synapse count in it is real data; the
running app never contacts neuPrint.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass

import numpy as np

# Sign / strength of a cell's outgoing synapses by predicted neurotransmitter.
# Acetylcholine excites; GABA and glutamate inhibit (glutamate is mixed in flies, so it
# is weighted slightly weaker); monoamines modulate, modelled as weak excitation.
NT_GAIN = {
    "unknown": 0.6,
    "acetylcholine": 1.0,
    "gaba": -1.0,
    "glutamate": -0.8,
    "dopamine": 0.5,
    "serotonin": 0.5,
    "octopamine": 0.5,
}

# Synapse counts are normalised by the receiving cell's total in-subgraph input, floored
# so cells with a handful of synapses are not over-amplified.
COLUMN_FLOOR = 30.0


@dataclass
class Channel:
    name: str
    cells: np.ndarray
    params: dict[str, float]


class Connectome:
    def __init__(self, data: dict):
        self.meta: dict = data.get("meta", {})
        self.roles: list[str] = data["roles"]
        self.nts: list[str] = data["nts"]
        self.types: list[str] = data["types"]

        cells = data["cells"]
        self.ids = np.asarray(cells["id"], dtype=np.int64)
        self.type_idx = np.asarray(cells["type"], dtype=np.int32)
        self.role = np.asarray(cells["role"], dtype=np.int8)
        self.nt = np.asarray(cells["nt"], dtype=np.int8)
        self.pos = np.asarray(cells["pos"], dtype=np.float32).reshape(-1, 3)
        self.n = int(self.ids.shape[0])

        edges = data["edges"]
        self.src = np.asarray(edges["src"], dtype=np.int32)
        self.dst = np.asarray(edges["dst"], dtype=np.int32)
        self.syn = np.asarray(edges["w"], dtype=np.float32)
        self.n_edges = int(self.src.shape[0])

        gains = np.asarray([NT_GAIN.get(self.nts[i], 0.6) for i in self.nt], dtype=np.float32)
        self.nt_gain = gains
        self.outsign = np.where(gains >= 0, 1.0, -1.0).astype(np.float32)

        raw = np.zeros((self.n, self.n), dtype=np.float32)
        raw[self.src, self.dst] = self.syn
        col_total = np.maximum(raw.sum(axis=0), COLUMN_FLOOR)
        self.W = (raw * gains[:, None]) / col_total[None, :]
        self.edge_w = self.W[self.src, self.dst].astype(np.float32)
        self.edge_abs = np.abs(self.edge_w)

        # Deterministic per-cell variation so a population never fires perfectly in unison.
        h = (self.ids * 2654435761) % 1000
        self.jitter = (0.85 + 0.30 * (h / 1000.0)).astype(np.float32)

        self.role_cells = {name: np.where(self.role == i)[0] for i, name in enumerate(self.roles)}
        self.accumulator = self.role_cells.get("accumulator", np.array([], dtype=np.int64))
        self.output = self.role_cells.get("output", np.array([], dtype=np.int64))

        self.channels: list[Channel] = [
            Channel(c["name"], np.asarray(c["cells"], dtype=np.int64), dict(c["params"]))
            for c in data.get("channels", [])
        ]
        self.channel_by_name = {c.name: c for c in self.channels}

        type_cells: dict[int, list[int]] = {}
        for cell, t in enumerate(self.type_idx):
            type_cells.setdefault(int(t), []).append(cell)
        self.type_cells = {t: np.asarray(v, dtype=np.int64) for t, v in type_cells.items()}

    # -- lookups ---------------------------------------------------------------------
    def type_name(self, cell: int) -> str:
        return self.types[int(self.type_idx[cell])]

    def role_name(self, cell: int) -> str:
        return self.roles[int(self.role[cell])]

    def nt_name(self, cell: int) -> str:
        return self.nts[int(self.nt[cell])]

    def summary(self) -> dict:
        return {
            "cells": self.n,
            "edges": self.n_edges,
            "types": len(self.types),
            "by_role": {name: int(idx.shape[0]) for name, idx in self.role_cells.items()},
            "dataset": self.meta.get("dataset", "male-cns:v1.0"),
        }


_LOCK = threading.Lock()
_CACHE: dict[str, Connectome] = {}


def load_connectome(path: str) -> Connectome:
    with _LOCK:
        cached = _CACHE.get(path)
        if cached is None:
            with open(path, encoding="utf-8") as fh:
                cached = Connectome(json.load(fh))
            _CACHE[path] = cached
        return cached
