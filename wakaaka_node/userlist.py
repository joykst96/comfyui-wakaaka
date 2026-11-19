"""설정된 유저의 와일드카드 목록 (DESIGN 8절 "노드의 자동완성").

`GET /api/users/{아이디}/wildcards`는 페이지 나눔 없이 통째로 오므로 Python 메모리에 약 60초 둔다.
받지 못하면(연결 실패, 서버 오류) 마지막 목록을 쓴다. 없는 아이디(404)도 60초 동안 기억한다.
`${` 자동완성(`/wakaaka/wildcards/suggest`)과 큐에 넣을 때의 이름 해석(text.py)이 같이 쓴다.
"""

import threading
import time

from .client import ApiError, Client, NetworkError
from .tagcsv import js_trim

TTL = 60.0
LIMIT = 30


class UserLists:
    def __init__(self, ttl=TTL, clock=time.monotonic):
        self.ttl = ttl
        self.clock = clock
        self._lock = threading.Lock()
        self._entries = {}  # (서버 주소, 아이디) → {"at", "items", "state"}

    def get(self, settings, client=None):
        """(목록, 상태). 상태: ok, stale(받지 못해 마지막 목록), no_user, bad_user, not_found, unavailable(받지 못했고 목록도 없음)."""
        if settings.user is None:
            return [], ("no_user" if js_trim(settings.raw_user) == "" else "bad_user")
        if settings.url is None:
            return [], "unavailable"
        key = (settings.url, settings.user)
        with self._lock:
            e = self._entries.get(key)
        if e is not None and self.clock() - e["at"] < self.ttl:
            return e["items"], e["state"]
        client = client or Client(settings.url)
        try:
            items, state = client.user_wildcards(settings.user), "ok"
        except ApiError as err:
            if err.status == 404:
                items, state = [], "not_found"
            else:
                return self._stale(e)
        except NetworkError:
            return self._stale(e)
        with self._lock:
            self._entries[key] = {"at": self.clock(), "items": items, "state": state}
        return items, state

    @staticmethod
    def _stale(e):
        if e is not None and e["state"] == "ok":
            return e["items"], "stale"
        return [], "unavailable"

    def forget(self):
        with self._lock:
            self._entries.clear()


def rank(name, q):
    n = name.lower()
    return 0 if n == q else 1 if n.startswith(q) else 2


def suggest(items, query, limit=LIMIT):
    """대소문자 무시 부분 일치. 완전 일치 → 접두 → 그 밖, 같은 단계는 이름 순(웹 useEditorContext.ts의 refOptions, 와카아카 6b28ef5).

    웹은 같은 단계를 `localeCompare`로 정렬한다. Python에는 ICU가 없어 (소문자, 원문) 순으로 근사한다.
    """
    q = js_trim(query).lower()
    hits = [w for w in items if q in w["name"].lower()]
    hits.sort(key=lambda w: (rank(w["name"], q), w["name"].lower(), w["name"]))
    return [{"id": w["id"], "name": w["name"], "fixed": w["fixed"]} for w in hits[:limit]]


def find(items, name):
    """이름(대소문자 무시)으로 ID. 없으면 None."""
    n = name.lower()
    for w in items:
        if w["name"].lower() == n:
            return w["id"]
    return None
