"""노드 `Wakaaka Text`: 줄 주석, 이름 해석, 묶음 합치기, 난수 경로, 오프라인, 훅과 노드 (가짜 서버 + 임시 캐시)."""

import json
import tempfile
import unittest
import unittest.mock

from wakaaka_node import client, hook, jcs, text as T
from wakaaka_node.cache import Cache
from wakaaka_node.expander import Expander, cleanup, derive, expand
from wakaaka_node.fill import FillError
from wakaaka_node.node import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS, RUN_TOKEN, WakaakaText, WakaakaTextSimple
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


class Simple(Base):
    """`Wakaaka Text (Simple)`: 화면만 다르고 채우기·기록·결과는 `Wakaaka Text`와 같다(DESIGN 8절)."""

    TEXTS = [
        "1girl, {smile|frown|grin}, \\{literal\\}",  # 참조 없음
        "# 메모 ${없는것}\n${hair}, {a|b|c}\n${Eyes}, ${w:%s}" % CLOTH,  # 이름·대소문자·직접 ID·주석 줄
        "${girl preset}, {1*a|2*b|3*c}, ${옷 > 상의}",
        "${옛 이름}, x",  # 목록에 없는 이름: refs 표로만 찾는다
    ]
    REFS = json.dumps({"refs": {"옛 이름": HAIR}, "lock": {}})

    def both(self, text, seed, refs):
        wf = {"nodes": [
            {"id": 5, "type": "WakaakaText", "widgets_values": ["", 0, "fixed", "", ""]},
            {"id": 8, "type": "WakaakaTextSimple", "widgets_values": ["", 0, "fixed", "", ""],
             "widgets_values_named": {"text": "", "seed": 0, "control_after_generate": "fixed", "result": "", "refs": ""}},
        ]}
        p = {"client_id": "c1", "extra_data": {"extra_pnginfo": {"workflow": wf}}, "prompt": {
            "5": {"class_type": "WakaakaText", "inputs": {"text": text, "seed": seed, "result": "", "refs": refs}},
            "8": {"class_type": "WakaakaTextSimple", "inputs": {"text": text, "seed": seed, "result": "", "refs": refs}},
        }}
        sent = []
        hook.on_prompt(p, None, lambda *a: sent.append(a), "tok", lambda t, s, r: self.fill(t, s, r))
        return p, wf, sent

    def test_same_as_text_node(self):
        for text in self.TEXTS:
            for seed in [0, 1, 7, 12345, (1 << 64) - 1]:
                p, wf, sent = self.both(text, seed, self.REFS)
                a, b = p["prompt"]["5"]["inputs"], p["prompt"]["8"]["inputs"]
                want = self.fill(text, seed, self.REFS)
                self.assertEqual((b["result"], b["refs"]), (a["result"], a["refs"]), text)
                self.assertEqual(b["result"], want.text)
                self.assertEqual(json.loads(b["refs"])["run"], "tok")
                self.assertEqual(wf["nodes"][1]["widgets_values"][-2:], wf["nodes"][0]["widgets_values"][-2:])
                named = wf["nodes"][1]["widgets_values_named"]
                self.assertEqual([named["result"], named["refs"]], wf["nodes"][0]["widgets_values"][-2:])
                self.assertEqual(wf["nodes"][1]["widgets_values"][:3], ["", 0, "fixed"])  # 앞 위젯은 그대로
                self.assertEqual([x[1]["node"] for x in sent], ["5", "8"])
                self.assertEqual(sent[0][1] | {"node": "8"}, sent[1][1])

    def test_refs_recorded_with_ids_and_lock(self):
        p, wf, _ = self.both("${hair}, ${Eyes}", 3, "")
        want = self.fill("${hair}, ${Eyes}", 3)
        rv = json.loads(wf["nodes"][1]["widgets_values"][-1])
        self.assertEqual(rv, {"refs": {"hair": HAIR, "Eyes": EYES}, "lock": dict(sorted(want.lock.items()))})
        self.assertEqual(wf["nodes"][1]["widgets_values"][-2], want.text)

    def test_errors_and_links_like_text_node(self):
        p, _, _ = self.both("${nope}", 1, "")
        self.assertEqual(p["prompt"]["8"]["inputs"]["refs"], p["prompt"]["5"]["inputs"]["refs"])
        self.assertIn("찾을 수 없음", json.loads(p["prompt"]["8"]["inputs"]["refs"])["error"])
        for k in ("text", "seed", "refs"):
            inputs = {"text": "x", "seed": 1, "refs": ""} | {k: ["9", 0]}
            p = {"prompt": {"8": {"class_type": "WakaakaTextSimple", "inputs": inputs}}}
            hook.on_prompt(p, None, None, "tok", lambda *a: self.fail("링크면 훅이 채우지 않는다"))
            self.assertNotIn("result", p["prompt"]["8"]["inputs"])

    def test_subgraph(self):
        wf = {
            "nodes": [{"id": 12, "type": "sg-uuid", "widgets_values": []}],
            "definitions": {"subgraphs": [{"id": "sg-uuid", "nodes": [
                {"id": 3, "type": "WakaakaTextSimple", "widgets_values": ["", 0, "fixed", "", ""]}]}]},
        }
        p = {"prompt": {"12:3": {"class_type": "WakaakaTextSimple", "inputs": {"text": "${hair}", "seed": 2, "refs": ""}}},
             "extra_data": {"extra_pnginfo": {"workflow": wf}}}
        hook.on_prompt(p, None, None, "tok", lambda t, s, r: self.fill(t, s, r))
        want = self.fill("${hair}", 2)
        self.assertEqual(wf["definitions"]["subgraphs"][0]["nodes"][0]["widgets_values"][-2:],
                         [want.text, T.dump_refs_value(want.refs, want.lock)])

    def test_wildcard_hook_does_not_touch_simple(self):
        wf = {"nodes": [{"id": 8, "type": "WakaakaTextSimple", "widgets_values": ["a", 0, "fixed", "r", "f"]}]}
        self.assertFalse(hook.set_widgets(wf, "8", "t", "l"))  # Wildcard 종류로 찾으면 없음
        self.assertEqual(wf["nodes"][0]["widgets_values"], ["a", 0, "fixed", "r", "f"])

    def test_node_class(self):
        self.assertIs(NODE_CLASS_MAPPINGS["WakaakaTextSimple"], WakaakaTextSimple)
        self.assertEqual(NODE_DISPLAY_NAME_MAPPINGS["WakaakaTextSimple"], "Wakaaka Text (Simple)")
        a, b = WakaakaText.INPUT_TYPES(), WakaakaTextSimple.INPUT_TYPES()
        # 위젯 이름·순서가 같아야 훅의 set_widgets(맨 끝 둘)가 맞다
        self.assertEqual([list(x) for x in b.values()], [list(x) for x in a.values()])
        self.assertEqual(b["required"], a["required"])
        self.assertEqual(b["required"]["text"][1]["dynamicPrompts"], False)
        self.assertTrue(b["required"]["seed"][1]["control_after_generate"])
        self.assertEqual(b["hidden"], a["hidden"])
        self.assertEqual(b["optional"]["refs"], a["optional"]["refs"])
        self.assertFalse(b["optional"]["result"][1]["multiline"])
        self.assertTrue(a["optional"]["result"][1]["multiline"])  # 부모는 그대로
        for k in ("RETURN_TYPES", "RETURN_NAMES", "FUNCTION", "CATEGORY"):
            self.assertEqual(getattr(WakaakaTextSimple, k), getattr(WakaakaText, k))

    def test_node_run(self):
        sent = []
        WakaakaText.fill_text = staticmethod(lambda t, s, r: self.fill(t, s, r))  # __init__.py처럼 부모에만 넣는다
        WakaakaText.send = staticmethod(lambda *a: sent.append(a))
        try:
            n = WakaakaTextSimple()
            self.assertEqual(n.run("x", 0, "kept", json.dumps({"refs": {}, "lock": {}, "run": RUN_TOKEN})), ("kept",))
            self.assertEqual(WakaakaTextSimple.IS_CHANGED(refs=json.dumps({"run": RUN_TOKEN})), "")
            self.assertNotEqual(WakaakaTextSimple.IS_CHANGED(refs=""), WakaakaTextSimple.IS_CHANGED(refs=""))  # NaN
            with self.assertRaises(RuntimeError):
                n.run("x", 0, "", json.dumps({"error": "문법 오류"}))
            wf = {"nodes": [{"id": 3, "type": "WakaakaTextSimple", "widgets_values": ["", 0, "fixed", "", ""]}]}
            out = n.run("${hair}", 4, "", "", unique_id="3", extra_pnginfo={"workflow": wf})  # 링크로 들어온 경우
            self.assertEqual(out, (self.fill("${hair}", 4).text,))
            self.assertEqual(out, WakaakaText().run("${hair}", 4, "", ""))
            self.assertEqual(wf["nodes"][0]["widgets_values"][-2], out[0])
            self.assertEqual(sent[0][0], "wakaaka.text")
        finally:
            WakaakaText.fill_text = None
            WakaakaText.send = None


FW_COMMA, IDEO_SPACE, NBSP = chr(0xFF0C), chr(0x3000), chr(0xA0)


class Tidy(unittest.TestCase):
    """결과 다듬기(DESIGN 8절 "결과 다듬기")"""

    def test_cases(self):
        cases = [
            ("a,b", "a, b"),
            ("a,   b", "a, b"),
            ("a  b,  c", "a b, c"),
            ("a , b", "a, b"),
            ("a,\nb", "a,\nb"),  # 줄 끝 쉼표 뒤에는 넣지 않는다
            ("a, \n  b", "a,\nb"),
            ("a,\n\nb", "a,\n\nb"),  # 빈 줄 유지
            ("\n\na\n\n", "\n\na\n\n"),  # 줄바꿈은 그대로
            ("a\t,\tb", "a, b"),
            ("\t a \t b\t", "a b"),
            ("a,\t\tb  \t c", "a, b c"),
            ("a" + FW_COMMA + "b" + IDEO_SPACE + IDEO_SPACE + "c" + NBSP + NBSP + ",d",
             "a" + FW_COMMA + "b" + IDEO_SPACE + IDEO_SPACE + "c" + NBSP + NBSP + ", d"),  # 대상 밖 글자는 그대로
            (IDEO_SPACE + "a" + NBSP, IDEO_SPACE + "a" + NBSP),
            ("(a:1.2), b", "(a:1.2), b"),
            ("(a:1.2),b, [c], <lora:x:0.8>", "(a:1.2), b, [c], <lora:x:0.8>"),
            ("\\(x\\), y", "\\(x\\), y"),
            ("", ""),
            ("   ", ""),
            (",", ","),
            ("a,\r\nb ,\r", "a,\nb,"),  # 줄 끝 \r은 공백
            ("a\rb", "a\rb"),  # 줄 가운데 \r은 그대로
            ("1,000", "1, 000"),  # 숫자 안의 쉼표도 띄운다(DESIGN)
            ("a,,b", "a,, b"),  # 쉼표 바로 뒤가 쉼표면 넣지 않는다(SPEC 4.4 뒤라 전개 결과에는 없다)
        ]
        for src, want in cases:
            self.assertEqual(T.tidy(src), want, repr(src))

    def test_properties(self):
        import random
        import re
        rnd = random.Random(20261119)
        alphabet = [" ", " ", "\t", ",", ",", "\n", "\r", "a", "b", "(", ":", "1", ".", "\\", "가", FW_COMMA, IDEO_SPACE, NBSP]
        ws = " \t\r,"
        for _ in range(20000):
            x = "".join(rnd.choice(alphabet) for _ in range(rnd.randrange(0, 24)))
            for src in (x, cleanup(x)):
                y = T.tidy(src)
                m = repr(src)
                self.assertEqual(T.tidy(y), y, m)  # 멱등
                self.assertEqual(y.count("\n"), src.count("\n"), m)  # 줄 수
                self.assertEqual([c for c in y if c not in ws], [c for c in src if c not in ws], m)  # 그 밖의 글자
                self.assertEqual(y.count(","), src.count(","), m)  # 쉼표는 지우거나 더하지 않는다
                self.assertNotIn("  ", y, m)
                self.assertNotIn("\t", y, m)
                self.assertNotIn(" ,", y, m)
                for line in y.split("\n"):
                    self.assertEqual(line, line.strip(" \t"), m)
                    self.assertFalse(line.endswith("\r"), m)
                self.assertIsNone(re.search(r",(?![ ,\n]|$)", y), m)  # 쉼표 뒤는 스페이스(줄 끝·붙은 쉼표 제외)
            # 전개기 정리(SPEC 4.4) 뒤에는 붙은 쉼표가 없으므로 예외 없이 쉼표 뒤가 스페이스나 줄 끝이다
            y = T.tidy(cleanup(x))
            self.assertNotIn(",,", y, repr(x))
            self.assertIsNone(re.search(r",(?![ \n]|$)", y), repr(x))


MESSY = "MessyMessyXX"


class TidyThroughHook(Base):
    def setUp(self):
        super().setUp()
        self.srv.close()
        self.world.docs[MESSY] = doc("red  hair ,blue eyes,smile\t\t,  1girl")
        items = USER_ITEMS + [{"id": MESSY, "name": "messy", "owner": "me", "version": 1, "hash": "x", "fixed": False}]
        self.srv = FakeServer(self.world.routes({"mine": items}))
        self.settings = Settings("server", self.srv.url, "mine")

    def both(self, text, seed):
        p = {"prompt": {
            "5": {"class_type": "WakaakaText", "inputs": {"text": text, "seed": seed, "result": "", "refs": ""}},
            "8": {"class_type": "WakaakaTextSimple", "inputs": {"text": text, "seed": seed, "result": "", "refs": ""}},
        }}
        hook.on_prompt(p, None, None, "tok", lambda t, s, r: self.fill(t, s, r))
        a, b = p["prompt"]["5"]["inputs"]["result"], p["prompt"]["8"]["inputs"]["result"]
        self.assertEqual(a, b)  # Text와 Simple은 여전히 같다
        return a

    def test_result_is_tidied(self):
        # 참조 있는 텍스트: 와일드카드 안의 띄어쓰기도 다듬는다
        self.assertEqual(self.both("${messy}", 0), "red hair, blue eyes, smile, 1girl")
        self.assertEqual(self.both("masterpiece ,${messy},{best  quality}\n  ${messy} , x  ", 3),
                         "masterpiece, red hair, blue eyes, smile, 1girl, best quality\nred hair, blue eyes, smile, 1girl, x")
        # 참조 없이 선택지만 있는 텍스트도 전체를 다듬는다
        self.assertEqual(self.both("1girl,smile , {a|a}  b,\n\n\tlast ,", 9), "1girl, smile, a b,\n\nlast")  # 맨 끝 쉼표는 SPEC 4.4가 지운다
        self.assertEqual(self.both("{x}a,b", 0), "xa, b")  # 보기 하나짜리 {x}도 선택지

    def test_nothing_to_expand_is_left_as_typed(self):
        # 펼 것(참조·선택지)이 없으면 다듬지 않는다(DESIGN f29b4d1). SPEC 4.4 정리만 전개기가 한다
        cases = [
            ("1girl,smile ,  b", "1girl,smile ,  b"),
            ("a,b\n c ,d", "a,b\n c ,d"),
            (",a ,, b,", "a , b"),  # SPEC 4.4: 빈 조각과 맨 앞뒤 쉼표만 지움
            ("\\{x\\},y  z", "{x},y  z"),  # 이스케이프한 { }는 글자: 펼 것이 아니다
            ("$5 ,y  z", "$5 ,y  z"),  # { 가 따르지 않는 $는 글자
            ("\\${hair},y  z", "$hair, y z"),  # 이 경우는 $만 이스케이프되고 {hair}는 보기 하나짜리 선택지라 다듬는다
            ("# ${hair} {a|b}\nx,y  z", "x,y  z"),  # 주석 줄을 지운 뒤에 본다
            ("1,000  steps", "1,000  steps"),
        ]
        for text, want in cases:
            self.assertEqual(self.both(text, 5), want, repr(text))
        self.assertEqual(self.requests("/api/"), [])

    def test_result_equals_tidy_of_raw(self):
        texts = ["${hair} ,${Eyes},{smile|frown}  x", "${girl preset},${옷 > 상의} ,  {1*a|2*b|3*c}", "a,b\n c ,d", "{a , b|c}  d,e"]
        for t in texts:
            expands = T.to_stored(t, "", self.settings, userlists=self.users)[3]
            self.assertEqual(expands, t != "a,b\n c ,d", t)
            for seed in range(20):
                with unittest.mock.patch.object(T, "tidy", lambda s: s):
                    raw = self.fill(t, seed).text
                got = self.both(t, seed)
                self.assertEqual(got, T.tidy(raw) if expands else raw, (t, seed))

    def test_wildcard_node_untouched(self):
        # Wakaaka Wildcard는 다듬지 않는다: 전개기 결과 그대로
        from wakaaka_node import ids
        from wakaaka_node.fill import fill
        s = Settings("local", self.srv.url, "mine")
        lock = self.world.bundle(MESSY)["lock"]
        with unittest.mock.patch.object(T, "tidy", side_effect=AssertionError("Wildcard 경로가 tidy를 부름")):
            r = fill(MESSY, 0, False, ids.dump_lock_value(lock, 1), s, cache=self.cache)
        self.assertEqual(r.text, "red  hair ,blue eyes,smile\t\t,  1girl")
        self.assertEqual(r.text, expand(self.world.bundle(MESSY), 0))


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


class SharedCache(Base):
    """검토 2번: Text가 먼저 새 버전을 받은 뒤 서버가 끊겨도 Wildcard의 오프라인 lock·버전 표시는 그대로다."""

    def test_text_does_not_overwrite_wildcard_latest(self):
        from wakaaka_node import fill as F

        old = self.world.bundle(HAIR)
        meta = {"id": HAIR, "name": "hair", "owner": "me", "version": 4, "hash": old["root"]}
        self.srv.routes[f"/api/w/{HAIR}/meta"] = (200, meta, 0)
        s = Settings("local", self.srv.url, "mine")
        r = F.fill(HAIR, 1, True, "", s, cache=self.cache)  # Wildcard가 v4를 받아 둠
        self.assertEqual((r.version, r.lock[HAIR]), (4, old["root"]))
        # 서버에 새 버전: Text가 먼저 받는다
        self.world.docs[HAIR] = doc("black hair")
        new = self.world.bundle(HAIR)
        self.srv.routes[f"/api/w/{HAIR}/bundle"] = (200, jcs.canonicalize(new).encode(), 0)
        self.assertEqual(self.fill("${hair}").lock[HAIR], new["root"])
        # 서버가 끊김
        self.srv.close()
        client.forget_failures()
        off = F.fill(HAIR, 1, True, "", s, cache=self.cache)
        self.assertEqual((off.source, off.version, off.lock[HAIR]), ("cache", 4, old["root"]))  # Wildcard는 전과 같다
        # Text는 자기가 마지막으로 받은 것으로 전개한다
        t = T.fill_text("${w:%s}" % HAIR, 0, "", self.settings, cache=self.cache, userlists=self.users)
        self.assertEqual((t.source, t.lock[HAIR], t.text), ("cache", new["root"], "black hair"))

    def test_text_falls_back_to_wildcard_record(self):
        # Text가 받은 적 없는 ID는 Wildcard가 받아 둔 최신 기록으로 오프라인 전개한다
        from wakaaka_node import fill as F

        b = self.world.bundle(EYES)
        self.srv.routes[f"/api/w/{EYES}/meta"] = (200, {"id": EYES, "name": "e", "owner": "me", "version": 2, "hash": b["root"]}, 0)
        F.fill(EYES, 0, True, "", Settings("local", self.srv.url), cache=self.cache)
        self.srv.close()
        client.forget_failures()
        t = T.fill_text("${w:%s}" % EYES, 0, "", self.settings, cache=self.cache, userlists=self.users)
        self.assertEqual((t.source, t.lock), ("cache", b["lock"]))


class RequestCount(Base):
    """README "한계": 훅은 이벤트 루프 안에서 동기로 부른다. 큐 한 번에 몇 번 부르는지."""

    def test_counts(self):
        t = "${hair}, ${Eyes}, ${옷 > 상의}"
        self.fill(t, 0)  # 이름이 refs에 없음: 유저 목록 1번 + 참조마다 묶음 1번
        self.assertEqual((len(self.requests("/api/users/")), len(self.requests("/bundle"))), (1, 3))
        r = self.fill(t, 1)  # 60초 안: 유저 목록은 메모리 캐시, 묶음은 다시 받는다(최신으로 전개하므로)
        self.assertEqual((len(self.requests("/api/users/")), len(self.requests("/bundle"))), (1, 6))
        refs = T.dump_refs_value(r.refs, r.lock)
        self.users.forget()
        self.fill(t, 2, refs)  # 이름이 모두 refs에 있음(자동완성으로 골랐거나 PNG에서 불러옴): 유저 목록을 부르지 않는다
        self.assertEqual((len(self.requests("/api/users/")), len(self.requests("/bundle"))), (1, 9))

    def test_server_down_costs_one_failure(self):
        import time

        t = "${hair}, ${Eyes}, ${옷 > 상의}"
        r = self.fill(t, 0)
        refs = T.dump_refs_value(r.refs, r.lock)
        self.srv.close()
        client.forget_failures()
        t0 = time.monotonic()
        off = self.fill(t, 0, refs)  # 첫 실패 뒤 10초 동안 그 서버를 부르지 않으므로 나머지는 바로 캐시로
        self.assertEqual((off.text, off.source), (r.text, "cache"))
        self.assertLess(time.monotonic() - t0, 2.5)
