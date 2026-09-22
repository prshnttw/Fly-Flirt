"""The optional 'full pathway' connectome: per-room switch, replay, capacity limit, calibration."""
import dataclasses
import os
import unittest

from flyflirt.connectome import load_connectome
from flyflirt.engine import EngineConfig, RoomEngine
from tests.helpers import enter_room, make_app, pair_up, send

FULL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "connectome_full.json")

WARM = dict(warmth=.85, humor=.3, reciprocity=.9, curiosity=.4, disclosure=.6, energy=.5, tension=.05)
COLD = dict(warmth=.05, humor=0, reciprocity=.1, curiosity=.05, disclosure=0, energy=.1, tension=.8)


@unittest.skipUnless(os.path.exists(FULL), "connectome_full.json not built (tools/build_connectome.py --context 4000)")
class FullPathwayTests(unittest.TestCase):
    def setUp(self):
        self.app = make_app()
        self.svc = self.app.extensions["flyflirt"]
        self.a, self.b, self.room_id, _, _ = pair_up(self.app, "friends", "friends")
        enter_room(self.a, self.room_id)
        enter_room(self.b, self.room_id)

    def test_full_connectome_is_bigger_and_consistent(self):
        std, full = self.svc.connectome, self.svc.connectome_for("full")
        self.assertGreater(full.n, 3 * std.n)
        self.assertGreater(full.n_edges, 2 * std.n_edges)
        self.assertEqual(sorted(full.role_cells), sorted(std.role_cells))
        self.assertEqual(len(full.accumulator), len(std.accumulator))          # same hub, more surrounding wiring
        self.assertEqual(len(full.channels), len(std.channels))

    def test_meter_reads_alike_on_both_graphs(self):
        std, full = self.svc.connectome, self.svc.connectome_for("full")
        for params, low, high in ((WARM, 0.5, 0.85), (COLD, 0.0, 0.05)):
            reads = []
            for conn, scale in ((std, "standard"), (full, "full")):
                eng = RoomEngine(conn, self.svc.engine_cfg_for(scale))
                for _ in range(12):
                    meter = eng.step(params).trace.meter
                reads.append(meter)
            for value in reads:
                self.assertTrue(low <= value <= high, reads)
            self.assertLess(abs(reads[0] - reads[1]), 0.12, reads)

    def test_switching_replays_the_chat_on_the_bigger_brain(self):
        lines = ["hello there, lovely to meet you!", "hi! nice to meet you too, how is your day?", "what do you enjoy doing on weekends?",
                 "hiking mostly, and reading. you?", "I love hiking and reading too", "ha, we should swap book tips"]
        for i, text in enumerate(lines):
            send(self.a if i % 2 == 0 else self.b, self.room_id, text, [self.a, self.b])
        before = self.svc.rooms.get(self.room_id)
        cells_before = before.engine.conn.n
        self.b.emit("set_scale", {"room_id": self.room_id, "scale": "full"})
        self.b.wait("scale_working")
        changed = self.a.wait("scale_changed", 20)
        self.assertEqual(changed["scale"], "full")
        room = self.svc.rooms.get(self.room_id)
        self.assertEqual(room.scale, "full")
        self.assertGreater(room.engine.conn.n, cells_before)
        self.assertEqual(room.engine.count, 6)                                  # every message was replayed
        self.assertTrue(all(m.meter is not None for m in room.messages))
        # the verdict is now computed on the full graph, and stays internally consistent
        code, verdict = self.a.json(f"/api/rooms/{self.room_id}/verdict")
        self.assertEqual(code, 200)
        self.assertEqual(verdict["totals"]["cells_total"], room.engine.conn.n)
        self.assertEqual(sum(m["responsible_cells"] for m in verdict["messages"]), verdict["totals"]["cells_active"])
        # and it switches back
        self.a.emit("set_scale", {"room_id": self.room_id, "scale": "standard"})
        self.a.wait("scale_changed", 20)
        self.assertEqual(self.svc.rooms.get(self.room_id).engine.conn.n, cells_before)

    def test_new_messages_use_the_room_scale_and_lab_ignores_full_rooms(self):
        self.a.emit("set_scale", {"room_id": self.room_id, "scale": "full"})
        self.a.wait("scale_changed", 20)
        lab = enter_lab(self.app)
        update = send(self.a, self.room_id, "a warm and curious message!", [self.a, self.b])
        self.assertEqual(update["counts"]["cells_total"], self.svc.connectome_for("full").n)
        self.assertFalse(lab.has("lab_pulse"))              # indices belong to a different graph

    def test_capacity_limit_and_bad_values(self):
        app = make_app(FULL_MAX_ROOMS=0)
        a, b, room_id, _, _ = pair_up(app, "friends", "friends")
        enter_room(a, room_id)
        a.emit("set_scale", {"room_id": room_id, "scale": "full"})
        self.assertEqual(a.wait("error_message")["code"], "full_busy")
        a.emit("set_scale", {"room_id": room_id, "scale": "gigantic"})
        self.assertEqual(a.wait("error_message")["code"], "bad_scale")

    def test_disabled_flag_and_outsiders(self):
        app = make_app(FULL_ENABLED=False)
        a, b, room_id, _, _ = pair_up(app, "friends", "friends")
        enter_room(a, room_id)
        a.emit("set_scale", {"room_id": room_id, "scale": "full"})
        self.assertEqual(a.wait("error_message")["code"], "no_full")
        from tests.helpers import Browser
        outsider = Browser(app)
        outsider.emit("set_scale", {"room_id": room_id, "scale": "full"})
        self.assertEqual(outsider.wait("error_message")["code"], "forbidden")

    def test_scale_survives_a_restart(self):
        self.a.emit("set_scale", {"room_id": self.room_id, "scale": "full"})
        self.a.wait("scale_changed", 20)
        send(self.a, self.room_id, "one more message", [self.a, self.b])
        room = self.svc.rooms.get(self.room_id)
        self.svc.rooms._rooms.pop(self.room_id)                                  # forget it in memory
        restored = self.svc.rooms.get(self.room_id)                              # ...and reload it from SQLite
        self.assertEqual(restored.scale, "full")
        self.assertEqual(restored.engine.conn.n, room.engine.conn.n)
        self.assertEqual(restored.engine.count, room.engine.count)


def enter_lab(app):
    from tests.helpers import Browser
    lab = Browser(app)
    lab.emit("lab_subscribe")
    lab.wait("lab_stats")
    return lab


if __name__ == "__main__":
    unittest.main()
