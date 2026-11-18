"""태그 검색을 실제 와카아카 서버의 /api/tags/suggest와 대조 (환경 변수 둘이 있을 때만).

    set WAKAAKA_LIVE_URL=https://wakaaka.baepoyong.com
    set WAKAAKA_TAGS_CSV=C:\\workspace\\wakaaka\\data\\danbooru-tags-kr-2026-11-04.csv
    python -I -m unittest discover -s tests -t . -p test_tags_live.py

CSV는 서버가 가져온 것과 같은 파일이어야 한다(서버 tag_import에 적힌 파일). 서버에는 GET만 보낸다.
"""

import json
import os
import tempfile
import time
import unittest
import urllib.request
from urllib.parse import quote

from wakaaka_node import USER_AGENT, tagindex

LIVE = os.environ.get("WAKAAKA_LIVE_URL")
CSV = os.environ.get("WAKAAKA_TAGS_CSV")

QUERIES = [
    # 영문: 접두, 부분 일치, 밑줄·공백·대소문자 변형, 짧은 질의, 특수 글자
    "long h", "long_h", "longh", "LONG H", "  long   h ", "1girl", "1g", "hair", "hai", "ha", "h", "l", "lo",
    "blue e", "blue_eyes", "blueeyes", "smile", "ahoge", "hatsune m", "hatsunem", "miku", "genshin", "school uni",
    "thighhigh", "thigh high", "sword", "red", "kancolle", "solo", "looking at", "at viewer", "viewer", "xyzw", "zzzz",
    "(", "_", "%", "100%", "o_o", "^_^", ">_<", "star (", "\\", "'s", "pokemon (", "nail polish", "ear", "ears",
    # 한국어: 키워드 전체·접두·낱말 접두·부분 일치
    "긴 머리", "긴머리", "머리", "머", "캐릭터", "고양이", "고양이 귀", "교복", "세일러", "타마키", "금발", "파란 눈",
    "파란눈", "눈", "하츠네 미쿠", "미쿠", "원신", "웃음", "미소", "가나다라", "여자 1명", "여자", "인원", "소녀전선",
    "작가", "동물 귀", "귀", "칸코레", "등장함", "있는", "miku 미쿠", "ㄱ", "머리카락이 긴",
]


def server(q):
    req = urllib.request.Request(f"{LIVE.rstrip('/')}/api/tags/suggest?q={quote(q, safe='')}", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


@unittest.skipUnless(LIVE and CSV, "WAKAAKA_LIVE_URL, WAKAAKA_TAGS_CSV가 없음")
class LiveSuggest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = os.path.join(cls.tmp.name, "live.sqlite")
        tagindex.build(CSV, cls.db, {"file": os.path.basename(CSV), "sha256": "", "size": 0})
        cls.con = tagindex._open(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.con.close()
        cls.tmp.cleanup()

    def test_same_as_server(self):
        diffs = []
        for q in QUERIES:
            want = server(q)
            got = tagindex.suggest(self.con, q)
            if got != want:
                diffs.append((q, [t["name"] for t in want["tags"]], [t["name"] for t in got["tags"]]))
            time.sleep(0.05)
        print(f"\n[tags live] {len(QUERIES)} queries, {len(QUERIES) - len(diffs)} same")
        for d in diffs:
            print("  differs:", ascii(d))
        self.assertEqual(diffs, [])


if __name__ == "__main__":
    unittest.main()
