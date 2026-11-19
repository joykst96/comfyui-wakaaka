"""노드 `Wakaaka Text`: 여러 줄 텍스트를 전개한다 (DESIGN 8절 "노드 `Wakaaka Text`", 2026-11-19 확정).

1. 줄 주석: 파싱 전에 첫 글자가 `#`인 줄을 줄바꿈째 지운다. 줄 맨 앞 `\\#`은 글자 `#`이다.
2. 편집 형태로 파싱해 `${이름}`을 `${w:ID}`로 바꾼다. 이름 해석: refs 표(대소문자 무시) → 설정된 유저의 목록 → 노드 오류.
3. 항목 하나짜리 임시 루트 문서 `{"schema":1,"items":[{"id":"t","text":…}]}`로 감싼다. 항목 id `t`는 난수 경로에 들어가므로 바꾸지 않는다.
4. 참조된 ID마다 최신 묶음을 받아 합친다(lock·docs 합집합, root는 임시 루트). 같은 ID의 해시가 엇갈리면 한 번 다시 받고, 그래도면 오류.
5. maxDepth 17로 전개한다(임시 루트가 깊이를 1 더한다). maxExpansions는 텍스트 전체가 함께 쓴다.

모드와 상관없이 노드에서 전개한다(서버에는 임의 텍스트를 전개하는 엔드포인트가 없다). 항상 최신으로 전개하고 lock은 기록만 한다.
서버에 연결할 수 없으면 기존 로컬 모드처럼 마지막으로 받은 최신 lock의 캐시로 전개한다. 최신 lock은 Text 자기 기록(`latest-text/`)을
먼저 보고, 없으면 Wakaaka Wildcard의 기록(`latest/`)을 본다. Text는 버전 번호를 모르므로 Wildcard의 기록에는 쓰지 않는다.
"""

TEXT_LATEST = "latest-text"

import json

from . import bundle as B
from .client import ApiError, Client, NetworkError
from .expander import ExpandError, Expander, parse
from .fill import FillError, api_error, fetch_bundle, not_found
from .ids import ID12, valid_lock
from .jcs import content_hash
from .userlist import find

ITEM_ID = "t"
LIMITS = {"maxDepth": 17}
SEED_MAX = (1 << 64) - 1

SYNTAX_MESSAGES = {
    "E_UNCLOSED_CHOICE": "닫히지 않은 {",
    "E_UNMATCHED_CLOSE": "짝이 없는 }",
    "E_UNCLOSED_REF": "닫히지 않은 ${",
    "E_REF_EMPTY": "빈 참조 ${}",
    "E_REF_INVALID": "참조에 쓸 수 없는 글자",
    "E_REF_RESERVED": "예약된 문법(매개변수·변수·h:)",
    "E_NESTING": "선택지가 64겹을 넘음",
    "E_CHOICE_WEIGHT": "보기 가중치는 0~1,000,000 정수(예: 3*)이고, 모든 보기가 0이면 안 됨",
}


class TextResult:
    def __init__(self, text, refs, lock, source):
        self.text = text  # 전개 결과(result 위젯)
        self.refs = refs  # 이름 → ID (텍스트에 나오는 순서)
        self.lock = lock  # ID → 해시
        self.source = source  # "local" | "cache"(서버에 닿지 않아 캐시로 전개)

    def __repr__(self):
        return f"TextResult({self.text!r}, refs={self.refs}, {self.source})"


def strip_comment_lines(text):
    """첫 글자가 `#`인 줄을 줄바꿈째 지운다. 남은 줄의 맨 앞 `\\#`은 글자 `#`.

    남은 첫 줄이면 텍스트 맨 앞이므로 SPEC 2.2의 시작 이스케이프 `\\#`을 그대로 두고, 둘째 줄부터는 `#`로 바꾼다
    (텍스트 중간의 `\\#`은 SPEC상 글자 두 개라서).
    """
    kept = []
    for line in text.split("\n"):
        if line.startswith("#"):
            continue
        if kept and line.startswith("\\#"):
            line = line[1:]
        kept.append(line)
    return "\n".join(kept)


def parse_refs_value(s):
    """refs 위젯 값 → {"refs": {이름: ID}, "lock": {ID: 해시}}(+ prompt 안에서만 "run"·"error"). 틀리면 빈 표."""
    out = {"refs": {}, "lock": {}}
    if not isinstance(s, str) or s.strip() == "":
        return out
    try:
        v = json.loads(s, parse_constant=_reject)
    except (ValueError, RecursionError):
        return out
    if not isinstance(v, dict):
        return out
    if "error" in v:
        return {"error": str(v["error"])}
    refs = v.get("refs")
    if isinstance(refs, dict):
        out["refs"] = {k: x for k, x in refs.items() if isinstance(k, str) and isinstance(x, str) and ID12.fullmatch(x)}
    if valid_lock(v.get("lock")):
        out["lock"] = v["lock"]
    if isinstance(v.get("run"), str):
        out["run"] = v["run"]
    return out


def dump_refs_value(refs, lock, **extra):
    return json.dumps({"refs": refs, "lock": dict(sorted(lock.items())), **extra}, separators=(",", ":"), ensure_ascii=False)


def _reject(name):
    raise ValueError(f"non-finite number {name}")


def to_stored(text, refs_value, settings, client=None, userlists=None):
    """주석 줄을 지우고 이름을 ID로 바꾼다. 반환: (저장 형태 텍스트, 이름 → ID, 참조된 ID 목록(순서대로, 겹침 없음))."""
    src = strip_comment_lines(text)
    spans = []
    try:
        parse(src, "edit", spans)
    except ExpandError as e:
        raise FillError(f"문법 오류: {SYNTAX_MESSAGES.get(e.code, e.code)} ({e.code})") from None
    table = {k.lower(): v for k, v in parse_refs_value(refs_value).get("refs", {}).items()}
    names = []
    for _, _, r in spans:
        if r.name is not None and r.name not in names:
            names.append(r.name)
    resolved = {}
    missing = []
    for n in names:
        wid = table.get(n.lower())
        if wid is not None:
            resolved[n] = wid
        else:
            missing.append(n)
    if missing:
        items, state = userlists.get(settings, client) if userlists is not None else ([], "unavailable")
        still = []
        for n in missing:
            wid = find(items, n)
            if wid is None:
                still.append(n)
            else:
                resolved[n] = wid
        if still:
            why = {
                "no_user": "설정 → Wakaaka에서 유저 아이디를 넣으세요",
                "bad_user": f"유저 아이디 설정이 올바르지 않음: {settings.raw_user!r}",
                "not_found": f"유저 {settings.user}가 없음",
                "unavailable": "유저 목록을 받을 수 없음",
            }.get(state, f"유저 {settings.user}의 와일드카드에 없음")
            raise FillError("와일드카드 이름을 찾을 수 없음: " + ", ".join("${" + n + "}" for n in still) + f" ({why})")
    out, at, ids = [], 0, []
    for a, b, r in spans:
        wid = r.id if r.id is not None else resolved[r.name]
        out.append(src[at:a])
        out.append("${w:" + wid + "}")
        at = b
        if wid not in ids:
            ids.append(wid)
    out.append(src[at:])
    return "".join(out), {n: resolved[n] for n in names}, ids


def _latest(wid, client, cache):
    """최신 묶음과 출처. 서버에 닿지 않으면 마지막으로 받은 최신 lock의 캐시."""
    try:
        b = fetch_bundle(client, wid)
    except NetworkError as e:
        b = None
        if cache is not None:
            for kind in (TEXT_LATEST, "latest"):
                src = cache.get_latest(wid, kind)
                b = cache.bundle(wid, src["lock"]) if src is not None else None
                if b is not None:
                    break
        if b is None:
            raise not_found(wid, f"서버에 연결할 수 없고 캐시도 없음: {e}") from None
        return b, "cache"
    except ApiError as e:
        raise api_error(wid, e) from None
    except B.BundleError as e:
        raise FillError(str(e)) from None
    if cache is not None:
        try:
            cache.put_bundle(b)
            cache.put_latest(wid, b["lock"], None, TEXT_LATEST)
        except OSError:
            pass
    return b, "local"


def _merge(bundles):
    lock, docs = {}, {}
    for b in bundles:
        for k, v in b["lock"].items():
            if lock.setdefault(k, v) != v:
                return None, k
        docs.update(b["docs"])
    return (lock, docs), None


def fill_text(text, seed, refs_value, settings, client=None, cache=None, userlists=None):
    """큐에 넣을 때 노드 하나를 채운다 → TextResult. 채울 수 없으면 FillError."""
    if not isinstance(text, str):
        raise FillError("text가 문자열이 아님")
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= SEED_MAX:
        raise FillError(f"seed는 0 ~ 2^64-1 정수여야 함: {seed!r}")
    if settings.url is None:
        raise FillError(f"와카아카 서버 주소 설정이 올바르지 않음: {settings.raw_url!r} (설정 → Wakaaka)")
    client = client or Client(settings.url)
    stored, refs, ids = to_stored(text, refs_value, settings, client, userlists)
    got = [_latest(wid, client, cache) for wid in ids]
    merged, clash = _merge([b for b, _ in got])
    if merged is None:  # 받는 사이에 하위 와일드카드가 갱신됨: 한 번 다시 받는다
        got = [_latest(wid, client, cache) for wid in ids]
        merged, clash = _merge([b for b, _ in got])
        if merged is None:
            raise FillError(f"참조한 와일드카드들의 하위 버전이 엇갈림: {clash} (잠시 뒤 다시 큐에 넣으세요)")
    lock, docs = merged
    root = {"schema": 1, "items": [{"id": ITEM_ID, "text": stored}]}
    h = content_hash(root)
    docs = dict(docs)
    docs[h] = root
    try:
        out = Expander({"format": 1, "root": h, "lock": lock, "docs": docs}, LIMITS).expand(seed)
    except ExpandError as e:
        raise FillError(f"전개하지 못함: {e.code}" + (f" ({e.wildcard})" if e.wildcard else "")) from None
    source = "cache" if any(s == "cache" for _, s in got) else "local"
    return TextResult(out, refs, lock, source)
