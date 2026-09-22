import json
import os
import tempfile
import time
import unittest

from flyflirt.services import build_services
from tests.helpers import Browser, enter_room, make_app, pair_up, send

CHAT = [
    ("a", "hey! I love your profile photo, that hike looks amazing 😊"),
    ("b", "haha thank you! it was freezing but so worth it. do you hike too?"),
    ("a", "honestly I'm more of a coffee-and-a-book person, but I'd try a hike with good company"),
    ("b", "good company is my specialty lol. what are you reading right now?"),
    ("a", "a ridiculous novel about a fruit fly that becomes a detective, weirdly wholesome"),
    ("b", "that sounds hilarious, I need to borrow it!"),
]


class MatchmakingFlowTests(unittest.TestCase):
    def setUp(self):
        self.app = make_app()

    def test_male_and_female_are_paired_and_get_distinct_slots(self):
        a, b, room_id, ma, mb = pair_up(self.app, "male", "female")
        self.assertEqual(ma["mode"], "flirt")
        self.assertEqual({ma["you"]["label"], mb["you"]["label"]}, {"Male", "Female"})
        self.assertEqual(ma["you"]["slot"], 0)          # first in line gets slot 0
        self.assertEqual(mb["you"]["slot"], 1)
        self.assertEqual(ma["partner"]["nickname"], "Ben")

    def test_preferences_decide_who_can_match(self):
        # a male who wants "a male" does not pair with a female who wants "a male"... but does with a male wanting "any"
        m1, f1, m2 = Browser(self.app), Browser(self.app), Browser(self.app)
        m1.emit("queue_join", {"mode": "male", "seeking": "male"})
        m1.wait("queue_waiting")
        f1.emit("queue_join", {"mode": "female", "seeking": "male"})     # m1 does not want a female
        f1.wait("queue_waiting")
        self.assertFalse(m1.has("matched"))
        m2.emit("queue_join", {"mode": "male", "seeking": "any"})
        matched = m1.wait("matched")
        self.assertEqual(matched["mode"], "friends")                     # same-gender pairings are friend chats
        self.assertEqual(m2.wait("matched")["room_id"], matched["room_id"])
        self.assertFalse(f1.has("matched"))

    def test_anyone_matches_anyone_and_explicit_opposites_flirt(self):
        a, b = Browser(self.app), Browser(self.app)
        a.emit("queue_join", {"mode": "friends", "seeking": "any"})
        a.wait("queue_waiting")
        b.emit("queue_join", {"mode": "female", "seeking": "any"})
        self.assertEqual(b.wait("matched")["mode"], "friends")
        c, d = Browser(self.app), Browser(self.app)
        c.emit("queue_join", {"mode": "male", "seeking": "female"})
        c.wait("queue_waiting")
        d.emit("queue_join", {"mode": "female", "seeking": "male"})
        self.assertEqual(d.wait("matched")["mode"], "flirt")

    def test_friends_pair_with_friends_only(self):
        a, b, _room, ma, _mb = pair_up(self.app, "friends", "friends")
        self.assertEqual(ma["mode"], "friends")
        self.assertEqual(ma["you"]["label"], "Friend")

    def test_same_gender_or_mixed_modes_do_not_match(self):
        a, b = Browser(self.app), Browser(self.app)
        a.emit("queue_join", {"mode": "male"})
        b.emit("queue_join", {"mode": "male"})
        a.wait("queue_waiting")
        b.wait("queue_waiting")
        c = Browser(self.app)
        c.emit("queue_join", {"mode": "friends"})
        c.wait("queue_waiting")
        for browser in (a, b, c):
            self.assertFalse(browser.has("matched"))
        self.assertEqual(self.app.extensions["flyflirt"].matchmaker.counts(), {"male": 2, "female": 0, "friends": 1})

    def test_cancel_removes_from_queue(self):
        a = Browser(self.app)
        a.emit("queue_join", {"mode": "female"})
        a.wait("queue_waiting")
        a.emit("queue_cancel")
        a.wait("queue_cancelled")
        b = Browser(self.app)
        b.emit("queue_join", {"mode": "male"})
        b.wait("queue_waiting")
        self.assertFalse(b.has("matched"))

    def test_bad_input_is_rejected(self):
        a = Browser(self.app)
        a.emit("queue_join", {"mode": "robot"})
        self.assertEqual(a.wait("error_message")["code"], "bad_mode")
        a.emit("queue_join", "not a dict")
        self.assertEqual(a.wait("error_message")["code"], "bad_mode")

    def test_refresh_keeps_your_place_in_line(self):
        a = Browser(self.app)
        a.emit("queue_join", {"mode": "male"})
        a.wait("queue_waiting")
        first = self.app.extensions["flyflirt"].matchmaker.get(a.http.get_cookie("ff_session") and self._cid(a)).joined_at
        time.sleep(0.05)
        a.emit("queue_join", {"mode": "male"})  # same client, e.g. page refresh
        a.wait("queue_waiting")
        second = self.app.extensions["flyflirt"].matchmaker.get(self._cid(a)).joined_at
        self.assertEqual(first, second)
        self.assertEqual(self.app.extensions["flyflirt"].matchmaker.counts()["male"], 1)

    def _cid(self, browser):
        with browser.http.session_transaction() as sess:
            return sess["cid"]

    def test_a_person_cannot_match_with_themselves_in_two_tabs(self):
        a = Browser(self.app)
        a.emit("queue_join", {"mode": "male"})
        a.wait("queue_waiting")
        second_tab = self.app.test_client()
        second_tab.set_cookie("ff_session", a.http.get_cookie("ff_session").value)
        from flyflirt import socketio
        tab = socketio.test_client(self.app, flask_test_client=second_tab)
        tab.emit("queue_join", {"mode": "female"})
        received = tab.get_received()
        self.assertFalse(any(e["name"] == "matched" for e in received))


class InviteFlowTests(unittest.TestCase):
    def svc_codes(self):
        return set(self.app.extensions["flyflirt"].rooms._codes)

    def setUp(self):
        self.app = make_app()

    def test_invite_code_connects_two_friends(self):
        a, b = Browser(self.app), Browser(self.app)
        a.emit("invite_create", {"mode": "friends", "nickname": "Ana"})
        invite = a.wait("invite_created")
        self.assertRegex(invite["code"], r"^[2-9A-HJ-NP-Z]{6}$")
        self.assertEqual(invite["path"], f"/join/{invite['code']}")
        b.emit("invite_join", {"code": invite["code"].lower(), "nickname": "Ben"})   # case-insensitive
        mb, ma = b.wait("matched"), a.wait("matched")
        self.assertEqual(mb["room_id"], ma["room_id"])
        self.assertEqual(ma["mode"], "friends")
        self.assertEqual(ma["partner"]["nickname"], "Ben")

    def test_invited_buddies_get_a_friends_chat(self):
        a, b = Browser(self.app), Browser(self.app)
        a.emit("invite_create", {"mode": "friends"})
        b.emit("invite_join", {"code": a.wait("invite_created")["code"]})
        mb = b.wait("matched")
        self.assertEqual(mb["mode"], "friends")
        self.assertEqual(mb["you"]["label"], "Friend")

    def test_invite_code_is_deleted_when_the_chat_ends(self):
        a, b = Browser(self.app), Browser(self.app)
        a.emit("invite_create", {"mode": "friends"})
        code = a.wait("invite_created")["code"]
        b.emit("invite_join", {"code": code})
        room_id = b.wait("matched")["room_id"]
        a.wait("matched")
        self.assertNotIn(code, self.svc_codes())            # used up the moment the buddy joins
        for person in (a, b):
            person.emit("room_join", {"room_id": room_id})
            person.wait("room_state")
        b.emit("leave_room", {"room_id": room_id})
        a.wait("room_ended")
        c = Browser(self.app)
        c.emit("invite_join", {"code": code})
        self.assertEqual(c.wait("invite_error")["reason"], "not_found")

    def test_host_leaving_before_anyone_joins_deletes_the_code(self):
        a, c = Browser(self.app), Browser(self.app)
        a.emit("invite_create", {"mode": "friends"})
        payload = a.wait("invite_created")
        a.emit("room_join", {"room_id": payload["room_id"]})
        a.wait("room_state")
        a.emit("leave_room", {"room_id": payload["room_id"]})
        a.wait("room_ended")
        c.emit("invite_join", {"code": payload["code"]})
        self.assertEqual(c.wait("invite_error")["reason"], "not_found")

    def test_cancelling_the_invite_deletes_the_code(self):
        a, c = Browser(self.app), Browser(self.app)
        a.emit("invite_create", {"mode": "friends"})
        code = a.wait("invite_created")["code"]
        a.emit("queue_cancel")
        a.wait("queue_cancelled")
        c.emit("invite_join", {"code": code})
        self.assertEqual(c.wait("invite_error")["reason"], "not_found")

    def test_bad_codes_and_reuse_are_rejected(self):
        a, b, c = Browser(self.app), Browser(self.app), Browser(self.app)
        b.emit("invite_join", {"code": "nope"})
        self.assertEqual(b.wait("invite_error")["reason"], "invalid")
        b.emit("invite_join", {"code": "ABCDEF"})
        self.assertEqual(b.wait("invite_error")["reason"], "not_found")
        a.emit("invite_create", {"mode": "friends"})
        code = a.wait("invite_created")["code"]
        a.emit("invite_join", {"code": code})
        self.assertEqual(a.wait("invite_error")["reason"], "own")
        b.emit("invite_join", {"code": code})
        b.wait("matched")
        c.emit("invite_join", {"code": code})                      # already used
        self.assertIn(c.wait("invite_error")["reason"], ("full", "not_found"))

    def test_code_guessing_is_rate_limited(self):
        app = make_app(CODE_GUESS_BURST=3, CODE_GUESS_REFILL_PER_S=0.001)
        b = Browser(app)
        reasons = []
        for _ in range(6):
            b.emit("invite_join", {"code": "ABCDEF"})
            reasons.append(b.wait("invite_error")["reason"])
        self.assertIn("rate_limited", reasons)

    def test_invite_is_reused_not_duplicated(self):
        a = Browser(self.app)
        a.emit("invite_create", {"mode": "friends"})
        first = a.wait("invite_created")
        a.emit("queue_join", {"mode": "friends"})
        a.wait("queue_waiting")
        a.emit("invite_create", {"mode": "friends"})
        self.assertEqual(a.wait("invite_created")["code"], first["code"])

    def test_queue_match_discards_pending_invite(self):
        a, b = Browser(self.app), Browser(self.app)
        a.emit("queue_join", {"mode": "male"})
        a.wait("queue_waiting")
        a.emit("invite_create", {"mode": "male"})
        code = a.wait("invite_created")["code"]
        b.emit("queue_join", {"mode": "female"})
        a.wait("matched")
        c = Browser(self.app)
        c.emit("invite_join", {"code": code})
        self.assertEqual(c.wait("invite_error")["reason"], "not_found")

    def test_timeout_suggests_an_invite_code(self):
        from flyflirt.sockets import start_background_loops
        app = make_app(MATCH_TIMEOUT_S=1)
        start_background_loops(__import__("flyflirt").socketio, app.extensions["flyflirt"])
        a = Browser(app)
        a.emit("queue_join", {"mode": "female", "nickname": "Solo"})
        a.wait("queue_waiting")
        suggestion = a.wait("invite_suggested", timeout=6)
        self.assertEqual(len(suggestion["code"]), 6)
        status = a.wait("queue_status", timeout=4)
        self.assertEqual(status["timeout"], 1)
        # and the code really works for a friend
        friend = Browser(app)
        friend.emit("invite_join", {"code": suggestion["code"]})
        self.assertEqual(friend.wait("matched")["you"]["label"], "Male")
        self.assertTrue(a.wait("matched"))


class ChatFlowTests(unittest.TestCase):
    def setUp(self):
        self.app = make_app()
        self.svc = self.app.extensions["flyflirt"]
        self.a, self.b, self.room_id, _, _ = pair_up(self.app)
        self.state_a = enter_room(self.a, self.room_id)
        self.state_b = enter_room(self.b, self.room_id)

    def chat(self, n=6):
        who = {"a": self.a, "b": self.b}
        updates = []
        for speaker, text in CHAT[:n]:
            updates.append(send(who[speaker], self.room_id, text, [self.a, self.b]))
        return updates

    def test_room_state_for_late_or_returning_joiners(self):
        self.assertEqual(self.state_a["you"], 0)
        self.assertEqual(self.state_b["you"], 1)
        self.assertEqual(self.state_a["messages"], [])
        self.assertFalse(self.state_a["verdict_ready"])
        self.assertEqual(self.state_a["min_for_verdict"], 3)
        self.assertEqual(self.state_a["totals"]["cells_total"], 1350)
        self.assertEqual(len(self.state_a["room"]["participants"]), 2)

    def test_pages_are_private_to_participants(self):
        self.assertEqual(self.a.http.get(f"/chat/{self.room_id}").status_code, 200)
        self.assertEqual(self.b.http.get(f"/chat/{self.room_id}").status_code, 200)
        outsider = Browser(self.app)
        self.assertEqual(outsider.http.get(f"/chat/{self.room_id}").status_code, 403)
        self.assertEqual(outsider.http.get(f"/verdict/{self.room_id}").status_code, 403)
        self.assertEqual(outsider.http.get("/chat/does-not-exist").status_code, 404)
        outsider.emit("room_join", {"room_id": self.room_id})
        self.assertEqual(outsider.wait("error_message")["code"], "forbidden")
        outsider.emit("message", {"room_id": self.room_id, "text": "let me in"})
        self.assertEqual(outsider.wait("error_message")["code"], "forbidden")

    def test_messages_flow_through_analysis_simulation_and_brain_waves(self):
        self.a.emit("message", {"room_id": self.room_id, "text": "haha you made me laugh out loud, thank you!"})
        got = self.b.wait("new_message")
        self.assertEqual(got["slot"], 0)
        self.assertEqual(got["text"], "haha you made me laugh out loud, thank you!")
        theirs = self.b.wait("fly_update")          # the partner gets no analysis, only shared numbers
        self.assertFalse(theirs["private"])
        for key in ("params", "diary", "narration", "llm", "topic", "channels"):
            self.assertNotIn(key, theirs)
        self.assertIn("meter", theirs)
        self.assertTrue(theirs["tip"])                 # the reply tip is for the recipient
        update = self.a.wait("fly_update")          # the author gets the full reading
        self.assertTrue(update["private"])
        self.assertNotIn("tip", update)                 # ...and never shown to the author
        self.assertEqual(set(update["params"]),
                         {"warmth", "humor", "reciprocity", "curiosity", "disclosure", "energy", "tension"})
        self.assertIn("plain", update["narration"])
        self.assertGreaterEqual(update["counts"]["new_cells"], 0)
        self.assertTrue(update["llm"]["degraded"])          # test config has the LLM disabled -> local scorer
        wave = self.b.wait("brain_wave")
        self.assertEqual(len(wave["frames"]), self.svc.engine_cfg.substeps)
        self.assertEqual(len(wave["state"]["idx"]), len(wave["state"]["val"]))
        self.assertEqual(update["seq"], 0)

    def test_room_state_hides_the_partners_analysis(self):
        send(self.a, self.room_id, "a warm and curious first message!", [self.a, self.b])
        mine = enter_room(self.a, self.room_id)["messages"][0]
        theirs = enter_room(self.b, self.room_id)["messages"][0]
        self.assertIsNotNone(mine["params"])
        self.assertTrue(theirs["analysed"])
        for key in ("params", "narration", "topic"):
            self.assertIsNone(theirs[key])
        self.assertEqual(theirs["diary"], "")
        self.assertTrue(theirs["tip"])
        self.assertIsNone(mine["tip"])

    def test_messages_are_processed_in_order(self):
        for text in ("one", "two", "three", "four"):
            self.a.emit("message", {"room_id": self.room_id, "text": text})
        seqs = [self.b.wait("fly_update", 8)["seq"] for _ in range(4)]
        self.assertEqual(seqs, [0, 1, 2, 3])
        self.assertEqual([m.text for m in self.svc.rooms.get(self.room_id).messages], ["one", "two", "three", "four"])

    def test_verdict_gating_then_per_message_attribution(self):
        code, body = self.a.json(f"/api/rooms/{self.room_id}/verdict")
        self.assertEqual(code, 409)
        self.assertEqual(body["error"], "not_ready")
        self.chat(6)
        code, v = self.a.json(f"/api/rooms/{self.room_id}/verdict")
        self.assertEqual(code, 200, v)

        self.assertEqual(v["messages_analysed"], 6)
        self.assertEqual(len(v["messages"]), 6)
        totals = v["totals"]
        self.assertGreater(totals["cells_active"], 0)
        self.assertGreater(totals["edges_active"], 0)
        self.assertLessEqual(totals["cells_active"], totals["cells_total"])
        self.assertEqual(sum(r["active"] for r in v["by_role"].values()), totals["cells_active"])

        # every activated neuron is credited to exactly one message
        self.assertEqual(sum(m["responsible_cells"] for m in v["messages"]), totals["cells_active"])
        # ... and every activated connection to exactly one message (edges from cells with no credit excluded)
        self.assertLessEqual(sum(m["responsible_edges"] for m in v["messages"]), totals["edges_active"])
        self.assertAlmostEqual(sum(m["share"] for m in v["messages"]), 1.0, places=2)
        # first-recruitment counts also add up
        self.assertEqual(sum(m["recruited_cells"] for m in v["messages"]), totals["cells_active"])

        first = v["messages"][0]
        self.assertEqual(first["text"], CHAT[0][1])
        self.assertEqual(first["nickname"], "Ana")
        self.assertTrue(set(first["footprint"]) == {"cells", "edges"})
        self.assertLessEqual(len(first["footprint"]["cells"]), 250)
        self.assertIn(v["tier"], {"resonance", "warming", "quiet", "friction"})
        self.assertTrue(v["headline"])
        self.assertIn("not destiny", v["disclaimer"])
        self.assertTrue(v["share_url"].endswith(f"/card/{self.svc.rooms.get(self.room_id).share_token}"))

    def test_verdict_is_private_but_card_is_public_and_text_free(self):
        self.chat(6)
        _, v = self.a.json(f"/api/rooms/{self.room_id}/verdict")
        outsider = Browser(self.app)
        self.assertEqual(outsider.json(f"/api/rooms/{self.room_id}/verdict")[0], 403)
        token = v["share_url"].rsplit("/", 1)[1]
        code, card = outsider.json(f"/api/card/{token}")
        self.assertEqual(code, 200)
        dump = json.dumps(card)
        for _, text in CHAT:
            self.assertNotIn(text[:25], dump, "the public card must never contain chat text")
        self.assertNotIn("Ana", dump)
        self.assertGreater(len(card["active_cells"]), 0)
        self.assertEqual(outsider.http.get(f"/card/{token}").status_code, 200)
        self.assertEqual(outsider.json("/api/card/not-a-token")[0], 404)

    def test_verdict_waits_for_queued_analysis(self):
        for speaker, text in CHAT[:6]:
            (self.a if speaker == "a" else self.b).emit("message", {"room_id": self.room_id, "text": text})
        # regardless of timing the API must either be consistent or ask us to retry, never half-baked
        for _ in range(60):
            code, body = self.a.json(f"/api/rooms/{self.room_id}/verdict")
            if code == 200:
                self.assertEqual(body["messages_analysed"], 6)
                break
            self.assertIn(body["error"], {"analysing", "not_ready"})
            time.sleep(0.1)
        else:
            self.fail("verdict never became available")

    def test_input_is_sanitised_and_limited(self):
        self.a.emit("message", {"room_id": self.room_id, "text": "   ​\x07\x00   "})
        self.a.emit("message", {"room_id": self.room_id, "text": ""})
        self.a.emit("message", {"room_id": self.room_id, "text": 12345})
        time.sleep(0.2)
        self.assertFalse(self.b.has("new_message"))
        limit = self.svc.config.MAX_MESSAGE_LEN
        self.a.emit("message", {"room_id": self.room_id, "text": ("lorem ipsum dolor sit amet " * 60)[: limit * 3]})
        self.assertIn(len(self.b.wait("new_message")["text"]), (limit, limit - 1))   # cut to the limit (trailing space trimmed)
        self.a.emit("message", {"room_id": self.room_id, "text": "hi\n\n\tthere\x00 <script>alert(1)</script>"})
        self.assertEqual(self.b.wait("new_message")["text"], "hi there <script>alert(1)</script>")  # stored verbatim, escaped by the UI

    def test_flood_control(self):
        app = make_app(MSG_BURST=2, MSG_REFILL_PER_S=0.0001)
        a, b, room_id, _, _ = pair_up(app)
        enter_room(a, room_id)
        enter_room(b, room_id)
        for i in range(4):
            a.emit("message", {"room_id": room_id, "text": f"spam {i}"})
        self.assertEqual(a.wait("error_message")["code"], "slow_down")

    def test_leaving_ends_the_chat_but_keeps_the_verdict(self):
        self.chat(6)
        self.b.emit("leave_room", {"room_id": self.room_id})
        ended = self.a.wait("room_ended")
        self.assertEqual(ended["by"], 1)
        self.a.emit("message", {"room_id": self.room_id, "text": "still there?"})
        self.assertEqual(self.a.wait("error_message")["code"], "not_active")
        self.assertEqual(self.a.json(f"/api/rooms/{self.room_id}/verdict")[0], 200)

    def test_presence_and_typing(self):
        self.a.pump()
        self.b.emit("typing", {"room_id": self.room_id, "on": True})
        self.assertEqual(self.a.wait("typing"), {"slot": 1, "on": True})
        self.b.disconnect()
        presence = self.a.wait("presence", where=lambda p: not p["participants"][1]["connected"])
        self.assertFalse(presence["participants"][1]["connected"])

    def test_lab_receives_anonymous_pulses(self):
        lab = Browser(self.app)
        lab.emit("lab_subscribe")
        lab.wait("lab_stats")
        send(self.a, self.room_id, "hello there, lovely weather today!", [self.a, self.b])
        pulse = lab.wait("lab_pulse")
        self.assertEqual(set(pulse), {"frames"})
        self.assertEqual(set(pulse["frames"][0]), {"cells", "acts"})
        self.assertNotIn("text", json.dumps(pulse))

    def test_report_endpoint(self):
        code = self.a.http.post(f"/api/rooms/{self.room_id}/report", json={"reason": "rude"}).status_code
        self.assertEqual(code, 200)
        outsider = Browser(self.app)
        self.assertEqual(outsider.http.post(f"/api/rooms/{self.room_id}/report", json={}).status_code, 403)


class RestoreTests(unittest.TestCase):
    def test_room_is_rebuilt_from_the_database_after_a_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "test.db")
            app = make_app(DB_PATH=path)
            a, b, room_id, _, _ = pair_up(app)
            enter_room(a, room_id)
            enter_room(b, room_id)
            for speaker, text in CHAT:
                send(a if speaker == "a" else b, room_id, text, [a, b])
            live = app.extensions["flyflirt"].rooms.get(room_id)
            meter_before = live.engine.meter()
            _, verdict_before = a.json(f"/api/rooms/{room_id}/verdict")
            app.extensions["flyflirt"].storage.close()

            # "restart": a brand-new service container over the same database file
            cfg = type("Cfg", (app.extensions["flyflirt"].config,), {"DB_PATH": path})
            fresh = build_services(cfg)
            restored = fresh.rooms.get(room_id)
            self.assertIsNotNone(restored)
            self.assertEqual(len(restored.messages), 6)
            self.assertAlmostEqual(restored.engine.meter(), meter_before, places=5)
            self.assertEqual([m.text for m in restored.messages], [t for _, t in CHAT])
            self.assertEqual(restored.participants[0].nickname, "Ana")
            from flyflirt.verdict import build_verdict
            v = build_verdict(restored, fresh.connectome, fresh.engine_cfg)
            self.assertEqual(v["totals"], verdict_before["totals"])
            self.assertEqual([m["responsible_cells"] for m in v["messages"]],
                             [m["responsible_cells"] for m in verdict_before["messages"]])
            fresh.storage.close()


class PageTests(unittest.TestCase):
    def setUp(self):
        self.app = make_app()
        self.c = self.app.test_client()

    def test_public_pages_render(self):
        for path in ("/", "/match?mode=male", "/match?mode=female&nick=Zed", "/match?mode=friends", "/join/ABC234",
                     "/join/garbage", "/how", "/lab", "/healthz"):
            response = self.c.get(path)
            self.assertEqual(response.status_code, 200, path)

    def test_invalid_mode_redirects_home(self):
        self.assertEqual(self.c.get("/match?mode=alien").status_code, 302)

    def test_api_endpoints(self):
        stats = self.c.get("/api/stats").get_json()
        self.assertEqual(stats["neurons"], 1350)
        self.assertEqual(stats["synapses"], 50158)
        status = self.c.get("/api/llm/status").get_json()
        self.assertIn("chain", status)
        self.assertEqual(self.c.get("/api/connectome").get_json()["summary"]["cells"], 1350)
        self.assertEqual(self.c.get("/nope").status_code, 404)
        self.assertEqual(self.c.get("/api/nope").get_json()["error"], "not_found")

    def test_security_headers(self):
        h = self.c.get("/").headers
        self.assertEqual(h["X-Content-Type-Options"], "nosniff")
        self.assertEqual(h["X-Frame-Options"], "DENY")
        self.assertIn("script-src 'self'", h["Content-Security-Policy"])
        self.assertEqual(h["Cache-Control"], "no-store")

    def test_session_cookie_flags(self):
        cookie = self.c.get("/").headers.get("Set-Cookie", "")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)

    def test_production_requires_a_secret_key(self):
        from flyflirt import create_app
        from flyflirt.config import Config
        cfg = type("Prod", (Config,), {"ENV": "production", "SECRET_KEY": None, "DB_PATH": ":memory:"})
        with self.assertRaises(RuntimeError):
            create_app(cfg)


if __name__ == "__main__":
    unittest.main()
