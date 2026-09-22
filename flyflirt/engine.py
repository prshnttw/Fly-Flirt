"""Cell-level neural simulation with exact per-message attribution.

Model
-----
Every cell carries a state ``a`` in (-1, 1). One chat message is a stimulus that lasts
``substeps`` synaptic time-steps, so its signal can cascade several synapses deep
(input -> pC1 hub -> output) within the same turn:

    pre   = x + gain * (a @ W)                 # x: message drive, W: signed synapse weights
    T     = tanh(pre)
    a_new = d * a + (1 - d) * T                # leaky integration; d drifts up (habituation)

Attribution
-----------
tanh is applied elementwise, so with the secant slope ``s = T / pre`` the update is an
*exactly additive* function of its inputs. That lets us keep one contribution vector per
message, ``c_m``, evolving under the same linearised dynamics, with ``sum_m c_m == a``.
``c_m[cell]`` is therefore precisely "how much of this cell's activity was caused by
message m". A connection (edge) is carried by its presynaptic cell, so the message that
drove the presynaptic cell is the message responsible for that connection.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .connectome import Connectome

F32 = np.float32


@dataclass
class EngineConfig:
    # Operating point fitted with tools/fit_operating_point.py on the real connectome:
    # supercritical for the hub's persistence mode (gain > ~1.7) yet below the oscillatory
    # instability (~4.9). A warm chat reads ~65% by message 10, a typical one ~40% by 15,
    # a low-key one ~15%, a cold one stays at 0%.
    gain: float = 4.0
    input_gain: float = 1.6
    threshold: float = 0.14        # soft firing threshold (shrinkage) applied to pre-activation
    meter_scale: float = 0.107     # mean hub activation at which the meter reads ~76%
    substeps: int = 4
    decay_start: float = 0.82
    decay_end: float = 0.95
    habituation_half: float = 14.0
    act_thr: float = 0.10          # |state| at which a cell counts as "activated"
    edge_thr: float = 0.03         # gain*|w|*|state_src| at which a connection counts as "activated"
    frame_thr: float = 0.02        # min |contribution| for a cell to appear in a live frame
    frame_ref: float = 0.15        # |contribution| mapped to full brightness in a live frame
    state_ref: float = 0.5         # |state| mapped to full brightness in the persistent glow
    frame_cells: int = 180
    frame_edges: int = 90
    state_cells: int = 320
    alive_max: int = 36
    freeze_eps: float = 2e-3

    @classmethod
    def from_config(cls, config) -> "EngineConfig":
        return cls(
            gain=config.SIM_GAIN,
            input_gain=config.SIM_INPUT_GAIN,
            threshold=config.SIM_THRESHOLD,
            meter_scale=config.SIM_METER_SCALE,
            substeps=config.SIM_SUBSTEPS,
            decay_start=config.SIM_DECAY_START,
            decay_end=config.SIM_DECAY_END,
            habituation_half=config.SIM_HABITUATION_HALF,
            act_thr=config.SIM_ACT_THR,
            edge_thr=config.SIM_EDGE_THR,
        )


@dataclass
class MessageTrace:
    index: int
    params: dict
    channels: dict
    meter: float
    output_level: float
    new_cells: int
    new_edges: int
    cells_active: int
    edges_active: int
    top_types: list = field(default_factory=list)
    top_channel: str | None = None


@dataclass
class StepResult:
    trace: MessageTrace
    frames: list
    state_idx: list
    state_val: list


class RoomEngine:
    """Simulation state for one conversation. Not thread-safe; guard with a room lock."""

    def __init__(self, conn: Connectome, cfg: EngineConfig | None = None):
        self.conn = conn
        self.cfg = cfg or EngineConfig()
        n = conn.n
        self.a = np.zeros(n, dtype=F32)
        self.count = 0
        self.peak_abs = np.zeros(n, dtype=F32)
        self.peak_flux = np.zeros(conn.n_edges, dtype=F32)
        self.first_msg = np.full(n, -1, dtype=np.int32)
        self.first_edge_msg = np.full(conn.n_edges, -1, dtype=np.int32)
        self._rows: dict[int, np.ndarray] = {}
        self._acc_dense: dict[int, np.ndarray] = {}
        self._acc_sparse: dict[int, tuple[np.ndarray, np.ndarray]] = {}
        self.traces: list[MessageTrace] = []
        self.peak_meter = 0.0

    # -- dynamics --------------------------------------------------------------------
    def decay_for(self, m: int) -> float:
        c = self.cfg
        frac = 1.0 - math.exp(-m / max(c.habituation_half, 1e-6))
        return c.decay_start + (c.decay_end - c.decay_start) * frac

    def channel_strengths(self, params: dict) -> dict[str, float]:
        out = {}
        for ch in self.conn.channels:
            s = sum(coef * float(params.get(name, 0.0)) for name, coef in ch.params.items())
            out[ch.name] = float(max(-1.0, min(1.0, s)))
        return out

    def _drive(self, strengths: dict[str, float]) -> np.ndarray:
        x = np.zeros(self.conn.n, dtype=F32)
        for ch in self.conn.channels:
            s = strengths.get(ch.name, 0.0)
            if s == 0.0 or ch.cells.size == 0:
                continue
            # outsign flips the push for inhibitory cells so the *net effect on the hub*
            # is excitatory: GABA cells are suppressed (disinhibition) rather than driven.
            x[ch.cells] = s * self.conn.outsign[ch.cells] * self.conn.jitter[ch.cells] * self.cfg.input_gain
        return x

    def hub_level(self) -> float:
        """Mean positive activation of the pC1 hub cells (the raw courtship-arousal signal)."""
        acc = self.conn.accumulator
        if acc.size == 0:
            return 0.0
        return float(np.clip(self.a[acc], 0.0, None).mean())

    def meter(self) -> float:
        """0..1 readout: a saturating transform of hub activation, so the bar climbs quickly at
        first and eases toward full instead of hitting a hard ceiling."""
        return float(math.tanh(self.hub_level() / max(self.cfg.meter_scale, 1e-6)))

    def output_level(self) -> float:
        out = self.conn.output
        return float(np.abs(self.a[out]).mean()) if out.size else 0.0

    def step(self, params: dict) -> StepResult:
        cfg, conn = self.cfg, self.conn
        m = self.count
        self.count += 1
        strengths = self.channel_strengths(params)
        x = self._drive(strengths)

        substeps = max(1, cfg.substeps)
        d = self.decay_for(m) ** (1.0 / substeps)
        one_minus = F32(1.0 - d)
        d = F32(d)
        gain = F32(cfg.gain)

        W = conn.W
        keys = sorted(self._rows)
        rows = np.stack([self._rows[k] for k in keys]) if keys else np.zeros((0, conn.n), dtype=F32)
        cm = np.zeros(conn.n, dtype=F32)
        acc_m = np.zeros(conn.n, dtype=F32)
        a = self.a

        edges_active_before = self.peak_flux >= cfg.edge_thr
        frames = []
        theta = F32(cfg.threshold)
        for _ in range(substeps):
            pre0 = x + gain * (a @ W)          # linear in the contributions
            pre = pre0
            if theta > 0:
                # soft threshold: sub-threshold input does nothing, so weakly-driven cells stay silent
                pre = (np.sign(pre0) * np.maximum(np.abs(pre0) - theta, 0.0)).astype(F32)
            T = np.tanh(pre)
            nz = np.abs(pre0) > 1e-6
            sec = np.where(nz, T / np.where(nz, pre0, 1.0), 0.0 if theta > 0 else 1.0).astype(F32)
            factor = one_minus * sec
            a = (d * a + one_minus * T).astype(F32)

            if rows.shape[0]:
                rows = (d * rows + factor * gain * (rows @ W)).astype(F32)
                for i, k in enumerate(keys):
                    self._acc_dense[k] += np.abs(rows[i])
            cm = (d * cm + factor * (x + gain * (cm @ W))).astype(F32)
            acc_m += np.abs(cm)

            abs_a = np.abs(a)
            newly = (abs_a >= cfg.act_thr) & (self.first_msg < 0)
            self.first_msg[newly] = m
            np.maximum(self.peak_abs, abs_a, out=self.peak_abs)
            flux = conn.edge_abs * gain * abs_a[conn.src]
            np.maximum(self.peak_flux, flux, out=self.peak_flux)
            frames.append(self._frame(cm, gain))

        self.a = a
        self._rows = {k: rows[i] for i, k in enumerate(keys)}
        self._rows[m] = cm
        self._acc_dense[m] = acc_m
        self._freeze_stale()

        edges_active_now = self.peak_flux >= cfg.edge_thr
        newly_edges = edges_active_now & ~edges_active_before
        self.first_edge_msg[newly_edges & (self.first_edge_msg < 0)] = m

        meter = self.meter()
        self.peak_meter = max(self.peak_meter, meter)
        trace = MessageTrace(
            index=m,
            params={k: round(float(v), 3) for k, v in params.items() if isinstance(v, (int, float))},
            channels={k: round(v, 3) for k, v in strengths.items()},
            meter=round(meter, 4),
            output_level=round(self.output_level(), 4),
            new_cells=int((self.first_msg == m).sum()),
            new_edges=int(newly_edges.sum()),
            cells_active=int((self.peak_abs >= cfg.act_thr).sum()),
            edges_active=int(edges_active_now.sum()),
            top_types=self._top_types(np.abs(cm)),
            top_channel=self._top_channel(strengths),
        )
        self.traces.append(trace)

        state_idx, state_val = self.state_snapshot()
        return StepResult(trace=trace, frames=frames, state_idx=state_idx, state_val=state_val)

    # -- live frames -----------------------------------------------------------------
    def _frame(self, cm: np.ndarray, gain: float) -> dict:
        cfg, conn = self.cfg, self.conn
        mag = np.abs(cm)
        idx = np.where(mag >= cfg.frame_thr)[0]
        if idx.size > cfg.frame_cells:
            idx = idx[np.argpartition(mag[idx], -cfg.frame_cells)[-cfg.frame_cells:]]
        idx = idx[np.argsort(-mag[idx])]

        flux = conn.edge_abs * gain * mag[conn.src]
        eidx = np.where(flux >= cfg.frame_thr * 0.5)[0]
        if eidx.size > cfg.frame_edges:
            eidx = eidx[np.argpartition(flux[eidx], -cfg.frame_edges)[-cfg.frame_edges:]]
        eidx = eidx[np.argsort(-flux[eidx])]
        return {
            "cells": idx.astype(int).tolist(),
            "acts": [round(min(1.0, float(v) / cfg.frame_ref), 2) for v in mag[idx]],
            "edges": eidx.astype(int).tolist(),
        }

    def state_snapshot(self, k: int | None = None) -> tuple[list, list]:
        k = k or self.cfg.state_cells
        mag = np.abs(self.a)
        idx = np.where(mag >= 0.02)[0]
        if idx.size > k:
            idx = idx[np.argpartition(mag[idx], -k)[-k:]]
        idx = idx[np.argsort(-mag[idx])]
        ref = self.cfg.state_ref
        return idx.astype(int).tolist(), [round(min(1.0, float(mag[i]) / ref), 2) for i in idx]

    def _top_types(self, mag: np.ndarray, k: int = 4) -> list:
        conn = self.conn
        scores: dict[int, float] = {}
        for role in ("accumulator", "output", "brake", "input"):
            for cell in conn.role_cells.get(role, []):
                t = int(conn.type_idx[cell])
                scores[t] = max(scores.get(t, 0.0), float(mag[cell]))
        ranked = sorted(scores.items(), key=lambda kv: -kv[1])[:k]
        return [{"type": conn.types[t], "level": round(v, 3)} for t, v in ranked if v > 1e-3]

    def _top_channel(self, strengths: dict[str, float]) -> str | None:
        if not strengths:
            return None
        name, val = max(strengths.items(), key=lambda kv: abs(kv[1]))
        return name if abs(val) > 0.05 else None

    # -- memory management -----------------------------------------------------------
    def _freeze(self, k: int) -> None:
        acc = self._acc_dense.pop(k, None)
        self._rows.pop(k, None)
        if acc is None:
            return
        idx = np.where(acc > 1e-4)[0].astype(np.int32)
        self._acc_sparse[k] = (idx, acc[idx].astype(F32))

    def _freeze_stale(self) -> None:
        cfg = self.cfg
        for k in list(self._rows):
            if k != self.count - 1 and float(np.abs(self._rows[k]).max()) < cfg.freeze_eps:
                self._freeze(k)
        while len(self._rows) > cfg.alive_max:
            self._freeze(min(self._rows))

    def compact(self) -> None:
        """Freeze every live contribution row (used when a room goes idle)."""
        for k in list(self._rows):
            self._freeze(k)

    # -- attribution -----------------------------------------------------------------
    def attribution_matrix(self) -> np.ndarray:
        """(messages x cells) accumulated |contribution| of each message to each cell."""
        out = np.zeros((self.count, self.conn.n), dtype=F32)
        for k, (idx, val) in self._acc_sparse.items():
            out[k, idx] = val
        for k, vec in self._acc_dense.items():
            out[k] = vec
        return out
