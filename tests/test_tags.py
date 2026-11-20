"""태그 인덱스: CSV 파서(두 키워드 표기), 검색 규칙, 받기·검증·교체 (가짜 Hugging Face + 임시 폴더)."""

import hashlib
import io
import json
import logging
import os
import random
import sqlite3
import tempfile
import unittest
from unittest import mock

from wakaaka_node import tagcsv, tagindex, tagupdate
from wakaaka_node.settings import Settings
from wakaaka_node.tagupdate import TagIndex

from .fakeserver import FakeServer

logging.getLogger("wakaaka").setLevel(logging.CRITICAL)  # 실패 시험이 남기는 경고를 숨긴다
REPO = tagupdate.REPO
LICENSE = b"Korean Tag CSV - License and Attribution\n(test copy)\n"


def csv_line(name, cat, pc, desc):
    return f'{name},{cat},{pc},"{desc.replace(chr(34), chr(34) * 2)}"\n'


def make_csv(n=300, form="new", seed=1, extra=""):
    """가짜 통합본. 이름은 밑줄 대신 공백(원본 표기)."""
    rng = random.Random(seed)
    lines = []
    for i in range(n):
        name = f"tag {i:05d} {rng.choice(['hair', 'eyes', 'dress', 'smile'])}"
        kws = [f"키워드{i}", f"태그 {i}"]
        body = f"[일반 > 묶음{i % 7}] 테스트 태그 {i}번."
        desc = f"{body} 키워드: <묶음>, {', '.join(kws)}" if form == "old" else f"{body} <키워드> 묶음, {', '.join(kws)}."
        lines.append(csv_line(name, rng.choice([0, 1, 3, 4, 5]), rng.randrange(1, 10**6), desc))
    return ("".join(lines) + extra).encode("utf-8")


def read(text):
    st = tagcsv.Stats()
    rows = list(tagcsv.read_rows(io.StringIO(text, newline=""), st))
    return rows, st


class Parser(unittest.TestCase):
    def test_both_keyword_forms(self):
        old = csv_line("1girl", 0, 8619766, "[인물 > 인원수] 여성 캐릭터가 한 명 등장함. 키워드: <인원>, 여자 1명, 여성 한명")
        new = csv_line("1girl", 0, 8619766, "[인물 > 인원수] 여성 캐릭터가 한 명 등장함. <키워드> 인원, 여자 1명, 여성 한명.")
        for text, form in ((old, "old"), (new, "new")):
            rows, st = read(text)
            self.assertEqual(rows, [("1girl", 0, 8619766, "여성 캐릭터가 한 명 등장함.", ["인원", "여자 1명", "여성 한명"])])
            self.assertEqual(st.forms[form], 1)

    def test_trailing_dot(self):
        # 원본은 마지막 키워드가 이미 .으로 끝나면 마침표를 덧붙이지 않는다
        cases = [
            ("a", "<키워드> 가, b.", ["가", "b"]),
            ("loading (verjuice)", "<키워드> 로딩, loading (verjuice), loading..", ["로딩", "loading (verjuice)", "loading.."]),
            ("studio c.h.", "<키워드> 스튜디오, studio C.H.", ["스튜디오", "studio C.H."]),
            ("b", "<키워드> 가, 나.,", ["가", "나"]),
            ("c", "<키워드> 가, 가.", ["가"]),
        ]
        for name, desc, kws in cases:
            rows, _ = read(csv_line(name, 0, 1, "본문. " + desc))
            self.assertEqual(rows[0][4], kws, desc)

    def test_headerless_rules(self):
        # 서버 tagcsv.ts의 헤더 없는 형식: 이름 공백 → 밑줄, [작가] 떼기, 묶음 꺾쇠 떼기
        rows, _ = read(csv_line("school uniform", 0, 5, "[교복] 학교 교복. 키워드: <교복>, 세일러복"))
        self.assertEqual(rows, [("school_uniform", 0, 5, "학교 교복.", ["교복", "세일러복"])])
        rows, _ = read(csv_line("x", 1, 5, "본문 키워드: a, a, , b "))
        self.assertEqual(rows[0][3:], ("본문", ["a", "b"]))

    def test_bad_rows_are_counted(self):
        text = "".join([
            csv_line("ok", 0, 1, "x"),
            "a,b,c\n",  # 열 수
            csv_line("bad_name", 0, 1, "x"),  # 밑줄(원본은 공백으로 적는다)
            csv_line(" lead", 0, 1, "x"),
            csv_line("two  spaces", 0, 1, "x"),
            csv_line("cat", 2, 1, "x"),  # 없는 분류
            csv_line("pc", 0, "1e3", "x"),
            csv_line("big", 0, 2**31, "x"),
            csv_line("ok", 0, 1, "x"),  # 중복
            csv_line("ctl", 0, 1, "a\x01b"),
            "\n",
        ])
        rows, st = read(text)
        self.assertEqual([r[0] for r in rows], ["ok"])
        self.assertEqual(st.bad, {"columns": 1, "name": 3, "number": 3, "duplicate": 1, "characters": 1})
        self.assertEqual(st.lines, 10)

    def test_js_whitespace(self):
        self.assertEqual(tagcsv.js_trim("\ufeff\u3000 a \u00a0"), "a")
        self.assertEqual(tagcsv.js_trim("\x85a\x1c"), "\x85a\x1c")  # Python strip과 다르다
        self.assertEqual(tagcsv.name_key("Long_H air\u3000X"), "longhairx")
        self.assertEqual(tagcsv.keyword_rows(["여자 1명", "인원", "여자"]), [("여자1명", 0), ("인원", 0), ("여자", 0), ("1명", 1)])


# ---------------------------------------------------------------- 검색

def tag(name, pc, kws=(), body="", cat=0):
    desc = body + (" 키워드: " + ", ".join(kws) if kws else "")
    return csv_line(name.replace("_", " "), cat, pc, desc)


SAMPLE = "".join([
    tag("long_hair", 500, ["긴 머리", "장발"], "머리카락이 긴 모습."),
    tag("long_sleeves", 400, ["긴 소매"], "소매가 긴 옷."),
    tag("very_long_hair", 300, ["아주 긴 머리"], "머리카락이 아주 긴 모습."),
    tag("hair_ornament", 450, ["머리 장식"], "머리에 다는 장식."),
    tag("blonde_hair", 450, ["금발", "노란 머리"], "금색 머리카락."),
    tag("longhair_(artist)", 10, ["롱헤어"], "작가.", cat=1),
    tag("a%b_c", 5, ["퍼센트"], "이스케이프 시험."),
    tag("cat_ears", 600, ["고양이 귀"], "고양이 귀가 달린 모습. " + "가" * 300),
    tag("animal_ears", 700, ["동물 귀"], "동물의 귀."),
])


class Search(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        src = os.path.join(cls.tmp.name, "s.csv")
        with open(src, "w", encoding="utf-8", newline="") as f:
            f.write(SAMPLE)
        cls.db = os.path.join(cls.tmp.name, "s.sqlite")
        tagindex.build(src, cls.db, {"file": "s.csv", "sha256": "0" * 64, "size": 1})
        cls.con = tagindex._open(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.con.close()
        cls.tmp.cleanup()

    def names(self, q):
        return [t["name"] for t in tagindex.suggest(self.con, q)["tags"]]

    def test_english(self):
        # 접두 일치 post_count 순 → 부분 일치 post_count 순. 밑줄·공백·대소문자 무시
        for q in ["long h", "long_h", "LONGH", "  longh "]:
            self.assertEqual(self.names(q), ["long_hair", "longhair_(artist)", "very_long_hair"], q)
        self.assertEqual(self.names("lo"), ["long_hair", "long_sleeves", "longhair_(artist)"])  # 2글자는 부분 일치 없음
        self.assertEqual(self.names("hair"), ["hair_ornament", "long_hair", "blonde_hair", "very_long_hair", "longhair_(artist)"])
        self.assertEqual(self.names("%b"), [])  # 2글자는 접두만
        self.assertEqual(self.names("%bc"), ["a%b_c"])  # LIKE 특수 글자도 글자 그대로
        self.assertEqual(self.names("a%"), ["a%b_c"])
        self.assertEqual(self.names("_"), [])
        self.assertEqual(self.names("   "), [])

    def test_korean_tiers(self):
        # 키워드 전체 같음 → 키워드 접두 → 낱말 접두 → 부분 일치(3글자 이상)
        self.assertEqual(self.names("긴 머리"), ["long_hair", "very_long_hair"])  # 0단계, 3단계("아주 긴 머리")
        self.assertEqual(self.names("머리 장"), ["hair_ornament"])
        self.assertEqual(self.names("긴머리"), ["long_hair"])
        # 1단계(키워드 접두) "머리 장식" → 2단계(낱말 접두) post_count 순
        self.assertEqual(self.names("머리"), ["hair_ornament", "long_hair", "blonde_hair", "very_long_hair"])
        self.assertEqual(self.names("귀"), ["animal_ears", "cat_ears"])

    def test_response_shape(self):
        r = tagindex.suggest(self.con, "cat ears")
        self.assertEqual(r["tags"][0], {"name": "cat_ears", "category": 0, "postCount": 600,
                                        "body": ("고양이 귀가 달린 모습. " + "가" * 300)[:200], "keywords": ["고양이 귀"]})

    def test_bad_query(self):
        for q in ["a\x00", "\ud800", "x" * 65, "가" * 257]:
            with self.assertRaises(tagindex.QueryError):
                tagindex.suggest(self.con, q)
        self.assertEqual(self.names("x" * 64 + "  "), [])

    def test_contains_paths_agree(self):
        # FTS 후보 경로와 post_count 순 훑기 경로의 결과가 같다
        for needle in ["hai", "ong", "air_", "%b_", "머리카", "가가가", "고양이 귀", "없는말"]:
            for col in ("name_key", "ko_text"):
                with mock.patch.object(tagindex, "CAP", 10**9):
                    a = tagindex.contains_ids(self.con, col, needle, 40)
                with mock.patch.object(tagindex, "CAP", 0):
                    b = tagindex.contains_ids(self.con, col, needle, 40)
                self.assertEqual(a, b, (col, needle))


# ---------------------------------------------------------------- 받기·검증·교체

class Hub:
    """가짜 Hugging Face. files: 이름 → 바이트."""

    def __init__(self):
        self.files = {}
        self.srv = FakeServer()
        self.url = self.srv.url
        self.lfs_oid_override = {}

    def put(self, name, data, license=True):
        self.files[name] = data
        if license:
            self.files["LICENSE"] = LICENSE
        else:
            self.files.pop("LICENSE", None)
        tree = [{"type": "directory", "oid": "x", "size": 0, "path": "old"}]
        for n, d in sorted(self.files.items()):
            e = {"type": "file", "oid": hashlib.sha1(b"blob %d\0" % len(d) + d).hexdigest(), "size": len(d), "path": n}
            if n.endswith(".csv"):
                e["lfs"] = {"oid": self.lfs_oid_override.get(n, hashlib.sha256(d).hexdigest()), "size": len(d), "pointerSize": 133}
            tree.append(e)
        routes = {f"/api/datasets/{REPO}/tree/main": (200, tree, 0)}
        for n, d in self.files.items():
            routes[f"/datasets/{REPO}/resolve/main/{n}"] = (200, d, 0)
        self.srv.routes = routes

    def downloads(self):
        return [r["path"] for r in self.srv.requests if "/resolve/" in r["path"] and r["path"].endswith(".csv")]

    def close(self):
        self.srv.close()


@mock.patch.object(tagupdate, "MIN_ROWS", 100)
class Update(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "tags")
        self.hub = Hub()

    def tearDown(self):
        self.hub.close()
        self.tmp.cleanup()

    def index(self):
        return TagIndex(self.root, base_url=self.hub.url)

    def files(self):
        return sorted(os.listdir(self.root))

    def test_first_download_then_skip_then_replace(self):
        self.hub.put("danbooru-tags-kr-2026-11-04.csv", make_csv(150, form="old"))
        self.hub.put("danbooru-tags-kr-2026-11-10.csv", make_csv(200))
        self.hub.put("danbooru-tags-backslash-kr-2026-11-30.csv", make_csv(10))  # 변형 파일은 쓰지 않는다
        ti = self.index()
        self.assertEqual(ti.status()["state"], "none")
        msg = ti.refresh()
        self.assertTrue(msg.startswith("built danbooru-tags-kr-2026-11-10.csv (200 rows"), msg)
        st = ti.status()
        self.assertEqual((st["state"], st["file"], st["rows"], st["error"]), ("ready", "danbooru-tags-kr-2026-11-10.csv", 200, None))
        self.assertEqual(st["sha256"], hashlib.sha256(make_csv(200)).hexdigest())
        self.assertEqual(self.files(), ["LICENSE-danbooru-tags-kr", f"tags-{st['sha256'][:16]}.sqlite"])  # CSV는 지웠다
        with open(os.path.join(self.root, "LICENSE-danbooru-tags-kr"), "rb") as f:
            self.assertEqual(f.read(), LICENSE)
        self.assertEqual(ti.suggest("tag 00001")["tags"][0]["name"][:9], "tag_00001")
        for r in self.hub.srv.requests:
            self.assertEqual(r["ua"], tagupdate.USER_AGENT)

        # 같은 해시: 받지 않는다. 새로 띄운 프로세스도 폴더에서 인덱스를 찾는다
        ti2 = self.index()
        self.assertEqual(ti2.status()["state"], "ready")
        self.assertTrue(ti2.refresh().startswith("up to date"))
        self.assertEqual(len(self.hub.downloads()), 1)

        # 새 파일: 바꿔 넣고 옛 인덱스는 지운다
        self.hub.put("danbooru-tags-kr-2026-11-20.csv", make_csv(220, seed=2))
        self.assertTrue(ti2.refresh().startswith("updated to danbooru-tags-kr-2026-11-20.csv (220 rows"))
        self.assertEqual(ti2.status()["rows"], 220)
        self.assertEqual(len([f for f in self.files() if f.endswith(".sqlite")]), 1)
        self.assertEqual(len(self.hub.downloads()), 2)

    def prepare(self):
        self.hub.put("danbooru-tags-kr-2026-11-10.csv", make_csv(200))
        ti = self.index()
        ti.refresh()
        before = (ti.status(), self.files())
        return ti, before

    def assert_kept(self, ti, before, msg):
        self.assertTrue(msg.startswith("failed:"), msg)
        st = ti.status()
        self.assertEqual((st["state"], st["sha256"], st["rows"]), ("ready", before[0]["sha256"], 200))
        self.assertTrue(st["error"])
        self.assertEqual([f for f in self.files() if f != "rejected.json"], before[1])  # 임시 파일도 남지 않는다
        self.assertEqual(ti.suggest("tag 00001")["tags"][0]["name"][:9], "tag_00001")

    def test_hash_mismatch_keeps_index(self):
        ti, before = self.prepare()
        self.hub.lfs_oid_override["danbooru-tags-kr-2026-11-20.csv"] = "f" * 64
        self.hub.put("danbooru-tags-kr-2026-11-20.csv", make_csv(220, seed=2))
        msg = ti.refresh()
        self.assert_kept(ti, before, msg)
        self.assertIn("sha256 mismatch", msg)
        self.assertNotIn("rejected.json", self.files())  # 받다가 깨진 것은 다음에 다시 받는다

    def test_format_check_keeps_index_and_is_remembered(self):
        ti, before = self.prepare()
        cases = [
            ("danbooru-tags-kr-2026-11-20.csv", make_csv(50, seed=3), "rows 50 < 100"),  # 행 수
            ("danbooru-tags-kr-2026-11-21.csv", make_csv(200, seed=4).replace("<키워드>".encode(), "[키워드]".encode()), "keyword mark"),  # 표기가 또 바뀜
            ("danbooru-tags-kr-2026-11-22.csv", make_csv(200, seed=5, extra="a,b,c\n" * 5), "bad rows"),  # 열 수
            ("danbooru-tags-kr-2026-11-23.csv", make_csv(200, seed=6) + b'x,0,1,"unterminated\n', "Error"),  # CSV 문법
            ("danbooru-tags-kr-2026-11-24.csv", make_csv(200, seed=7) + b"\xff\xfe,0,1,x\n", "UnicodeDecodeError"),
        ]
        for name, data, why in cases:
            self.hub.put(name, data)
            msg = ti.refresh()
            self.assert_kept(ti, before, msg)
            self.assertIn(why, msg)
            with open(os.path.join(self.root, "rejected.json"), encoding="utf-8") as f:
                self.assertEqual(json.load(f)["sha256"], hashlib.sha256(data).hexdigest())
            n = len(self.hub.downloads())
            self.assertIn("rejected before", ti.refresh())  # 같은 파일은 다시 받지 않는다
            self.assertEqual(len(self.hub.downloads()), n)

    def test_shrunk_file_is_rejected(self):
        ti, before = self.prepare()
        self.hub.put("danbooru-tags-kr-2026-11-20.csv", make_csv(150, seed=8))  # 지금 200행의 0.8배 미만
        self.assertIn("x current 200", ti.refresh())
        self.assertEqual(ti.status()["rows"], 200)

    def test_connection_failure_keeps_index(self):
        ti, before = self.prepare()
        self.hub.close()
        msg = ti.refresh()
        self.assert_kept(ti, before, msg)
        # 인덱스가 없을 때는 오류 상태
        empty = TagIndex(os.path.join(self.tmp.name, "other"), base_url=self.hub.url)
        self.assertTrue(empty.refresh().startswith("failed:"))
        self.assertEqual(empty.status()["state"], "error")
        self.assertEqual(empty.suggest("hair"), {"tags": []})

    def test_server_error_and_bad_tree(self):
        ti, before = self.prepare()
        self.hub.srv.routes[f"/api/datasets/{REPO}/tree/main"] = (500, {"error": "x"}, 0)
        self.assert_kept(ti, before, ti.refresh())
        self.hub.srv.routes[f"/api/datasets/{REPO}/tree/main"] = (200, [{"type": "file", "path": "danbooru-tags-kr-2026-12-01.csv"}], 0)
        self.assert_kept(ti, before, ti.refresh())

    def test_broken_or_partial_files_in_folder(self):
        ti, before = self.prepare()
        sha = before[0]["sha256"]
        # 다른 형식 버전의 인덱스, 깨진 파일, 오래된 임시 파일은 로딩 때 지운다
        bad = os.path.join(self.root, "tags-" + "a" * 16 + ".sqlite")
        with open(bad, "wb") as f:
            f.write(b"not sqlite")
        stale = os.path.join(self.root, "tmp-abc.csv")
        with open(stale, "wb") as f:
            f.write(b"x")
        os.utime(stale, (0, 0))
        fresh = os.path.join(self.root, "tmp-def.csv")
        with open(fresh, "wb") as f:
            f.write(b"x")
        ti2 = self.index()
        self.assertEqual(ti2.status()["sha256"], sha)
        self.assertNotIn(os.path.basename(bad), self.files())
        self.assertNotIn("tmp-abc.csv", self.files())
        self.assertIn("tmp-def.csv", self.files())  # 다른 ComfyUI가 만드는 중일 수 있다

    def test_license_restored_when_missing(self):
        ti, before = self.prepare()
        os.unlink(os.path.join(self.root, "LICENSE-danbooru-tags-kr"))
        self.assertTrue(ti.refresh().startswith("up to date"))
        self.assertIn("LICENSE-danbooru-tags-kr", self.files())


class Gate(unittest.TestCase):
    def test_pick_latest(self):
        tree = [
            {"type": "file", "path": "danbooru-tags-kr-2026-11-10.csv", "lfs": {"oid": "a" * 64, "size": 10}},
            {"type": "file", "path": "danbooru-tags-kr-2026-11-04.csv", "lfs": {"oid": "b" * 64, "size": 10}},
            {"type": "file", "path": "danbooru-tags-kr-2026-12-01.csv", "lfs": {"oid": "bad", "size": 10}},
            {"type": "file", "path": "old/danbooru-tags-kr-2027-01-01.csv", "lfs": {"oid": "c" * 64, "size": 10}},
            {"type": "file", "path": "danbooru-tags-anima-kr-2027-01-01.csv", "lfs": {"oid": "d" * 64, "size": 10}},
            {"type": "file", "path": "LICENSE", "oid": "e" * 40, "size": 3},
        ]
        latest, lic = tagupdate.pick_latest(tree)
        self.assertEqual(latest, {"file": "danbooru-tags-kr-2026-11-10.csv", "sha256": "a" * 64, "size": 10})
        self.assertEqual(lic["oid"], "e" * 40)
        with self.assertRaises(tagupdate.UpdateError):
            tagupdate.pick_latest([])


if __name__ == "__main__":
    unittest.main()


@mock.patch.object(tagupdate, "MIN_ROWS", 100)
class SuggestDuringSwap(unittest.TestCase):
    """검토 3번: 검색이 경로를 읽은 뒤 연결을 열기 전에 refresh가 옛 파일을 지운 경우."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "tags")
        self.hub = Hub()
        self.hub.put("danbooru-tags-kr-2026-11-10.csv", make_csv(200))
        self.ti = TagIndex(self.root, base_url=self.hub.url)
        with mock.patch.object(tagupdate, "MIN_ROWS", 100):  # 클래스 데코레이터는 setUp에 걸리지 않는다
            self.assertTrue(self.ti.refresh().startswith("built"))

    def tearDown(self):
        self.hub.close()
        self.tmp.cleanup()

    def swap_during_open(self, new_path):
        """처음 연결을 열 때 옛 파일이 지워지고 인덱스가 new_path로 바뀐 것처럼"""
        real = tagindex._open
        calls = []

        def fake(path):
            calls.append(path)
            if len(calls) == 1:
                with self.ti._lock:
                    self.ti._path = new_path
                raise sqlite3.OperationalError("unable to open database file")
            return real(path)

        return mock.patch.object(tagindex, "_open", fake), calls

    def test_retries_with_current_index(self):
        old = self.ti._path
        self.hub.put("danbooru-tags-kr-2026-11-20.csv", make_csv(220, seed=2))
        other = TagIndex(os.path.join(self.tmp.name, "other"), base_url=self.hub.url)
        other.refresh()
        patch, calls = self.swap_during_open(other._path)
        with patch:
            r = self.ti.suggest("tag 00210")
        self.assertEqual(calls, [old, other._path])
        self.assertEqual(r["tags"][0]["name"][:9], "tag_00210")  # 새 인덱스에만 있는 태그

    def test_gives_empty_result_with_error(self):
        patch, calls = self.swap_during_open(os.path.join(self.root, "tags-" + "0" * 16 + ".sqlite"))
        with patch:
            r = self.ti.suggest("tag")
        self.assertEqual(r["tags"], [])
        self.assertIn("error", r)
        self.assertEqual(len(calls), 2)


@mock.patch.object(tagupdate, "MIN_ROWS", 100)
class NonFormatFailures(unittest.TestCase):
    """검토 6번: 형식 문제가 아닌 이유로 실패해 켤 때마다 27MB를 다시 받지 않게."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "tags")
        self.hub = Hub()

    def tearDown(self):
        self.hub.close()
        self.tmp.cleanup()

    def test_no_fts5_trigram_checks_before_download(self):
        self.hub.put("danbooru-tags-kr-2026-11-10.csv", make_csv(200))
        ti = TagIndex(self.root, base_url=self.hub.url)
        with mock.patch.object(tagindex, "fts5_trigram_support", lambda: "no such tokenizer: trigram"):
            msg = ti.refresh()
            self.assertIn("FTS5 trigram", msg)
            self.assertEqual(self.hub.srv.requests, [])  # 파일 목록도 받지 않는다
            st = ti.status()
            self.assertEqual(st["state"], "error")
            self.assertIn("FTS5 trigram", st["error"])

    def test_missing_license_still_builds_and_is_reported(self):
        self.hub.put("danbooru-tags-kr-2026-11-10.csv", make_csv(200), license=False)
        ti = TagIndex(self.root, base_url=self.hub.url)
        self.assertTrue(ti.refresh().startswith("built"))
        st = ti.status()
        self.assertEqual((st["state"], st["rows"]), ("ready", 200))
        self.assertIn("LICENSE", st["licenseError"])
        self.assertNotIn("LICENSE-danbooru-tags-kr", os.listdir(self.root))
        # 다시 켜도 CSV를 다시 받지 않는다
        ti2 = TagIndex(self.root, base_url=self.hub.url)
        self.assertTrue(ti2.refresh().startswith("up to date"))
        self.assertEqual(len(self.hub.downloads()), 1)
        self.assertIn("LICENSE", ti2.status()["licenseError"])
        # LICENSE가 생기면 받아 둔다
        self.hub.put("danbooru-tags-kr-2026-11-10.csv", make_csv(200))
        self.assertTrue(ti2.refresh().startswith("up to date"))
        self.assertIsNone(ti2.status()["licenseError"])
        self.assertIn("LICENSE-danbooru-tags-kr", os.listdir(self.root))
        self.assertEqual(len(self.hub.downloads()), 1)

    def test_license_download_failure_does_not_discard_index(self):
        self.hub.put("danbooru-tags-kr-2026-11-10.csv", make_csv(200))
        self.hub.srv.routes[f"/datasets/{REPO}/resolve/main/LICENSE"] = (500, {}, 0)
        ti = TagIndex(self.root, base_url=self.hub.url)
        self.assertTrue(ti.refresh().startswith("built"))
        self.assertIn("HTTP 500", ti.status()["licenseError"])


class StartOnLoad(unittest.TestCase):
    """태그 자동완성 끔(DESIGN 8절 "켜고 끄기"): 로딩 때 확인하지 않고, 켠 뒤 첫 검색에서 한 번만 시작한다."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "tags")
        self.spawned = []

    def tearDown(self):
        self.tmp.cleanup()

    def spawn(self, target):
        self.spawned.append(target)

    def test_on_load_follows_setting(self):
        on = TagIndex(self.root)
        self.assertTrue(tagupdate.on_load(on, Settings(), self.spawn))
        self.assertEqual((len(self.spawned), on.status()["check"]), (1, "started"))
        off = TagIndex(self.root)
        self.assertFalse(tagupdate.on_load(off, Settings(tag_autocomplete=False), self.spawn))
        self.assertEqual((len(self.spawned), off.status()["check"]), (1, "off"))
        # 와일드카드 자동완성은 태그 인덱스와 상관없다
        self.assertTrue(tagupdate.on_load(TagIndex(self.root), Settings(wildcard_autocomplete=False), self.spawn))

    def test_first_search_starts_once(self):
        ti = TagIndex(self.root)
        tagupdate.on_load(ti, Settings(tag_autocomplete=False), self.spawn)
        self.assertEqual(self.spawned, [])
        self.assertTrue(ti.start_refresh(self.spawn))  # 켠 뒤 첫 검색(__init__.py의 /wakaaka/tags/suggest)
        for _ in range(5):
            self.assertFalse(ti.start_refresh(self.spawn))  # 다음 검색들은 다시 시작하지 않는다
        self.assertEqual(self.spawned, [ti.refresh])
        self.assertEqual(ti.status()["check"], "started")
        ti.skip_refresh()  # 시작한 뒤에는 off로 돌아가지 않는다
        self.assertEqual(ti.status()["check"], "started")

    def test_concurrent_requests_start_once(self):
        import threading
        ti = TagIndex(self.root)
        ti.skip_refresh()
        lock = threading.Lock()
        count = []

        def spawn(target):
            with lock:
                count.append(target)

        go = threading.Barrier(16)

        def worker():
            go.wait()
            ti.start_refresh(spawn)

        ts = [threading.Thread(target=worker) for _ in range(16)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(len(count), 1)

    @mock.patch.object(tagupdate, "MIN_ROWS", 100)
    def test_off_keeps_existing_index_and_real_start_works(self):
        hub = Hub()
        try:
            hub.put("danbooru-tags-kr-2026-11-10.csv", make_csv(200))
            first = TagIndex(self.root, base_url=hub.url)
            self.assertTrue(first.refresh().startswith("built"))
            files = sorted(os.listdir(self.root))
            # 다시 띄웠는데 태그 자동완성이 꺼져 있다: 확인하지 않고(요청 없음) 기존 인덱스는 그대로 쓴다
            n = len(hub.srv.requests)
            ti = TagIndex(self.root, base_url=hub.url)
            tagupdate.on_load(ti, Settings(tag_autocomplete=False))
            self.assertEqual(len(hub.srv.requests), n)
            self.assertEqual(sorted(os.listdir(self.root)), files)
            st = ti.status()
            self.assertEqual((st["state"], st["check"]), ("ready", "off"))
            self.assertTrue(ti.suggest("tag 0001")["tags"])
            # 켠 뒤 첫 검색: 실제 스레드로 한 번 확인한다(같은 파일이라 받지 않음)
            done = []
            self.assertTrue(ti.start_refresh(lambda f: done.append(f())))
            self.assertEqual(len(done), 1)
            self.assertGreater(len(hub.srv.requests), n)
            self.assertEqual(ti.status()["check"], "started")
        finally:
            hub.close()
