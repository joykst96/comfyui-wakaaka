"""와카아카 ComfyUI 노드 진입점. 훅·라우트·노드를 ComfyUI에 붙인다. 로직은 wakaaka_node 패키지에 있다."""

import asyncio
import logging
import os
import threading

import folder_paths
from aiohttp import web
from server import PromptServer

from .wakaaka_node import VERSION, hook, settings
from .wakaaka_node.client import ApiError, Client, NetworkError
from .wakaaka_node.cache import Cache
from .wakaaka_node.fill import Prefetcher, fill
from .wakaaka_node.ids import extract_id
from .wakaaka_node.tagindex import QueryError
from .wakaaka_node.tagupdate import TagIndex
from .wakaaka_node.node import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS, RUN_TOKEN, WakaakaWildcard

log = logging.getLogger("wakaaka")
WEB_DIRECTORY = "./web"


def _settings(user="default"):
    # 훅은 요청을 받지 않아 누가 큐에 넣었는지 모른다. 단일 사용자 기본값인 default의 설정을 쓴다.
    return settings.load(settings.settings_path(folder_paths.get_user_directory(), user))


def _data_root():
    try:
        return folder_paths.get_system_user_directory("wakaaka")  # user/__wakaaka: HTTP로 읽을 수 없는 시스템 폴더
    except AttributeError:  # 옛 ComfyUI
        return os.path.join(folder_paths.get_user_directory(), "__wakaaka")


def _cache_root():
    return os.path.join(_data_root(), "cache")


def _client():
    s = _settings()
    if s.url is None:
        raise NetworkError("서버 주소 설정이 올바르지 않음")
    return Client(s.url)


CACHE = Cache(_cache_root())
PREFETCH = Prefetcher(CACHE, _client)
# 태그 인덱스(DESIGN 8절): 로딩 때 백그라운드로 한 번 최신 확인. 실패해도 기존 인덱스를 쓰고 시작을 막지 않는다.
TAGS = TagIndex(os.path.join(_data_root(), "tags"))
threading.Thread(target=TAGS.refresh, name="wakaaka-tags", daemon=True).start()


def _fill_node(wildcard, seed, follow_latest, lock):
    return fill(wildcard, seed, follow_latest, lock, _settings(), cache=CACHE, prefetch=PREFETCH)


def _send(event, data, sid):
    srv = PromptServer.instance
    srv.send_sync(event, data, sid if sid is not None else srv.client_id)


def _on_prompt(json_data):
    return hook.on_prompt(json_data, _fill_node, _send, RUN_TOKEN)


WakaakaWildcard.fill_node = staticmethod(_fill_node)
WakaakaWildcard.send = staticmethod(_send)
PromptServer.instance.add_on_prompt_handler(_on_prompt)


@PromptServer.instance.routes.get("/wakaaka/meta")
async def _meta(request):
    """노드 표시용 이름·소유자·버전. 브라우저가 와카아카 서버를 직접 부르면 CORS에 막혀서 여기를 거친다."""
    wid = extract_id(request.query.get("id", ""))
    if wid is None:
        return web.json_response({"ok": False, "error": "ID를 읽을 수 없음"})
    try:
        user = PromptServer.instance.user_manager.get_request_user_id(request)
    except KeyError:
        user = "default"
    s = _settings(user)
    if s.url is None:
        return web.json_response({"ok": False, "id": wid, "error": "서버 주소 설정이 올바르지 않음"})
    try:
        m = await asyncio.get_running_loop().run_in_executor(None, Client(s.url).meta, wid)
    except NetworkError:
        cached = CACHE.get_latest(wid)
        why = f"서버에 연결할 수 없음, 캐시 v{cached['v']}로 전개함" if cached else "서버에 연결할 수 없음, 캐시 없음"
        return web.json_response({"ok": False, "id": wid, "error": why})
    except ApiError as e:
        return web.json_response({"ok": False, "id": wid, "error": "찾을 수 없음" if e.status == 404 else f"서버 오류 {e.status}"})
    return web.json_response({"ok": True, "id": wid, "name": m["name"], "owner": m["owner"], "version": m["version"]})


@PromptServer.instance.routes.get("/wakaaka/tags/suggest")
async def _tags_suggest(request):
    """태그 자동완성. 서버 /api/tags/suggest와 같은 규칙·응답(`{tags:[…]}`, 최대 20개). 검색은 스레드에서 한다."""
    if "q" not in request.query:
        return web.json_response({"error": {"code": "E_QUERY", "message": "query does not match the schema"}}, status=400)
    try:
        r = await asyncio.get_running_loop().run_in_executor(None, TAGS.suggest, request.query["q"])
    except QueryError as e:
        return web.json_response({"error": {"code": "E_QUERY", "message": str(e)}}, status=400)
    return web.json_response(r)


@PromptServer.instance.routes.get("/wakaaka/tags/status")
async def _tags_status(request):
    """태그 인덱스 상태: state(none·downloading·ready·error), job, 파일 이름, sha256, 행 수, 만든 시각, 마지막 오류."""
    return web.json_response(TAGS.status())


log.info("[wakaaka] comfy node %s loaded from %s, cache %s", VERSION, os.path.dirname(__file__), CACHE.root)

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
