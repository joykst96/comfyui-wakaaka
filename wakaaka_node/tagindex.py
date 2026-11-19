"""태그 인덱스: SQLite 파일 만들기와 검색 (DESIGN 8절 "노드의 태그 인덱스", 7절 "태그 자동완성").

검색 규칙과 응답 모양은 서버 `/api/tags/suggest`와 같다. 옮겨 온 곳(와카아카 커밋 6b28ef5):
`apps/api/src/services/tags.ts`(suggest), `apps/api/src/repositories/tags.ts`(englishPrefix, englishContains, suggestKorean).

- 영문: 이름 키(밑줄·공백 제거, 소문자) 접두 일치를 post_count 순으로, 모자라면 3글자 이상일 때 부분 일치를 post_count 순으로.
- 한국어: 키워드 전체 같음 → 키워드 접두 → 키워드 낱말 접두 → (3글자 이상, 앞 단계가 20개 미만일 때) 키워드·본문 부분 일치.
  단계마다 post_count 순으로 2×20행만 고르고, 태그마다 가장 앞 단계로 정렬한다(서버 suggestKorean과 같은 자르기).
- 같은 post_count는 id(CSV 행 순서) 순이다. 서버의 id도 가져올 때 CSV 순서로 매겨진다.

검색 방식(실측은 NOTES 2026-11-19, 11-04 파일 150,993행):
- 접두는 커버링 색인 (name_key, post_count, id)의 범위를 훑고 정렬한다. 색인만 읽어서 `s`(16,853개)도 1.3ms다.
  이름 키 색인만 두면 행마다 표를 읽어 94ms, post_count 색인을 따라가면 드문 접두에서 60ms였다.
- 부분 일치는 FTS5 trigram(detail=full)으로 후보를 고른다. 후보가 CAP개 이상이면 post_count 순으로 표를 훑으며 instr로 거른다.
  일치가 드물면 FTS가 0.1~0.3ms(훑기 60~80ms), 흔하면 훑기가 0.2ms(FTS 20~37ms)라 서버의 "울타리"처럼 둘을 고른다.
  둘의 결과가 같음은 테스트한다. detail=none·column은 구문 질의를 거부한다.

파일은 다 만든 뒤에만 최종 이름을 얻는다(tagupdate.py). 그래서 최종 이름의 파일은 늘 완전하다. 읽을 때는 질의마다 읽기 전용으로 연다.
Windows에서는 열려 있는 파일을 바꿔 넣을 수 없어서, 연결을 오래 쥐지 않는다.
"""

import json
import os
import sqlite3
import time

from . import tagcsv

FORMAT = 1  # 인덱스 스키마 버전. 바꾸면 기존 인덱스를 다시 만든다.
LIMIT = 20
Q_MAX = 64  # 코드 포인트
Q_RAW_MAX = Q_MAX * 4  # 서버 zod max(256): UTF-16 길이
CONTAINS_MIN = 3
BODY_MAX = 200  # 응답 본문은 200자까지(서버 left(body, 200))
BATCH = 5000

SCHEMA = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE tag(
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  category INTEGER NOT NULL,
  post_count INTEGER NOT NULL,
  body TEXT NOT NULL,
  keywords TEXT NOT NULL,
  name_key TEXT NOT NULL,
  ko_text TEXT NOT NULL
);
CREATE TABLE tag_keyword(
  kind INTEGER NOT NULL,
  key TEXT NOT NULL,
  post_count INTEGER NOT NULL,
  tag_id INTEGER NOT NULL,
  PRIMARY KEY(kind, key, post_count DESC, tag_id)
) WITHOUT ROWID;
CREATE VIRTUAL TABLE name_fts USING fts5(name_key, content='tag', content_rowid='id', tokenize='trigram case_sensitive 1', detail=full, columnsize=0);
CREATE VIRTUAL TABLE ko_fts USING fts5(ko_text, content='tag', content_rowid='id', tokenize='trigram case_sensitive 1', detail=full, columnsize=0);
"""
INDEXES = """
CREATE INDEX tag_name_key ON tag(name_key, post_count, id);
CREATE INDEX tag_post_count ON tag(post_count DESC, id);
INSERT INTO name_fts(name_fts) VALUES('rebuild');
INSERT INTO ko_fts(ko_fts) VALUES('rebuild');
INSERT INTO name_fts(name_fts) VALUES('optimize');
INSERT INTO ko_fts(ko_fts) VALUES('optimize');
"""
CAP = 1000  # 부분 일치 후보가 이보다 많으면 FTS 대신 post_count 순 훑기


def fts5_trigram_support():
    """이 Python의 SQLite가 FTS5 trigram(detail=full)을 만들 수 있으면 None, 아니면 이유. 인덱스를 받기 전에 본다."""
    try:
        con = sqlite3.connect(":memory:")
        try:
            con.execute("CREATE VIRTUAL TABLE t USING fts5(a, tokenize='trigram case_sensitive 1', detail=full, columnsize=0)")
            con.execute("INSERT INTO t VALUES ('abcd')")
            con.execute("SELECT rowid FROM t WHERE t MATCH '\"bcd\"'").fetchall()
        finally:
            con.close()
    except sqlite3.Error as e:
        return f"{e} (SQLite {sqlite3.sqlite_version})"
    return None


class QueryError(ValueError):
    """서버가 400 E_QUERY로 답하는 질의."""


# ---------------------------------------------------------------- 만들기


def build(csv_path, out_path, source):
    """CSV를 읽어 out_path에 인덱스를 만든다. 반환: tagcsv.Stats. out_path가 있으면 덮어쓴다.

    source: {"file": 원본 파일 이름, "sha256": ..., "size": ...}. meta에 행 수·만든 시각·통계와 함께 적는다.
    형식 검증은 하지 않는다(부르는 쪽이 stats로 판단한다). CSV 문법·UTF-8 오류는 예외로 올라간다.
    """
    if os.path.exists(out_path):
        os.unlink(out_path)
    stats = tagcsv.Stats()
    t0 = time.perf_counter()
    con = sqlite3.connect(out_path)
    try:
        con.execute("PRAGMA journal_mode=OFF")
        con.execute("PRAGMA synchronous=OFF")
        con.execute("PRAGMA cache_size=-131072")  # 128MB. 작으면 키워드 표(키 순서가 아닌 삽입 약 100만 행)가 8배 느리다
        con.executescript(SCHEMA)
        tags, kws = [], []
        with open(csv_path, encoding="utf-8", errors="strict", newline="") as f:
            for i, (name, category, post_count, body, keywords) in enumerate(tagcsv.read_rows(f, stats), 1):
                tags.append((i, name, category, post_count, body[:BODY_MAX], json.dumps(keywords, ensure_ascii=False),
                             tagcsv.name_key(name), tagcsv.ko_text(keywords, body)))
                kws.extend((kind, key, post_count, i) for key, kind in tagcsv.keyword_rows(keywords))
                if len(tags) >= BATCH:
                    _flush(con, tags, kws)
        _flush(con, tags, kws)
        con.executescript(INDEXES)
        meta = {
            "format": FORMAT,
            "file": source.get("file", ""),
            "sha256": source.get("sha256", ""),
            "size": source.get("size", 0),
            "rows": stats.rows,
            "builtAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "buildSeconds": round(time.perf_counter() - t0, 2),
            "stats": stats.as_dict(),
        }
        con.executemany("INSERT INTO meta VALUES (?, ?)", [(k, json.dumps(v, ensure_ascii=False)) for k, v in meta.items()])
        con.commit()
        con.execute("ANALYZE")
        con.commit()
        con.execute("VACUUM")  # FTS rebuild·optimize가 남긴 빈 페이지(약 8MB)를 거둔다. 2초쯤 든다
    finally:
        con.close()
    return stats


def _flush(con, tags, kws):
    con.executemany("INSERT INTO tag VALUES (?, ?, ?, ?, ?, ?, ?, ?)", tags)
    con.executemany("INSERT INTO tag_keyword VALUES (?, ?, ?, ?)", kws)
    tags.clear()
    kws.clear()


def read_meta(path):
    """인덱스의 meta. 열 수 없거나 형식이 다르면 None."""
    try:
        con = _open(path)
    except sqlite3.Error:
        return None
    try:
        meta = {k: json.loads(v) for k, v in con.execute("SELECT key, value FROM meta")}
        con.execute("SELECT id FROM tag LIMIT 1").fetchall()
    except (sqlite3.Error, ValueError):
        return None
    finally:
        con.close()
    if meta.get("format") != FORMAT or not isinstance(meta.get("rows"), int):
        return None
    return meta


def _open(path):
    uri = "file:" + os.path.abspath(path).replace("\\", "/").replace("?", "%3f").replace("#", "%23") + "?mode=ro"
    return sqlite3.connect(uri, uri=True, check_same_thread=False)


# ---------------------------------------------------------------- 검색


def normalize_query(q):
    """서버 suggest의 입력 처리. 잘못된 질의는 QueryError, 빈 질의는 ""."""
    if not isinstance(q, str) or tagcsv.u16len(q) > Q_RAW_MAX:
        raise QueryError("query does not match the schema")
    if not tagcsv.is_safe_string(q):
        raise QueryError("q contains characters that are not allowed")
    q = tagcsv.collapse_ws(_trim_spec(q)).lower()
    if len(q) > Q_MAX:
        raise QueryError(f"q must be at most {Q_MAX} characters")
    return q


def _trim_spec(s):
    """core trimSpecWhitespace: SPEC 1절의 공백 네 글자(공백, 탭, LF, CR)만 앞뒤에서 지운다."""
    return s.strip(" \t\n\r")


def _next_prefix(p):
    """p로 시작하는 모든 문자열보다 크고, p보다 큰 문자열 중 p로 시작하지 않는 가장 작은 것. 없으면 None.

    SQLite의 BINARY 비교는 UTF-8 바이트 순이고 이는 코드 포인트 순과 같다.
    """
    while p:
        c = ord(p[-1])
        if c < 0x10FFFF:
            c += 1
            if 0xD800 <= c <= 0xDFFF:
                c = 0xE000
            return p[:-1] + chr(c)
        p = p[:-1]
    return None


def _prefix_where(col, p):
    hi = _next_prefix(p)
    if hi is None:
        return f"{col} >= ?", [p]
    return f"{col} >= ? AND {col} < ?", [p, hi]


COLS = "t.name, t.category, t.post_count, t.body, t.keywords"
ORDER = "ORDER BY t.post_count DESC, t.id LIMIT ?"


def _view(row):
    name, category, post_count, body, keywords = row
    return {"name": name, "category": category, "postCount": post_count, "body": body, "keywords": json.loads(keywords)}


def _phrase(s):
    return '"' + s.replace('"', '""') + '"'


def contains_ids(con, col, needle, limit, exclude=None):
    """col(name_key·ko_text)에 needle(3글자 이상)이 든 태그 id를 post_count·id 순으로 limit개. exclude는 (where, args)로 뺄 조건."""
    fts = {"name_key": "name_fts", "ko_text": "ko_fts"}[col]
    ex_sql, ex_args = ("", []) if exclude is None else (f" AND NOT ({exclude[0]})", exclude[1])
    n = con.execute(f"SELECT count(*) FROM (SELECT rowid FROM {fts} WHERE {fts} MATCH ? LIMIT ?)", [_phrase(needle), CAP]).fetchone()[0]
    if n < CAP:
        sql = f"SELECT t.id FROM {fts} f JOIN tag t ON t.id = f.rowid WHERE {fts} MATCH ?{ex_sql} {ORDER}"
        args = [_phrase(needle), *ex_args, limit]
    else:
        sql = f"SELECT t.id FROM tag t INDEXED BY tag_post_count WHERE instr(t.{col}, ?) > 0{ex_sql} {ORDER}"
        args = [needle, *ex_args, limit]
    return [r[0] for r in con.execute(sql, args).fetchall()]


def suggest(con, q):
    """서버 /api/tags/suggest와 같은 결과: {"tags": [...]} (최대 20개)."""
    q = normalize_query(q)
    if q == "":
        return {"tags": []}
    if tagcsv.has_hangul(q):
        return {"tags": _fetch(con, _korean(con, q))}
    key = tagcsv.name_key(q)
    if key == "":
        return {"tags": []}
    where, args = _prefix_where("t.name_key", key)
    ids = [r[0] for r in con.execute(f"SELECT t.id FROM tag t INDEXED BY tag_name_key WHERE {where} {ORDER}", [*args, LIMIT]).fetchall()]
    if len(key) >= CONTAINS_MIN and len(ids) < LIMIT:
        ids += contains_ids(con, "name_key", key, LIMIT - len(ids), exclude=(where, args))
    return {"tags": _fetch(con, ids)}


def _fetch(con, ids):
    if not ids:
        return []
    rows = {r[0]: r[1:] for r in con.execute(f"SELECT t.id, {COLS} FROM tag t WHERE t.id IN ({','.join('?' * len(ids))})", ids)}
    return [_view(rows[i]) for i in ids]


def _korean(con, q):
    """한국어 단계 검색. 태그 id를 순위대로."""
    key = tagcsv.ko_key(q)  # 비지 않는다: 한글이 있다
    n = 2 * LIMIT
    tiers = {}  # tag_id -> 가장 앞 단계

    def stage(ids, tier):
        for tid in ids:
            if tid not in tiers or tiers[tid] > tier:
                tiers[tid] = tier

    def kw(where, args):
        sql = f"SELECT tag_id FROM tag_keyword WHERE {where} ORDER BY post_count DESC, tag_id LIMIT ?"
        return [r[0] for r in con.execute(sql, [*args, n]).fetchall()]

    where, args = _prefix_where("key", key)
    stage(kw("kind = 0 AND key = ?", [key]), 0)
    stage(kw(f"kind = 0 AND {where}", args), 1)
    stage(kw(f"kind = 1 AND {where}", args), 2)
    if len(key) >= CONTAINS_MIN and len(tiers) < LIMIT:
        stage(contains_ids(con, "ko_text", q, n), 3)
    if not tiers:
        return []
    pcs = dict(con.execute(f"SELECT id, post_count FROM tag WHERE id IN ({','.join('?' * len(tiers))})", list(tiers)).fetchall())
    return sorted(tiers, key=lambda i: (tiers[i], -pcs[i], i))[:LIMIT]
