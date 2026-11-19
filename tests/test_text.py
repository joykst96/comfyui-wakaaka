"""노드 `Wakaaka Text`: 줄 주석, 이름 해석, 묶음 합치기, 난수 경로, 오프라인, 훅과 노드 (가짜 서버 + 임시 캐시)."""

import json
import tempfile
import unittest

from wakaaka_node import client, hook, jcs, text as T
from wakaaka_node.cache import Cache
from wakaaka_node.expander import Expander, cleanup, derive, expand
from wakaaka_node.fill import FillError
from wakaaka_node.node import RUN_TOKEN, WakaakaText
from wakaaka_node.settings import Settings, load
from wakaaka_node.userlist import UserLists, suggest

from .fakeserver import FakeServer

HAIR, EYES, GIRL, CLOTH = "HairHairHair", "EyesEyesEyes", "GirlGirlGirl", "ClothClothXX"


def doc(*texts, **extra):
    return {"schema": 1, "items": [{"id": f"i{n}", "text": t} for n, t in enumerate(texts)], **extra}


class World:
    """와일드카드 ID → 문서. 참조는 `${w:ID}`. 루트마다 닫힌 묶음을 만든다."""

    def __init__(self, docs):
        self.docs = docs

    def bundle(self, wid):
        lock, todo = {}, [wid]
        while todo:
            w = todo.pop()
            if w in lock:
                continue
            lock[w] = jcs.content_hash(self.docs[w])
            for it in self.docs[w]["items"]:
                t = it.get("text", "")
                i = 0
                while (i := t.find("${w:", i)) >= 0:
                    todo.append(t[i + 4 : i + 16])
                    i += 16
        return {"format": 1, "root": lock[wid], "lock": lock, "docs": {h: self.docs[w] for w, h in lock.items()}}

    def routes(self, users=None):
        r = {f"/api/w/{w}/bundle": (200, jcs.canonicalize(self.bundle(w)).encode(), 0) for w in self.docs}
        for name, items in (users or {}).items():
            r[f"/api/users/{name}/wildcards"] = (200, {"user": {"username": name}, "wildcards": items}, 0)
        return r


def base_world():
    return World({
        HAIR: doc("red hair", "blue hair", "{3*silver|platinum} hair"),
        EYES: doc("green eyes", "red eyes"),
        CLOTH: doc("dress", "{school|sailor} uniform", "hoodie"),
        GIRL: doc("1girl, ${w:%s}, ${w:%s}" % (HAIR, EYES), select={"count": [1, 1]}),
    })


USER_ITEMS = [
    {"id": HAIR, "name": "hair", "owner": "me", "version": 1, "hash": "x", "fixed": False},
    {"id": EYES, "name": "Eyes", "owner": "me", "version": 2, "hash": "x", "fixed": True},
    {"id": GIRL, "name": "girl preset", "owner": "me", "version": 3, "hash": "x", "fixed": False},
    {"id": CLOTH, "name": "옷 > 상의", "owner": "me", "version": 1, "hash": "x", "fixed": False},
]


def ctx(seed, *labels):
    for lb in labels:
        seed = derive(seed, lb)
    return seed


class Base(unittest.TestCase):
    def setUp(self):
        client.forget_failures()
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = Cache(self.tmp.name)
        self.users = UserLists()
        self.world = base_world()
        self.srv = FakeServer(self.world.routes({"mine": USER_ITEMS}))
        self.settings = Settings("server", self.srv.url, "mine")

    def tearDown(self):
        self.srv.close()
        self.tmp.cleanup()

    def fill(self, text, seed=0, refs="", settings=None):
        return T.fill_text(text, seed, refs, settings or self.settings, cache=self.cache, userlists=self.users)

    def requests(self, part):
        return [r["path"] for r in self.srv.requests if part in r["path"]]


class CommentLines(unittest.TestCase):
    def test_strip(self):
        cases = [
            ("a\n#c\nb", "a\nb"),
            ("#c\nb", "b"),
            ("a\n#c", "a"),
            ("#only", ""),
            (" #a\n#b", " #a"),  # 앞 공백이 있으면 주석이 아니다
            ("\\#compass, a", "\\#compass, a"),  # 남은 첫 줄의 \#은 SPEC 시작 이스케이프 그대로
            ("#c\n\\#compass", "\\#compass"),  # 주석 줄을 지워 첫 줄이 된 경우도
            ("a\n\\#compass", "a\n#compass"),  # 둘째 줄부터는 글자 #
            ("a\n\\\\#b", "a\n\\\\#b"),  # \\#은 \#이 아니다
            ("x\r\n#c\r\ny", "x\r\ny"),
            ("{a|\n#b}", "{a|"),  # 줄 단위: 여러 줄 선택지 안의 # 줄도 지운다(DESIGN)
        ]
        for src, want in cases:
            self.assertEqual(T.strip_comment_lines(src), want, src)


class Expand(Base):
    def test_no_refs_still_parsed(self):
        r = self.fill("1girl, {smile|frown}, \\{literal\\}, a\\|b")
        self.assertIn(r.text, ["1girl, smile, {literal}, a\\|b", "1girl, frown, {literal}, a\\|b"])
        self.assertEqual((r.refs, r.lock, r.source), ({}, {}, "local"))
        self.assertEqual(self.requests("/api/"), [])  # 참조가 없으면 서버를 부르지 않는다

    def test_comment_lines_and_escape(self):
        self.assertEqual(self.fill("#memo {broken ${nothing\n\\#compass, a\n\\#b").text, "#compass, a\n#b")

    def test_random_path_is_item_t(self):
        # 텍스트 문맥은 H(seed, ["item:t"]). 번호 p 참조는 H(seed, ["item:t", "pos:p"])로 대상을 전개한다
        hb, eb = self.world.bundle(HAIR), self.world.bundle(EYES)
        for seed in [0, 1, 2, 77, (1 << 64) - 1]:
            self.assertEqual(self.fill("${hair}", seed).text, expand(hb, ctx(seed, "item:t", "pos:0")))
            want = expand(hb, ctx(seed, "item:t", "pos:1")) + " " + expand(eb, ctx(seed, "item:t", "pos:2"))
            self.assertEqual(self.fill("{a|b|c}${hair} ${eyes}", seed).text[1:].lstrip(), want)

    def test_not_same_as_wildcard_node(self):
        # 특례를 두지 않는다(DESIGN 8절): ${X} 하나뿐이어도 X를 seed로 바로 전개한 것과 같지 않을 수 있다
        hb = self.world.bundle(GIRL)
        diff = sum(self.fill("${girl preset}", s).text != expand(hb, s) for s in range(40))
        self.assertGreater(diff, 0)

    def test_comment_lines_do_not_change_result(self):
        base = "1girl, ${hair}, {smile|frown|grin}\n${Eyes}, {a|b|c|d}"
        noisy = "# 머리말 ${없는것} {깨진\n1girl, ${hair}, {smile|frown|grin}\n#중간 {x|y}\n${Eyes}, {a|b|c|d}\n# 끝"
        for seed in range(60):
            self.assertEqual(self.fill(base, seed).text, self.fill(noisy, seed).text)

    def test_same_input_same_result(self):
        t = "${girl preset}, {1*a|2*b|3*c}, ${옷 > 상의}"
        outs = {self.fill(t, s).text for s in range(30)}
        self.assertGreater(len(outs), 3)
        for s in [0, 5, 29]:
            self.assertEqual(self.fill(t, s).text, self.fill(t, s).text)

    def test_multiple_refs_merge_lock(self):
        r = self.fill("${girl preset}, ${w:%s}, ${hair}" % CLOTH)
        self.assertEqual(r.refs, {"girl preset": GIRL, "hair": HAIR})  # ${w:ID}는 표에 넣지 않는다
        want = {}
        for w in (GIRL, CLOTH, HAIR):
            want.update(self.world.bundle(w)["lock"])
        self.assertEqual(r.lock, want)
        self.assertEqual(set(r.lock), {GIRL, CLOTH, HAIR, EYES})

    def test_syntax_errors(self):
        for t, code in [("{a", "E_UNCLOSED_CHOICE"), ("a}", "E_UNMATCHED_CLOSE"), ("${hair", "E_UNCLOSED_REF"),
                        ("${}", "E_REF_EMPTY"), ("${a:b}", "E_REF_INVALID"), ("${x=1}", "E_REF_RESERVED"), ("{0*a}", "E_CHOICE_WEIGHT")]:
            with self.assertRaises(FillError) as cm:
                self.fill(t)
            self.assertIn(code, str(cm.exception))

    def test_bad_seed(self):
        for s in [-1, 1 << 64, True, "1", None]:
            with self.assertRaises(FillError):
                self.fill("a", s)


class Names(Base):
    def test_table_first_then_list_case_insensitive(self):
        refs = T.dump_refs_value({"HAIR": EYES, "old": CLOTH}, {})
        r = self.fill("${hair} ${eyes} ${GIRL PRESET}", 3, refs)
        # 표가 목록보다 먼저(hair → EYES), 표에 없는 이름은 목록(대소문자 무시), text에 없는 이름(old)은 지운다
        self.assertEqual(r.refs, {"hair": EYES, "eyes": EYES, "GIRL PRESET": GIRL})
        self.assertEqual(len(self.requests("/api/users/")), 1)

    def test_table_only_needs_no_list(self):
        refs = T.dump_refs_value({"hair": HAIR}, {})
        r = self.fill("${hair}", 0, refs, Settings("server", self.srv.url, ""))
        self.assertEqual(r.refs, {"hair": HAIR})
        self.assertEqual(self.requests("/api/users/"), [])

    def test_missing_names_listed(self):
        with self.assertRaises(FillError) as cm:
            self.fill("${hair}, ${nope}, ${also nope}, ${nope}")
        msg = str(cm.exception)
        self.assertIn("${nope}, ${also nope}", msg)
        self.assertNotIn("${hair}", msg)
        for s, why in [(Settings("server", self.srv.url, ""), "유저 아이디를 넣으세요"),
                       (Settings("server", self.srv.url, "a b"), "올바르지 않음"),
                       (Settings("server", self.srv.url, "ghost"), "유저 ghost가 없음")]:
            with self.assertRaises(FillError) as cm:
                self.fill("${hair}", 0, "", s)
            self.assertIn(why, str(cm.exception))

    def test_list_is_cached_and_stale_on_failure(self):
        now = [0.0]
        users = UserLists(clock=lambda: now[0])
        items, state = users.get(self.settings)
        self.assertEqual((len(items), state), (4, "ok"))
        users.get(self.settings)
        self.assertEqual(len(self.requests("/api/users/")), 1)  # 60초 안
        now[0] = 61
        self.srv.routes["/api/users/mine/wildcards"] = (500, {}, 0)
        self.assertEqual(users.get(self.settings)[1], "stale")  # 받지 못하면 마지막 목록
        self.assertEqual(len(users.get(self.settings)[0]), 4)
        self.assertEqual(users.get(Settings("server", self.srv.url, ""))[1], "no_user")
        self.assertEqual(users.get(Settings("server", self.srv.url, "x"))[1], "bad_user")
        client.forget_failures()  # 500은 그 서버를 10초 동안 부르지 않게 한다(client.py)
        self.assertEqual(UserLists().get(Settings("server", self.srv.url, "ghost"))[1], "not_found")

    def test_suggest_rank(self):
        items = [{"id": f"{i:012d}", "name": n, "fixed": False} for i, n in enumerate(["Hair color", "long hair", "hair", "Hairband", "chair", "eyes"])]
        self.assertEqual([w["name"] for w in suggest(items, " HAIR ")], ["hair", "Hair color", "Hairband", "chair", "long hair"])
        self.assertEqual(len(suggest(items * 10, "")), 30)
        self.assertEqual(suggest(items, "zzz"), [])


class Bundles(Base):
    def test_hash_conflict_is_refetched_then_error(self):
        # GIRL의 묶음은 옛 HAIR, HAIR의 최신 묶음은 새 HAIR: 받는 사이에 갱신된 경우
        old = self.world.bundle(GIRL)
        self.world.docs[HAIR] = doc("black hair")
        self.srv.routes[f"/api/w/{HAIR}/bundle"] = (200, jcs.canonicalize(self.world.bundle(HAIR)).encode(), 0)
        self.srv.routes[f"/api/w/{GIRL}/bundle"] = (200, jcs.canonicalize(old).encode(), 0)
        with self.assertRaises(FillError) as cm:
            self.fill("${girl preset} ${hair}")
        self.assertIn("엇갈림: " + HAIR, str(cm.exception))
        self.assertEqual(len(self.requests(f"/api/w/{GIRL}/bundle")), 2)  # 한 번 다시 받았다

    def test_offline_uses_cache(self):
        t = "${girl preset}, ${옷 > 상의}, {a|b}"
        online = [self.fill(t, s) for s in range(10)]
        self.srv.close()
        client.forget_failures()
        users = self.users  # 이름은 표로 해석한다(목록은 60초 캐시도 있다)
        for s, r in enumerate(online):
            off = T.fill_text(t, s, T.dump_refs_value(r.refs, r.lock), self.settings, cache=self.cache, userlists=users)
            self.assertEqual((off.text, off.lock, off.source), (r.text, r.lock, "cache"))
        with self.assertRaises(FillError) as cm:
            T.fill_text("${w:ZZZZZZZZZZZZ}", 0, "", self.settings, cache=self.cache, userlists=users)
        self.assertIn("캐시도 없음", str(cm.exception))

    def test_missing_wildcard(self):
        with self.assertRaises(FillError) as cm:
            self.fill("${w:ZZZZZZZZZZZZ}")
        self.assertIn("찾을 수 없음: ZZZZZZZZZZZZ", str(cm.exception))

    def test_depth_17_allowed(self):
        ids = [f"Chain{n:07d}" for n in range(18)]
        docs = {ids[n]: doc("c%d ${w:%s}" % (n, ids[n + 1])) for n in range(17)}
        docs[ids[17]] = doc("end")
        w = World(docs)
        with FakeServer(w.routes()) as srv:
            s = Settings("server", srv.url)
            # ids[1]은 깊이 16: Wakaaka Wildcard에서 되는 것은 텍스트에서도 된다
            self.assertTrue(Expander(w.bundle(ids[1])).expand(0).endswith("end"))
            self.assertTrue(T.fill_text("${w:%s}" % ids[1], 0, "", s).text.endswith("end"))
            with self.assertRaises(FillError) as cm:  # ids[0]은 깊이 17: 텍스트에서 18
                T.fill_text("${w:%s}" % ids[0], 0, "", s)
            self.assertIn("E_DEPTH", str(cm.exception))


class HookAndNode(Base):
    def prompt(self, inputs, wv=None):
        return {
            "client_id": "c1",
            "prompt": {"5": {"class_type": "WakaakaText", "inputs": inputs}, "6": {"class_type": "Other", "inputs": {}}},
            "extra_data": {"extra_pnginfo": {"workflow": {"nodes": [
                {"id": 5, "type": "WakaakaText", "widgets_values": wv or ["", 0, "randomize", "", ""],
                 "widgets_values_named": {"text": "", "seed": 0, "control_after_generate": "randomize", "result": "", "refs": ""}}]}}},
        }

    def test_hook_fills_prompt_workflow_and_screen(self):
        sent = []
        fill = lambda t, s, r: self.fill(t, s, r)  # noqa: E731
        p = self.prompt({"text": "${hair}, x", "seed": 7, "result": "old", "refs": ""})
        hook.on_prompt(p, None, lambda *a: sent.append(a), "tok", fill)
        inp = p["prompt"]["5"]["inputs"]
        want = self.fill("${hair}, x", 7)
        self.assertEqual(inp["result"], want.text)
        rv = json.loads(inp["refs"])
        self.assertEqual(rv, {"refs": {"hair": HAIR}, "lock": dict(sorted(want.lock.items())), "run": "tok"})
        node = p["extra_data"]["extra_pnginfo"]["workflow"]["nodes"][0]
        clean = T.dump_refs_value({"hair": HAIR}, want.lock)
        self.assertEqual(node["widgets_values"][-2:], [want.text, clean])
        self.assertEqual((node["widgets_values_named"]["result"], node["widgets_values_named"]["refs"]), (want.text, clean))
        self.assertEqual(sent, [("wakaaka.text", {"node": "5", "result": want.text, "refs": clean, "source": "local"}, "c1")])

    def test_hook_error_and_links(self):
        p = self.prompt({"text": "${nope}", "seed": 1})
        hook.on_prompt(p, None, None, "tok", lambda t, s, r: self.fill(t, s, r))
        self.assertIn("찾을 수 없음", json.loads(p["prompt"]["5"]["inputs"]["refs"])["error"])
        p = self.prompt({"text": ["9", 0], "seed": 1})
        hook.on_prompt(p, None, None, "tok", lambda *a: self.fail("링크면 훅이 채우지 않는다"))
        self.assertNotIn("refs", p["prompt"]["5"]["inputs"])

    def test_node_run(self):
        sent = []
        WakaakaText.fill_text = staticmethod(lambda t, s, r: self.fill(t, s, r))
        WakaakaText.send = staticmethod(lambda *a: sent.append(a))
        try:
            n = WakaakaText()
            self.assertEqual(n.run("x", 0, "kept", json.dumps({"refs": {}, "lock": {}, "run": RUN_TOKEN})), ("kept",))
            self.assertEqual(WakaakaText.IS_CHANGED(refs=json.dumps({"run": RUN_TOKEN})), "")
            with self.assertRaises(RuntimeError):
                n.run("x", 0, "", json.dumps({"error": "문법 오류"}))
            wf = {"nodes": [{"id": 3, "type": "WakaakaText", "widgets_values": ["", 0, "fixed", "", ""]}]}
            out = n.run("${hair}", 4, "", "", unique_id="3", extra_pnginfo={"workflow": wf})  # 링크로 들어온 경우
            self.assertEqual(out, (self.fill("${hair}", 4).text,))
            self.assertEqual(wf["nodes"][0]["widgets_values"][-2], out[0])
            self.assertEqual(sent[0][0], "wakaaka.text")
        finally:
            WakaakaText.fill_text = None
            WakaakaText.send = None


class SettingsKeys(unittest.TestCase):
    def test_user_and_artist(self):
        self.assertEqual((Settings().user, Settings().artist_at), (None, False))
        self.assertEqual(Settings(user_id=" joy_96 ").user, "joy_96")
        for bad in ["ab", "a" * 33, "한글아이디", "a b", None, 5]:
            self.assertIsNone(Settings(user_id=bad).user, bad)
        self.assertTrue(Settings(artist_at=True).artist_at)
        self.assertFalse(Settings(artist_at="true").artist_at)
        with tempfile.TemporaryDirectory() as d:
            p = d + "/s.json"
            with open(p, "w", encoding="utf-8") as f:
                json.dump({"Wakaaka.UserId": "wakaaka", "Wakaaka.ArtistAt": True, "Wakaaka.Mode": "local"}, f)
            s = load(p)
            self.assertEqual((s.user, s.artist_at, s.mode, s.url), ("wakaaka", True, "local", "https://wakaaka.baepoyong.com"))


if __name__ == "__main__":
    unittest.main()
