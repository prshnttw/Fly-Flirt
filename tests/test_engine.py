import unittest

import numpy as np

from flyflirt.config import Config
from flyflirt.connectome import load_connectome
from flyflirt.engine import EngineConfig, RoomEngine

PARAMS = ["warmth", "humor", "reciprocity", "curiosity", "disclosure", "energy", "tension"]


def p(**kw):
    base = {k: 0.0 for k in PARAMS}
    base.update(kw)
    return base


WARM = p(warmth=0.8, humor=0.7, reciprocity=0.8, curiosity=0.6, disclosure=0.5, energy=0.7, tension=0.05)
COLD = p(warmth=0.05, reciprocity=0.05, energy=0.05, tension=0.6)
FUNNY = p(warmth=0.3, humor=0.9, reciprocity=0.4, energy=0.8)
CURIOUS = p(warmth=0.4, reciprocity=0.6, curiosity=0.9, energy=0.3)


class EngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conn = load_connectome(Config.CONNECTOME_PATH)

    def engine(self, **overrides):
        cfg = EngineConfig.from_config(Config)
        for k, v in overrides.items():
            setattr(cfg, k, v)
        return RoomEngine(self.conn, cfg)

    def test_connectome_shape_and_roles(self):
        c = self.conn
        self.assertEqual(c.n, 1350)
        self.assertGreater(c.n_edges, 40000)
        self.assertEqual(c.accumulator.size, 156)   # every pC1 cell
        self.assertGreater(c.output.size, 0)
        self.assertEqual({ch.name for ch in c.channels},
                         {"brake_m1", "brake_m8", "banter", "attention", "confiding", "vigor", "feedback"})
        # brake cells are GABAergic => negative outgoing weights
        brake = c.role_cells["brake"]
        out_w = c.edge_w[np.isin(c.src, brake)]
        self.assertTrue((out_w <= 0).all())

    def test_contributions_sum_exactly_to_state(self):
        """The core attribution claim: per-message contributions add up to the true state."""
        eng = self.engine(freeze_eps=0.0, alive_max=10_000)
        seq = [WARM, FUNNY, COLD, CURIOUS, WARM, FUNNY]
        for params in seq:
            eng.step(params)
        total = np.zeros(self.conn.n, dtype=np.float64)
        for row in eng._rows.values():
            total += row
        self.assertLess(float(np.abs(total - eng.a).max()), 1e-4)

    def test_attribution_matrix_survives_compaction(self):
        eng = self.engine()
        for params in [WARM, FUNNY, CURIOUS, WARM]:
            eng.step(params)
        before = eng.attribution_matrix()
        eng.compact()
        after = eng.attribution_matrix()
        self.assertEqual(before.shape, (4, self.conn.n))
        # sparse storage drops only vanishing values
        self.assertLess(float(np.abs(before - after).max()), 2e-4)

    def test_is_deterministic_so_rooms_can_be_replayed(self):
        seq = [WARM, FUNNY, COLD, CURIOUS, WARM]
        a, b = self.engine(), self.engine()
        for params in seq:
            ra, rb = a.step(params), b.step(params)
            self.assertEqual(ra.trace.meter, rb.trace.meter)
        self.assertTrue(np.array_equal(a.a, b.a))
        self.assertEqual(a.traces[-1].cells_active, b.traces[-1].cells_active)

    def test_pacing_matches_design_targets(self):
        warm = self.engine()
        meters = [warm.step(WARM).trace.meter for _ in range(12)]
        self.assertGreaterEqual(meters[9], 0.55, "a warm chat should be well up by message 10")
        self.assertTrue(all(b >= a - 1e-9 for a, b in zip(meters, meters[1:])), "warm chat climbs monotonically")
        cold = self.engine()
        self.assertLess(max(cold.step(COLD).trace.meter for _ in range(20)), 0.05, "a cold chat stays flat")

    def test_different_message_styles_use_different_channels(self):
        eng = self.engine()
        self.assertEqual(eng.step(FUNNY).trace.top_channel, "banter")
        eng = self.engine()
        self.assertEqual(eng.step(CURIOUS).trace.top_channel, "attention")

    def test_tension_opposes_warmth_at_the_brake(self):
        eng = self.engine()
        strengths = eng.channel_strengths(p(warmth=0.5, tension=0.9))
        self.assertLess(strengths["brake_m1"], 0.0)
        self.assertGreater(eng.channel_strengths(p(warmth=0.9))["brake_m1"], 0.0)

    def test_habituation_slows_gains(self):
        eng = self.engine()
        self.assertLess(eng.decay_for(0), eng.decay_for(30))

    def test_frames_are_sparse_and_normalised(self):
        eng = self.engine()
        result = eng.step(WARM)
        self.assertEqual(len(result.frames), eng.cfg.substeps)
        for frame in result.frames:
            self.assertLessEqual(len(frame["cells"]), eng.cfg.frame_cells)
            self.assertLessEqual(len(frame["edges"]), eng.cfg.frame_edges)
            self.assertTrue(all(0.0 <= v <= 1.0 for v in frame["acts"]))
        self.assertEqual(len(result.state_idx), len(result.state_val))

    def test_first_recruitment_is_recorded(self):
        eng = self.engine()
        eng.step(WARM)
        eng.step(WARM)
        recruited = (eng.first_msg >= 0)
        self.assertEqual(int(recruited.sum()), int((eng.peak_abs >= eng.cfg.act_thr).sum()))
        self.assertEqual(sum(t.new_cells for t in eng.traces), int(recruited.sum()))


if __name__ == "__main__":
    unittest.main()
