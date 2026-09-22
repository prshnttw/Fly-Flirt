"""Abuse controls: message filter, blocks, skips, strikes/bans, report handling."""
import unittest

from flyflirt.moderation import RepeatGuard, check_message
from flyflirt.storage import Storage
from flyflirt.safety import Safety
from tests.helpers import Browser, enter_room, make_app, pair_up


class ModerationTests(unittest.TestCase):
    def test_clean_text_passes_untouched(self):
        for text in ("Hey, how was your day?", "I love hiking in the mountains!", "Scunthorpe is a town", "Class assignment due"):
            v = check_message(text)
            self.assertEqual((v.action, v.text), ("ok", text), text)

    def test_hate_threats_and_sexual_harassment_are_blocked(self):
        for text in ("i will kill you", "go kill yourself", "k y s", "send nudes", "you f a g g o t", "I'm gonna find you"):
            self.assertEqual(check_message(text).action, "block", text)

    def test_evasions_are_caught(self):
        for text in ("s3nd nud3s", "s.e.n.d n.u.d.e.s", "k1ll y0urself"):
            self.assertEqual(check_message(text).action, "block", text)

    def test_profanity_is_masked_not_blocked(self):
        v = check_message("this is such bullshit lol")
        self.assertEqual(v.action, "mask")
        self.assertNotIn("bullshit", v.text)
        self.assertTrue(v.text.startswith("this is such b"))

    def test_contact_details_are_hidden(self):
        v = check_message("email me at jane.doe@example.com or call +1 (555) 123-4567 or see https://x.example.com/me")
        self.assertEqual(v.action, "mask")
        for leaked in ("jane.doe@example.com", "555", "https://"):
            self.assertNotIn(leaked, v.text)

    def test_spam_and_shouting(self):
        self.assertEqual(check_message("a" * 30).action, "spam")
        v = check_message("STOP SHOUTING AT ME PLEASE")
        self.assertEqual(v.action, "mask")
        self.assertFalse(v.text.isupper())

    def test_repeat_guard(self):
        g = RepeatGuard(limit=3)
        self.assertFalse(g.repeated("c", "hello"))
        self.assertFalse(g.repeated("c", "hello"))
        self.assertTrue(g.repeated("c", "hello"))
        self.assertFalse(g.repeated("other", "hello"))


class SafetyStoreTests(unittest.TestCase):
    def setUp(self):
        self.storage = Storage(":memory:")
        self.safety = Safety(self.storage, strike_limit=3, report_ban_threshold=3)

    def test_blocks_are_symmetric_and_persist(self):
        self.safety.block("a", "b")
        self.assertTrue(self.safety.blocked_between("a", "b"))
        self.assertTrue(self.safety.blocked_between("b", "a"))
        self.assertFalse(self.safety.blocked_between("a", "c"))
        self.assertEqual(Safety(self.storage).blocked_between("b", "a"), True)      # reloaded from SQLite

    def test_skip_cools_off_the_pair(self):
        self.safety.skip("a", "b")
        self.assertTrue(self.safety.blocked_between("b", "a"))
        self.safety.skip_avoid_s = -1
        self.safety.skip("a", "c")
        self.assertFalse(self.safety.blocked_between("a", "c"))

    def test_three_strikes_ban(self):
        self.assertEqual(self.safety.strike("x"), (1, False))
        self.assertEqual(self.safety.strike("x"), (2, False))
        self.assertEqual(self.safety.strike("x"), (3, True))
        self.assertGreater(self.safety.banned_for("x"), 0)
        self.assertEqual(self.safety.banned_for("y"), 0)

    def test_reports_dedupe_per_reporter_and_auto_ban_on_three_people(self):
        first = self.safety.report("r1", "p1", "bad", "harassment", "rude")
        again = self.safety.report("r1", "p1", "bad", "spam", "still rude")
        self.assertTrue(first["new"])
        self.assertFalse(again["new"])
        self.assertEqual(again["reporters_24h"], 1)
        self.safety.report("r2", "p2", "bad", "harassment", "")
        self.assertEqual(self.safety.banned_for("bad"), 0)
        last = self.safety.report("r3", "p3", "bad", "hate", "")
        self.assertTrue(last["auto_banned"])
        self.assertGreater(self.safety.banned_for("bad"), 0)


class SafetyFlowTests(unittest.TestCase):
    def setUp(self):
        self.app = make_app()
        self.svc = self.app.extensions["flyflirt"]
        self.a, self.b, self.room_id, _ma, _mb = pair_up(self.app, "friends", "friends")
        enter_room(self.a, self.room_id)
        enter_room(self.b, self.room_id)

    def test_blocked_message_is_not_delivered_and_counts_a_strike(self):
        self.a.emit("message", {"room_id": self.room_id, "text": "i will kill you"})
        blocked = self.a.wait("message_blocked")
        self.assertEqual(blocked["strikes_left"], 2)
        self.assertFalse(self.b.has("new_message"))
        self.assertEqual(self.svc.rooms.get(self.room_id).messages, [])

    def test_masked_message_is_delivered_masked(self):
        self.a.emit("message", {"room_id": self.room_id, "text": "well that is bullshit"})
        got = self.b.wait("new_message")
        self.assertNotIn("bullshit", got["text"])
        self.assertEqual(self.a.wait("message_masked")["reason"], "profanity")

    def test_third_strike_ends_the_chat_and_bans_the_sender(self):
        for _ in range(3):
            self.a.emit("message", {"room_id": self.room_id, "text": "send nudes"})
            self.a.drain("message_blocked")
        self.b.wait("room_ended")
        self.assertEqual(self.a.wait("error_message", where=lambda e: e["code"] == "banned")["code"], "banned")
        self.a.emit("queue_join", {"mode": "male"})
        self.assertEqual(self.a.wait("error_message", where=lambda e: e["code"] == "banned")["code"], "banned")

    def test_block_ends_chat_and_prevents_rematching(self):
        self.a.emit("block_user", {"room_id": self.room_id})
        self.a.wait("blocked")
        self.assertEqual(self.b.wait("room_ended")["by"], 0)           # the blocked person just sees a normal leave
        self.a.emit("queue_join", {"mode": "friends"})
        self.a.wait("queue_waiting")
        self.b.emit("queue_join", {"mode": "friends"})
        self.b.wait("queue_waiting")
        self.assertFalse(self.a.has("matched"))                        # still waiting: never paired with the blocker

    def test_skip_avoids_the_same_person_but_finds_others(self):
        self.a.emit("leave_room", {"room_id": self.room_id, "skip": True})
        self.b.wait("room_ended")
        self.a.emit("queue_join", {"mode": "friends"})
        self.a.wait("queue_waiting")
        self.b.emit("queue_join", {"mode": "friends"})
        self.b.wait("queue_waiting")
        self.assertFalse(self.a.has("matched"))
        c = Browser(self.app)
        c.emit("queue_join", {"mode": "friends"})
        c.wait("matched")

    def test_blocked_pair_cannot_join_each_others_invite(self):
        self.a.emit("block_user", {"room_id": self.room_id})
        self.a.wait("blocked")
        self.a.emit("invite_create", {"mode": "friends"})
        code = self.a.wait("invite_created")["code"]
        self.b.emit("invite_join", {"code": code})
        self.assertEqual(self.b.wait("invite_error")["reason"], "not_found")

    def test_report_endpoint_categories_dedupe_and_rate_limit(self):
        url = f"/api/rooms/{self.room_id}/report"
        r1 = self.a.http.post(url, json={"category": "harassment", "reason": "mean", "block": True})
        self.assertEqual(r1.status_code, 200)
        self.assertTrue(r1.get_json()["blocked"])
        r2 = self.a.http.post(url, json={"category": "spam"})
        self.assertTrue(r2.get_json()["updated"])
        self.assertTrue(self.svc.safety.blocked_between(self.svc.rooms.get(self.room_id).participant(0).client_id,
                                                        self.svc.rooms.get(self.room_id).participant(1).client_id))
        # report bucket is 3: the 4th within seconds is refused
        self.a.http.post(url, json={})
        self.assertEqual(self.a.http.post(url, json={}).status_code, 429)

    def test_cannot_report_an_empty_room(self):
        solo = Browser(self.app)
        solo.emit("invite_create", {"mode": "friends"})
        room_id = solo.wait("invite_created")["room_id"]
        self.assertEqual(solo.http.post(f"/api/rooms/{room_id}/report", json={}).status_code, 409)


if __name__ == "__main__":
    unittest.main()
