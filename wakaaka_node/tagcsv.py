"""태그 CSV 읽기 (DESIGN 6절 "태그 가져오기"의 헤더 없는 형식, 8절 "노드의 태그 인덱스").

Hugging Face `mengod-hf/danbooru-tags-kr`의 통합본만 읽는다: 헤더 없는 4열 `name,category,post_count,description`.
서버 파서 `apps/api/src/services/tagcsv.ts`(와카아카 커밋 6b28ef5)의 헤더 없는 형식 규칙을 옮겼다.
서버와 다른 점은 둘이다.
- 키워드 표기 둘을 모두 읽는다: `… 키워드: <묶음>, a, b`(11-04까지, 서버와 같음)와 `… <키워드> 묶음, a, b.`(11-10부터).
- 틀린 행이 있어도 멈추지 않고 건너뛰며 센다. 받아들일지는 형식 검증 관문(tagindex.py)이 정한다.

JS와 결과가 같아야 하는 곳(공백, trim)은 JS의 정의를 그대로 쓴다. Python의 `\\s`·`str.strip()`은 JS와 글자 집합이 다르다
(Python은 U+001C~U+001F·U+0085를 넣고 U+FEFF를 뺀다).
"""

import csv
import re

# JS의 \s와 String.prototype.trim이 지우는 글자(WhiteSpace + LineTerminator)
JS_WS_CHARS = "\t\n\v\f\r \u00a0\u1680" + "".join(map(chr, range(0x2000, 0x200B))) + "\u2028\u2029\u202f\u205f\u3000\ufeff"
JS_WS = "".join(re.escape(c) for c in JS_WS_CHARS)  # 정규식 글자 묶음 [...] 안에 넣는 형태
_JS_WS_RUN = re.compile(f"[{JS_WS}]+")
_JS_WS_ANY = re.compile(f"[{JS_WS}]")
_NAME_KEY_DROP = re.compile(f"[_{JS_WS}]")

CATEGORIES = (0, 1, 3, 4, 5)  # Danbooru: general 0, artist 1, copyright 3, character 4, meta 5
NAME_MAX = 256
TEXT_MAX = 20_000
POST_COUNT_MAX = 2**31 - 1

_DESC = re.compile(f"^\\[([^\\]>]+?) > ([^\\]]+)\\][{JS_WS}]*")
_SINGLE = re.compile(f"^\\[([^\\]>]+)\\][{JS_WS}]*")
_GROUP_LABEL = re.compile(r"^<[^<>]*>$")
OLD_MARK = "키워드:"
NEW_MARK = "<키워드>"
_HANGUL = re.compile("[\u1100-\u11ff\u3130-\u318f\uac00-\ud7a3]")
_UNSAFE = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f]")  # 탭·줄바꿈·CR을 뺀 C0 (core isSafeString)


def js_trim(s):
    return s.strip(JS_WS_CHARS)


def u16len(s):
    """JS의 String.length(UTF-16 코드 단위 수)."""
    return len(s) + sum(1 for c in s if ord(c) > 0xFFFF)


def is_safe_string(s):
    """core `isSafeString`: 짝 없는 서로게이트·U+0000·탭/LF/CR 밖의 C0 제어문자가 없다."""
    if _UNSAFE.search(s):
        return False
    try:
        s.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def has_hangul(s):
    return _HANGUL.search(s) is not None


def name_key(s):
    """영문 검색 키: 밑줄·공백을 빼고 소문자 (DESIGN 7절)."""
    return _NAME_KEY_DROP.sub("", s).lower()


def ko_key(s):
    """한국어 검색 키: 공백을 빼고 소문자."""
    return _JS_WS_ANY.sub("", s).lower()


def collapse_ws(s):
    return _JS_WS_RUN.sub(" ", s)


def _split_list(s, sep):
    out = []
    for x in s.split(sep):
        x = js_trim(x)
        if x and x not in out:
            out.append(x)
    return out


def split_description(description, name=None):
    """설명 → (본문, 키워드, 표기). 표기는 "old"·"new"·None. name은 태그 이름(밑줄 표기).

    서버 splitHeaderlessDescription과 같다. 다만 새 표기 `<키워드> a, b.`도 읽는다: 표시 뒤를 키워드로 보고 끝의 마침표를 뗀다.
    원본은 마지막 키워드가 이미 `.`로 끝나면 마침표를 덧붙이지 않는다(`loading..`, `studio C.H.`). 그래서 끝이 `..`이거나
    마지막 키워드가 태그 이름과 같으면(`monsters inc.`) 떼지 않는다. 11-04와 11-10을 대조해 고른 규칙이다(NOTES 2026-11-19).
    두 표시가 다 있으면 뒤에 있는 것을 쓴다(실제 파일에는 없다).
    """
    body = description
    m = _DESC.match(body)
    if m:
        body = body[m.end():]
    keywords, form = [], None
    k_old, k_new = body.rfind(OLD_MARK), body.rfind(NEW_MARK)
    if k_old >= 0 and k_old > k_new:
        keywords = _split_list(body[k_old + len(OLD_MARK):], ",")
        body, form = body[:k_old], "old"
    elif k_new >= 0:
        keywords = _split_list(body[k_new + len(NEW_MARK):], ",")
        if keywords and keywords[-1].endswith("."):
            last = keywords[-1]
            if not last.endswith("..") and (name is None or name_key(last) != name_key(name)):
                keywords[-1] = js_trim(last[:-1])
                if keywords[-1] == "" or keywords[-1] in keywords[:-1]:
                    keywords.pop()
        body, form = body[:k_new], "new"
    body = js_trim(body)
    if not m:
        s = _SINGLE.match(body)
        if s:
            body = js_trim(body[s.end():])
    out = []
    for k in keywords:
        if _GROUP_LABEL.match(k):
            k = js_trim(k[1:-1])
        if k and k not in out:
            out.append(k)
    return body, out, form


def _check_name(raw):
    """헤더 없는 형식은 이름의 밑줄을 공백으로 적는다. 되돌린 이름, 틀리면 None."""
    if raw != js_trim(raw) or "  " in raw or "_" in raw:
        return None
    name = raw.replace(" ", "_")
    if name == "" or u16len(name) > NAME_MAX or _JS_WS_ANY.search(name):
        return None
    return name


def _check_int(s, pattern, limit):
    if not pattern.fullmatch(s):
        return None
    v = int(s)
    return v if v <= limit else None


_CAT = re.compile(r"[0-9]")
_PC = re.compile(r"[0-9]{1,10}")


class Stats:
    """형식 검증에 쓰는 셈. bad는 사유별 건너뛴 행 수."""

    def __init__(self):
        self.rows = 0  # 받아들인 행
        self.lines = 0  # 읽은 행(빈 줄 제외)
        self.bad = {}
        self.forms = {"old": 0, "new": 0, None: 0}
        self.with_keywords = 0
        self.examples = []  # 처음 몇 개의 틀린 행

    def reject(self, line, why):
        self.bad[why] = self.bad.get(why, 0) + 1
        if len(self.examples) < 5:
            self.examples.append(f"line {line}: {why}")

    @property
    def bad_rows(self):
        return sum(self.bad.values())

    def as_dict(self):
        return {
            "rows": self.rows,
            "lines": self.lines,
            "bad": dict(self.bad),
            "oldForm": self.forms["old"],
            "newForm": self.forms["new"],
            "noKeywordMark": self.forms[None],
            "withKeywords": self.with_keywords,
            "examples": list(self.examples),
        }


def read_rows(f, stats):
    """열린 텍스트 파일(newline="")에서 태그를 하나씩 낸다: (이름, 분류, 수, 본문, 키워드).

    CSV 문법이 틀리면 csv.Error, UTF-8이 틀리면 UnicodeDecodeError로 멈춘다(파일 전체가 의심스럽다).
    행 단위 문제(열 수, 숫자, 이름, 금지 문자, 중복 이름)는 건너뛰고 stats에 센다.
    """
    names = set()
    reader = csv.reader(f, strict=True)
    for fields in reader:
        line = reader.line_num
        if len(fields) == 0 or (len(fields) == 1 and fields[0] == ""):
            continue  # 빈 줄
        stats.lines += 1
        if len(fields) != 4:
            stats.reject(line, "columns")
            continue
        if not all(is_safe_string(x) and (len(x) <= TEXT_MAX // 2 or u16len(x) <= TEXT_MAX) for x in fields):
            stats.reject(line, "characters")
            continue
        raw, cat, pc, description = fields
        name = _check_name(raw)
        if name is None:
            stats.reject(line, "name")
            continue
        category = _check_int(cat, _CAT, 9)
        post_count = _check_int(pc, _PC, POST_COUNT_MAX)
        if category not in CATEGORIES or post_count is None:
            stats.reject(line, "number")
            continue
        if name in names:
            stats.reject(line, "duplicate")
            continue
        names.add(name)
        body, keywords, form = split_description(description, name)
        stats.rows += 1
        stats.forms[form] += 1
        if keywords:
            stats.with_keywords += 1
        yield name, category, post_count, body, keywords


def keyword_rows(keywords):
    """한국어 접두 검색 표의 (키, 종류). 종류 0: 키워드 전체(공백 제거), 1: 공백으로 나눈 낱말. 같은 키는 0이 이긴다.

    서버 keywordRows와 같다. 헤더 없는 형식에는 other_names가 없어 키워드만 쓴다.
    """
    out = {}
    for k in keywords:
        key = ko_key(k)
        if key:
            out[key] = 0
    for k in keywords:
        words = [w for w in _JS_WS_RUN.split(js_trim(k))]
        if len(words) < 2:
            continue
        for w in words:
            key = w.lower()
            if key and key not in out:
                out[key] = 1
    return list(out.items())


def ko_text(keywords, body):
    """한국어 부분 일치용 글: 키워드들과 한글이 든 본문 (서버 koText)."""
    parts = list(keywords)
    if has_hangul(body):
        parts.append(body)
    return "\n".join(parts).lower()
