"""태그 인덱스 받기·검증·교체 (DESIGN 8절 "노드의 태그 인덱스").

ComfyUI가 노드를 로딩할 때 백그라운드 스레드에서 refresh()를 한 번 부른다.
1. Hugging Face 파일 목록에서 이름이 `danbooru-tags-kr-YYYY-MM-DD.csv`인 것 중 날짜가 가장 늦은 것을 고른다. SHA-256은 `lfs.oid`.
2. 지금 인덱스의 sha256과 같으면 끝. 다르면 받아서 받은 바이트의 SHA-256과 크기를 확인한다.
3. 임시 파일에 인덱스를 만들고 형식 검증 관문(check_stats)을 통과할 때만 `tags-<sha256 앞 16자>.sqlite`로 바꿔 넣는다.
4. 받은 CSV는 지운다. 데이터셋의 LICENSE를 옆에 둔다.
무엇이 실패하든 기존 인덱스를 그대로 쓰고 ComfyUI 시작을 막지 않는다. 형식 검증에 떨어진 파일은 sha256을 적어 두고 다시 받지 않는다.

폴더 안의 `tags-*.sqlite`는 다 만든 뒤에만 그 이름을 얻으므로 늘 완전하다. 여러 개면 가장 늦게 만든 것을 쓰고 나머지는 지운다.
"""

import csv
import hashlib
import http.client
import json
import logging
import os
import re
import secrets
import socket
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import quote, urlsplit

from . import USER_AGENT, tagcsv, tagindex

log = logging.getLogger("wakaaka")

HF_BASE = "https://huggingface.co"
REPO = "mengod-hf/danbooru-tags-kr"
CSV_NAME = re.compile(r"danbooru-tags-kr-(\d{4})-(\d{2})-(\d{2})\.csv")
LICENSE_NAME = "LICENSE"
LICENSE_FILE = "LICENSE-danbooru-tags-kr"
INDEX_NAME = re.compile(r"tags-[0-9a-f]{16}\.sqlite")
REJECTED_FILE = "rejected.json"
SHA256 = re.compile(r"[0-9a-f]{64}")
SHA1 = re.compile(r"[0-9a-f]{40}")

TIMEOUT = 30.0  # 소켓 동작 하나의 시간 제한(초)
MAX_CSV = 256 * 1024 * 1024
MAX_TREE = 1024 * 1024
MAX_LICENSE = 256 * 1024
CHUNK = 1024 * 1024
STALE_TMP = 3600  # 이보다 오래된 임시 파일은 끊긴 갱신의 찌꺼기로 보고 지운다

# 형식 검증 관문. 근거는 NOTES 2026-11-19: 11-04(150,993행)와 11-10(152,801행) 모두 4열 100%, 틀린 행 0,
# 키워드 표시 인식 100%, 키워드가 하나 이상인 행 100%와 99.98%.
MIN_ROWS = 100_000  # 알려진 가장 작은 파일의 약 2/3
MAX_BAD_RATIO = 0.001  # 열 수·숫자·이름·금지 문자·중복으로 건너뛴 행
MIN_MARK_RATIO = 0.95  # 키워드 표시(`키워드:`·`<키워드>`)를 찾은 행. 표기가 또 바뀌면 0 근처로 떨어진다
MIN_KEYWORD_RATIO = 0.95  # 키워드가 하나 이상인 행
MIN_KEEP_RATIO = 0.8  # 지금 인덱스 행 수에 대한 비율. 잘린 파일을 막는다


class UpdateError(Exception):
    pass


class Rejected(UpdateError):
    """형식 검증에 떨어짐. 같은 sha256은 다시 받지 않는다."""


def check_stats(stats, current_rows=None):
    """형식 검증. 통과하면 None, 아니면 사유 문자열."""
    rows, lines = stats.rows, max(stats.lines, 1)
    if rows < MIN_ROWS:
        return f"rows {rows} < {MIN_ROWS}"
    if stats.bad_rows / lines > MAX_BAD_RATIO:
        return f"bad rows {stats.bad_rows}/{lines} {stats.bad} {stats.examples}"
    marked = stats.forms["old"] + stats.forms["new"]
    if marked / rows < MIN_MARK_RATIO:
        return f"keyword mark found in {marked}/{rows} rows"
    if stats.with_keywords / rows < MIN_KEYWORD_RATIO:
        return f"keywords in {stats.with_keywords}/{rows} rows"
    if current_rows and rows < current_rows * MIN_KEEP_RATIO:
        return f"rows {rows} < {MIN_KEEP_RATIO} x current {current_rows}"
    return None


def pick_latest(tree):
    """파일 목록 API 응답 → (CSV 항목, LICENSE 항목 또는 None). 맞는 CSV가 없으면 UpdateError."""
    if not isinstance(tree, list):
        raise UpdateError("file list is not a list")
    best, lic = None, None
    for e in tree:
        if not isinstance(e, dict) or e.get("type") != "file" or not isinstance(e.get("path"), str):
            continue
        path = e["path"]
        if path == LICENSE_NAME:
            lic = e
            continue
        m = CSV_NAME.fullmatch(path)
        if not m:
            continue
        lfs = e.get("lfs")
        sha = lfs.get("oid") if isinstance(lfs, dict) else None
        size = lfs.get("size") if isinstance(lfs, dict) else None
        if not isinstance(sha, str) or not SHA256.fullmatch(sha) or not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= MAX_CSV:
            continue
        date = m.groups()
        if best is None or date > best[0]:
            best = (date, {"file": path, "sha256": sha, "size": size})
    if best is None:
        raise UpdateError("no danbooru-tags-kr-YYYY-MM-DD.csv in the file list")
    return best[1], lic


def git_blob_sha1(data):
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


class _NoDowngrade(urllib.request.HTTPRedirectHandler):
    """https에서 http로 넘어가는 리다이렉트를 막는다(받은 파일은 해시로 확인하지만 목록 응답은 아니다)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlsplit(req.full_url).scheme == "https" and urlsplit(newurl).scheme != "https":
            raise urllib.error.HTTPError(newurl, code, "redirect to non-https refused", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_NoDowngrade)


class TagIndex:
    """인덱스 폴더 하나. suggest·status는 어느 스레드에서 불러도 된다."""

    def __init__(self, root, base_url=HF_BASE, repo=REPO, timeout=TIMEOUT):
        self.root = root
        self.base = base_url.rstrip("/")
        self.repo = repo
        self.timeout = timeout
        self._lock = threading.Lock()
        self._refresh_lock = threading.Lock()
        self._path = None
        self._meta = None
        self._job = "idle"  # idle, checking, downloading, building
        self._error = None
        self._checked_at = None
        self._load()

    # ------------------------------------------------------------ 읽기

    def _load(self):
        """폴더에서 쓸 인덱스를 고른다. 나머지 인덱스와 남은 임시 파일은 지운다(열려 있으면 다음 기회에)."""
        found = []
        try:
            names = os.listdir(self.root)
        except OSError:
            names = []
        for name in names:
            p = os.path.join(self.root, name)
            if name.startswith("tmp-"):
                if _age(p) > STALE_TMP:  # 다른 ComfyUI가 지금 만드는 중일 수 있다
                    _remove(p)
            elif INDEX_NAME.fullmatch(name):
                meta = tagindex.read_meta(p)
                if meta is None:
                    _remove(p)
                else:
                    found.append((str(meta.get("builtAt", "")), name, meta))
        found.sort()
        for _, name, _ in found[:-1]:
            _remove(os.path.join(self.root, name))
        with self._lock:
            if found:
                self._path, self._meta = os.path.join(self.root, found[-1][1]), found[-1][2]
            else:
                self._path, self._meta = None, None

    def suggest(self, q):
        """서버 /api/tags/suggest와 같은 결과. 인덱스가 없으면 빈 목록. 잘못된 질의는 tagindex.QueryError."""
        q = tagindex.normalize_query(q)  # 인덱스가 없어도 잘못된 질의는 400으로
        with self._lock:
            path = self._path
        if path is None:
            return {"tags": []}
        con = tagindex._open(path)
        try:
            return tagindex.suggest(con, q)
        finally:
            con.close()

    def status(self):
        with self._lock:
            meta, job, error = self._meta, self._job, self._error
            out = {
                "state": "ready" if meta else ("downloading" if job != "idle" else ("error" if error else "none")),
                "job": job,
                "error": error,
                "checkedAt": self._checked_at,
            }
        if meta:
            out.update({k: meta.get(k) for k in ("file", "sha256", "size", "rows", "builtAt")})
        return out

    # ------------------------------------------------------------ 갱신

    def refresh(self):
        """최신 확인과 필요하면 교체. 결과를 한 줄로 돌려주고 로그에 남긴다. 예외를 올리지 않는다."""
        if not self._refresh_lock.acquire(blocking=False):
            return "already running"
        try:
            msg = self._refresh()
            with self._lock:
                self._error = None
            log.info("[wakaaka] tag index: %s", msg)
            return msg
        except Exception as e:  # 네트워크, 형식, 디스크 … 무엇이든 기존 인덱스를 쓴다
            why = f"{type(e).__name__}: {e}" if not isinstance(e, UpdateError) else str(e)
            with self._lock:
                self._error = why
                current = self._meta
            tail = f"; using {current['file']} ({current['rows']} rows)" if current else "; no index"
            log.warning("[wakaaka] tag index update failed: %s%s", why, tail)
            return "failed: " + why
        finally:
            with self._lock:
                self._job = "idle"
                self._checked_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            self._refresh_lock.release()

    def _set_job(self, job):
        with self._lock:
            self._job = job

    def _refresh(self):
        self._set_job("checking")
        os.makedirs(self.root, exist_ok=True)
        tree = self._get_json(f"/api/datasets/{self.repo}/tree/main")
        latest, lic = pick_latest(tree)
        with self._lock:
            meta = self._meta
        if meta and meta.get("sha256") == latest["sha256"]:
            self._ensure_license(lic)
            return f"up to date ({meta['file']}, {meta['rows']} rows)"
        rejected = self._read_rejected()
        if rejected.get("sha256") == latest["sha256"]:
            raise UpdateError(f"{latest['file']} was rejected before ({rejected.get('reason')})")

        tag = secrets.token_hex(6)
        csv_tmp = os.path.join(self.root, f"tmp-{tag}.csv")
        db_tmp = os.path.join(self.root, f"tmp-{tag}.sqlite")
        try:
            self._set_job("downloading")
            t0 = time.perf_counter()
            self._download(latest, csv_tmp)
            t1 = time.perf_counter()
            license_bytes = self._fetch_license(lic)
            self._set_job("building")
            try:
                stats = tagindex.build(csv_tmp, db_tmp, latest)
            except (UnicodeDecodeError, csv.Error) as e:  # 파일 전체가 의심스럽다
                raise Rejected(f"cannot read {latest['file']}: {type(e).__name__}: {e}") from None
            t2 = time.perf_counter()
            why = check_stats(stats, meta["rows"] if meta else None)
            if why is not None:
                raise Rejected(f"{latest['file']} failed the format check: {why}")
            final = os.path.join(self.root, f"tags-{latest['sha256'][:16]}.sqlite")
            try:
                os.replace(db_tmp, final)
            except PermissionError:  # 다른 ComfyUI가 같은 파일을 만들어 열고 있다
                if (tagindex.read_meta(final) or {}).get("sha256") != latest["sha256"]:
                    raise
            self._write_license(license_bytes)
            new_meta = tagindex.read_meta(final)
            if new_meta is None:
                raise UpdateError("new index cannot be read")
            with self._lock:
                old = self._path
                self._path, self._meta = final, new_meta
            if old and os.path.abspath(old) != os.path.abspath(final):
                _remove(old)  # 질의가 열고 있으면 실패한다. 다음 로딩 때 지운다
            _remove(os.path.join(self.root, REJECTED_FILE))
            size = os.path.getsize(final) / 1e6
            return (f"{'updated to' if meta else 'built'} {latest['file']} ({stats.rows} rows, "
                    f"download {t1 - t0:.1f}s, build {t2 - t1:.1f}s, {size:.0f}MB)")
        except Rejected as e:
            self._write_rejected(latest["sha256"], str(e))
            raise
        finally:
            _remove(csv_tmp)
            _remove(db_tmp)

    # ------------------------------------------------------------ HTTP

    def _open_url(self, path):
        req = urllib.request.Request(self.base + path, headers={"User-Agent": USER_AGENT})
        try:
            return _opener.open(req, timeout=self.timeout)
        except urllib.error.HTTPError as e:
            raise UpdateError(f"GET {path}: HTTP {e.code}") from None
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as e:
            raise UpdateError(f"GET {path}: {getattr(e, 'reason', e)}") from None

    def _read_all(self, path, limit):
        with self._open_url(path) as r:
            data = r.read(limit + 1)
        if len(data) > limit:
            raise UpdateError(f"GET {path}: response too large")
        return data

    def _get_json(self, path):
        raw = self._read_all(path, MAX_TREE)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError) as e:
            raise UpdateError(f"GET {path}: bad JSON: {e}") from None

    def _download(self, entry, dest):
        path = f"/datasets/{self.repo}/resolve/main/{quote(entry['file'])}"
        h, n = hashlib.sha256(), 0
        try:
            with self._open_url(path) as r, open(dest, "wb") as f:
                while True:
                    chunk = r.read(CHUNK)
                    if not chunk:
                        break
                    n += len(chunk)
                    if n > entry["size"]:
                        raise UpdateError(f"{entry['file']}: more than {entry['size']} bytes")
                    h.update(chunk)
                    f.write(chunk)
        except (socket.timeout, TimeoutError, ConnectionError, http.client.HTTPException) as e:
            raise UpdateError(f"{entry['file']}: download interrupted: {type(e).__name__}: {e}") from None
        if n != entry["size"]:
            raise UpdateError(f"{entry['file']}: got {n} bytes, expected {entry['size']}")
        if h.hexdigest() != entry["sha256"]:
            raise UpdateError(f"{entry['file']}: sha256 mismatch")

    # ------------------------------------------------------------ LICENSE, 떨어진 파일 기록

    def _fetch_license(self, lic):
        if lic is None:
            raise UpdateError("no LICENSE in the file list")
        data = self._read_all(f"/datasets/{self.repo}/resolve/main/{LICENSE_NAME}", MAX_LICENSE)
        oid = lic.get("oid")
        if isinstance(oid, str) and SHA1.fullmatch(oid) and git_blob_sha1(data) != oid:
            raise UpdateError("LICENSE does not match the file list")
        return data

    def _write_license(self, data):
        _atomic_write(os.path.join(self.root, LICENSE_FILE), data)

    def _ensure_license(self, lic):
        if not os.path.isfile(os.path.join(self.root, LICENSE_FILE)):
            self._write_license(self._fetch_license(lic))

    def _read_rejected(self):
        try:
            with open(os.path.join(self.root, REJECTED_FILE), encoding="utf-8") as f:
                v = json.load(f)
            return v if isinstance(v, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write_rejected(self, sha256, reason):
        body = json.dumps({"sha256": sha256, "reason": reason[:2000], "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}, ensure_ascii=False)
        try:
            _atomic_write(os.path.join(self.root, REJECTED_FILE), body.encode("utf-8"))
        except OSError:
            pass


def _atomic_write(path, data):
    tmp = f"{path}.{secrets.token_hex(4)}.part"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def _age(path):
    try:
        return time.time() - os.path.getmtime(path)
    except OSError:
        return 0


def _remove(path):
    try:
        os.unlink(path)
    except OSError:
        pass
