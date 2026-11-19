"""노드 `Wakaaka Wildcard`, `Wakaaka Text`, `Wakaaka Text (Simple)` (DESIGN 8절). ComfyUI에 묶이는 함수(채우기, send_sync)는 __init__.py가 넣어 준다."""

import secrets

from .fill import FillError
from .hook import NODE_TYPE, TEXT_SIMPLE_TYPE, TEXT_TYPE, publish, publish_text
from .ids import parse_lock_value
from .text import parse_refs_value

SEED_MAX = (1 << 64) - 1
# 이 프로세스에서 훅이 채운 값인지 알아보는 표시. prompt의 lock에만 들어가고 워크플로·화면에는 남지 않는다.
RUN_TOKEN = secrets.token_hex(8)


class WakaakaWildcard:
    fill_node = None  # (wildcard, seed, follow_latest, lock) -> fill.Result
    send = None  # (event, data, sid)

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "wildcard": ("STRING", {"default": "", "multiline": False, "tooltip": "와일드카드 ID(12자). 웹 주소를 붙여 넣어도 된다."}),
                "seed": ("INT", {"default": 0, "min": 0, "max": SEED_MAX, "control_after_generate": True}),
                "follow_latest": ("BOOLEAN", {"default": True, "tooltip": "켜면 큐에 넣을 때마다 최신 버전으로 받는다. 끄면 저장된 lock을 그대로 쓴다."}),
            },
            "optional": {
                "text": ("STRING", {"default": "", "multiline": True, "tooltip": "큐에 넣을 때 채워지는 결과(읽기 전용)."}),
                "lock": ("STRING", {"default": "", "multiline": False, "tooltip": "큐에 넣을 때 고정한 버전들."}),
            },
            "hidden": {"unique_id": "UNIQUE_ID", "extra_pnginfo": "EXTRA_PNGINFO"},
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("text",)
    FUNCTION = "run"
    CATEGORY = "wakaaka"
    DESCRIPTION = "와카아카 와일드카드를 ID와 seed로 전개한다. 큐에 넣을 때 결과와 lock이 채워져 이미지에 남는다."

    @classmethod
    def IS_CHANGED(cls, lock="", **_):
        lv = parse_lock_value(lock)
        if lv is not None and lv.get("run") == RUN_TOKEN:
            return ""  # 훅이 채운 값이면 입력(text, lock)이 그대로 캐시 키다
        return float("nan")  # 실행할 때 채우는 경우: 최신이 바뀌었을 수 있으므로 매번 실행

    def run(self, wildcard, seed, follow_latest, text="", lock="", unique_id=None, extra_pnginfo=None):
        lv = parse_lock_value(lock)
        if lv is not None and "error" in lv:
            raise RuntimeError(f"[와카아카] {lv['error']}")
        if lv is not None and lv.get("run") == RUN_TOKEN:
            return (text,)
        # 훅이 채우지 못한 경우(입력이 링크였음): 지금 채우고 PNG 워크플로와 화면에도 반영한다
        try:
            r = type(self).fill_node(wildcard, seed, follow_latest, lock)
        except FillError as e:
            raise RuntimeError(f"[와카아카] {e}") from None
        workflow = extra_pnginfo.get("workflow") if isinstance(extra_pnginfo, dict) else None
        publish(unique_id, r, None, workflow, type(self).send, None, RUN_TOKEN)
        return (r.text,)


class WakaakaText:
    fill_text = None  # (text, seed, refs) -> text.TextResult
    send = None  # (event, data, sid)

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "text": ("STRING", {"default": "", "multiline": True, "dynamicPrompts": False,
                                    "tooltip": "프롬프트. 와카아카 문법(${이름}, {a|b}, {2*a|b}, 이스케이프)을 쓴다. 첫 글자가 #인 줄은 주석."}),
                "seed": ("INT", {"default": 0, "min": 0, "max": SEED_MAX, "control_after_generate": True}),
            },
            "optional": {
                "result": ("STRING", {"default": "", "multiline": True, "tooltip": "큐에 넣을 때 채워지는 결과(읽기 전용)."}),
                "refs": ("STRING", {"default": "", "multiline": False, "tooltip": "참조한 이름 → ID와 그때의 lock."}),
            },
            "hidden": {"unique_id": "UNIQUE_ID", "extra_pnginfo": "EXTRA_PNGINFO"},
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("text",)
    FUNCTION = "run"
    CATEGORY = "wakaaka"
    DESCRIPTION = "와카아카 문법을 쓰는 여러 줄 프롬프트. 태그와 와일드카드 자동완성이 붙고, 큐에 넣을 때 전개 결과와 참조 ID가 채워져 이미지에 남는다."

    @classmethod
    def IS_CHANGED(cls, refs="", **_):
        rv = parse_refs_value(refs)
        if rv.get("run") == RUN_TOKEN:
            return ""  # 훅이 채운 값이면 입력(result, refs)이 그대로 캐시 키다
        return float("nan")

    def run(self, text, seed, result="", refs="", unique_id=None, extra_pnginfo=None):
        rv = parse_refs_value(refs)
        if "error" in rv:
            raise RuntimeError(f"[와카아카] {rv['error']}")
        if rv.get("run") == RUN_TOKEN:
            return (result,)
        # 훅이 채우지 못한 경우(입력이 링크였음): 지금 채우고 PNG 워크플로와 화면에도 반영한다
        try:
            r = type(self).fill_text(text, seed, refs)
        except FillError as e:
            raise RuntimeError(f"[와카아카] {e}") from None
        workflow = extra_pnginfo.get("workflow") if isinstance(extra_pnginfo, dict) else None
        publish_text(unique_id, r, None, workflow, type(self).send, None, RUN_TOKEN)
        return (r.text,)


class WakaakaTextSimple(WakaakaText):
    """`Wakaaka Text`와 같은 일을 하고 화면에는 text와 seed만 보인다(DESIGN 8절 "노드 `Wakaaka Text (Simple)`").

    전개·이름 해석·기록·IS_CHANGED·run은 부모 그대로다(fill_text·send도 부모 클래스 속성을 쓴다).
    result·refs는 위젯 값으로 남아 훅이 채우고 PNG에 기록된다. 화면에서 숨기는 것은 web/wakaaka_text.js.
    result를 한 줄 위젯으로 두는 것은 숨기기 위해서다: 여러 줄 위젯은 textarea(DOM 위젯)라서 숨김 처리가 렌더링마다 다르다.
    값은 여러 줄이어도 그대로 저장된다.
    """

    @classmethod
    def INPUT_TYPES(cls):
        t = super().INPUT_TYPES()
        t["optional"]["result"] = ("STRING", {"default": "", "multiline": False, "tooltip": "큐에 넣을 때 채워지는 결과(화면에서는 숨김)."})
        return t

    DESCRIPTION = "Wakaaka Text와 같지만 텍스트 칸과 seed만 보인다. 결과와 참조 ID는 숨긴 채 이미지에 남는다."


NODE_CLASS_MAPPINGS = {NODE_TYPE: WakaakaWildcard, TEXT_TYPE: WakaakaText, TEXT_SIMPLE_TYPE: WakaakaTextSimple}
NODE_DISPLAY_NAME_MAPPINGS = {NODE_TYPE: "Wakaaka Wildcard", TEXT_TYPE: "Wakaaka Text", TEXT_SIMPLE_TYPE: "Wakaaka Text (Simple)"}
