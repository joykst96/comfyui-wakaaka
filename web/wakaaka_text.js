// 노드 Wakaaka Text와 Wakaaka Text (Simple)의 화면 쪽 (DESIGN 8절 "노드 `Wakaaka Text`", "노드 `Wakaaka Text (Simple)`", "노드의 자동완성").
// - text 칸의 태그·와일드카드 자동완성. 뜨는 자리·넣는 표기는 웹과 같다(lib/textcore.js).
// - result는 읽기 전용, refs는 숨김, 노드 아래 정보 줄에 참조한 이름과 아직 ID를 모르는 이름.
// - 훅이 보낸 값(wakaaka.text)을 위젯에 반영한다.
// - Simple은 result와 refs를 둘 다 숨기고 정보 줄을 만들지 않는다. 자동완성과 refs 기록은 같다.
//
// 캔버스 렌더링과 Vue 노드 렌더링 모두에서 동작해야 한다(NOTES 2026-11-19 단계 0). 두 방식은 textarea가 다르다.
// - 캔버스: 위젯의 element가 그 textarea다.
// - Vue 노드: widget.element는 화면에 없고 Vue가 그린 textarea가 [data-node-id] 안의 [data-widget-name="text"] 안에 있다.
//   캔버스 배율을 CSS transform으로 그리므로 커서 좌표를 배율만큼 보정한다.
// 그래서 칸마다 이벤트를 거는 대신 문서 전체에서 받아 노드를 찾는다.
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import * as core from "./lib/textcore.js";

const SIMPLE = "WakaakaTextSimple";
const NODES = new Set(["WakaakaText", SIMPLE]);
const DEBOUNCE = 150;
const CATEGORY_COLORS = { 0: "#5aa2ff", 1: "#ff6b6b", 3: "#c58cff", 4: "#55d17a", 5: "#ffa24d" };

const widget = (node, name) => node?.widgets?.find((w) => w.name === name);
const setting = (id, fallback) => app.extensionManager?.setting?.get(id) ?? fallback;
const currentGraph = () => app.canvas?.graph ?? app.graph;

// ---------------------------------------------------------------- textarea → 노드

/** 이 textarea가 Wakaaka Text의 text 칸이면 그 노드 */
function ownerOf(ta) {
  if (!(ta instanceof HTMLTextAreaElement)) return null;
  const el = ta.closest?.("[data-node-id]");
  if (el) {
    if (ta.closest("[data-widget-name]")?.dataset.widgetName !== "text") return null;
    const node = currentGraph()?.getNodeById?.(Number(el.dataset.nodeId)) ?? currentGraph()?.getNodeById?.(el.dataset.nodeId);
    return NODES.has(node?.comfyClass) ? node : null;
  }
  for (const node of currentGraph()?._nodes ?? []) {
    if (!NODES.has(node.comfyClass)) continue;
    const w = widget(node, "text");
    if (w && (w.element === ta || w.inputEl === ta)) return node;
  }
  return null;
}

/** 실행 ID(서브그래프 안이면 "바깥:안")로 노드를 찾는다 */
function findNode(execId) {
  let graph = app.rootGraph ?? app.graph;
  let node = null;
  const parts = String(execId).split(":");
  for (let i = 0; i < parts.length; i++) {
    node = graph?.getNodeById?.(Number(parts[i])) ?? graph?.getNodeById?.(parts[i]);
    if (!node) return null;
    if (i < parts.length - 1) graph = node.subgraph;
  }
  return NODES.has(node?.comfyClass) ? node : null;
}

// ---------------------------------------------------------------- 정보 줄

function refreshInfo(node) {
  const info = node.wakaakaInfo;
  if (!info) return;
  const names = core.nameRefs(widget(node, "text")?.value ?? "");
  const refs = widget(node, "refs")?.value ?? "";
  const unknown = core.unresolvedNames(names, refs);
  info.refs.textContent = names.length ? "참조: " + names.join(", ") : "참조 없음 · ${ 를 치면 와일드카드를 찾습니다";
  info.refs.title = names.join("\n");
  const parts = [];
  if (unknown.length) parts.push("큐에 넣을 때 ID를 찾을 이름: " + unknown.join(", "));
  if (node.wakaakaSource === "cache") parts.push("서버에 연결하지 못해 캐시로 전개함");
  info.note.textContent = parts.join(" · ");
}

/** 노드마다 따로 늦춘다(전역 타이머 하나면 다른 노드의 갱신을 취소한다) */
function refreshInfoSoon(node) {
  clearTimeout(node.wakaakaInfoTimer);
  node.wakaakaInfoTimer = setTimeout(() => refreshInfo(node), 200);
}

/** 위젯을 숨긴다. 캔버스는 widget.hidden, Vue 노드는 options.hidden도 본다(프런트 1.57 deriveWidgetVisibility) */
function hide(w) {
  if (!w) return;
  w.hidden = true;
  w.options = { ...w.options, hidden: true };
}

/** Simple: text와 seed(와 생성 후 제어)만 보인다. result·refs는 값으로만 있어 훅이 채우고 PNG에 남는다 */
function setupSimple(node) {
  hide(widget(node, "result"));
  hide(widget(node, "refs"));
  // 노드를 만들 때 잰 크기에는 숨기기 전의 두 칸이 들어 있다. 높이만 다시 잰다(너비는 그대로)
  const [w, h] = node.computeSize();
  node.setSize?.([Math.max(node.size?.[0] ?? w, w), h]);
}

function setup(node, simple) {
  const text = widget(node, "text");
  if (text?.element) text.element.placeholder = "프롬프트. ${ 로 와일드카드, 쉼표 뒤에서 태그 자동완성. # 로 시작하는 줄은 주석";
  if (simple) return setupSimple(node);
  const result = widget(node, "result");
  const refs = widget(node, "refs");
  // result: 읽기 전용. 캔버스는 textarea의 readOnly, Vue 노드는 options.read_only를 본다(프런트 1.57 WidgetTextarea)
  if (result) {
    result.options = { ...result.options, read_only: true };
    if (result.element) {
      result.element.readOnly = true;
      result.element.style.opacity = "0.85";
      result.element.placeholder = "큐에 넣으면 전개 결과가 채워집니다";
    }
  }
  hide(refs);
  const el = document.createElement("div");
  el.style.cssText = "font: 12px sans-serif; line-height: 1.5; padding: 2px 4px; opacity: 0.8; overflow: hidden;";
  const refsLine = el.appendChild(document.createElement("div"));
  const note = el.appendChild(document.createElement("div"));
  refsLine.style.cssText = note.style.cssText = "white-space: nowrap; overflow: hidden; text-overflow: ellipsis;";
  const info = node.addDOMWidget("wakaaka_text_info", "wakaaka_text_info", el, {
    serialize: false,
    hideOnZoom: false,
    getValue: () => "",
    setValue: () => {},
    getMinHeight: () => 60,
    getMaxHeight: () => 60,
  });
  info.serialize = false;
  node.wakaakaInfo = { refs: refsLine, note };
  // 정보 줄을 text 바로 아래로
  const ws = node.widgets;
  ws.splice(ws.indexOf(info), 1);
  ws.splice(ws.indexOf(text) + 1, 0, info);
  for (const w of [text, refs]) {
    if (!w) continue;
    const cb = w.callback;
    w.callback = function (...args) {
      const r = cb?.apply(this, args);
      refreshInfoSoon(node);
      return r;
    };
  }
  refreshInfo(node);
}

// ---------------------------------------------------------------- 자동완성 팝업

const S = {
  ta: null, // 지금 보는 textarea
  node: null,
  comp: null, // core.completionAt 결과
  items: [], // {kind:"tag", tag} | {kind:"ref", w}
  sel: 0,
  status: "", // 목록 대신 보일 안내 한 줄
  seq: 0,
  timer: 0,
  composing: false,
  suppress: false, // 고른 직후의 input 이벤트는 다시 찾지 않는다
  pressed: null, // 목록에서 누른 항목 {item, comp}. 누르는 동안(mousedown~click)은 닫지 않는다
  popup: null,
};
window.__wakaakaText = S; // 시험용

function popupEl() {
  if (S.popup) return S.popup;
  const p = document.createElement("div");
  p.className = "wakaaka-ac";
  p.style.cssText = [
    "position: fixed", "z-index: 100000", "min-width: 260px", "max-width: 420px", "max-height: 320px", "overflow-y: auto",
    "background: var(--comfy-menu-bg, #202020)", "color: var(--input-text, #ddd)", "border: 1px solid var(--border-color, #555)",
    "border-radius: 6px", "box-shadow: 0 6px 18px rgba(0,0,0,.45)", "font: 13px/1.35 sans-serif", "padding: 3px 0",
  ].join(";");
  // 고르는 동안 textarea의 포커스를 지킨다. 한글 조합 중이면 기본 동작으로 조합을 끝내고(칸이 blur된다) click에서 고른다.
  // 누른 항목은 인덱스가 아니라 값으로 잡아 둔다: 그 사이 목록이 다시 그려져도 누른 것을 넣는다
  p.addEventListener("mousedown", (e) => {
    const row = e.target.closest?.("[data-i]");
    S.pressed = row ? { item: S.items[Number(row.dataset.i)], comp: S.comp } : null;
    if (!S.composing) e.preventDefault();
  });
  p.addEventListener("click", () => {
    const pressed = S.pressed;
    S.pressed = null;
    if (pressed?.item) pick(pressed.item, pressed.comp);
  });
  document.body.appendChild(p);
  S.popup = p;
  return p;
}

function close() {
  S.pressed = null;
  S.seq++;
  clearTimeout(S.timer);
  S.items = [];
  S.status = "";
  S.comp = null;
  if (S.popup) S.popup.style.display = "none";
}

const isOpen = () => S.popup?.style.display === "block";

function render() {
  const p = popupEl();
  p.replaceChildren();
  if (S.status) {
    const d = p.appendChild(document.createElement("div"));
    d.style.cssText = "padding: 4px 10px; opacity: .75; white-space: normal;";
    d.textContent = S.status;
  }
  S.items.forEach((it, i) => {
    const row = p.appendChild(document.createElement("div"));
    row.dataset.i = String(i);
    row.style.cssText = `padding: 3px 10px; cursor: pointer; ${i === S.sel ? "background: var(--comfy-input-bg, #3a3a3a);" : ""}`;
    const top = row.appendChild(document.createElement("div"));
    top.style.cssText = "display: flex; gap: 8px; align-items: baseline; white-space: nowrap;";
    const name = top.appendChild(document.createElement("span"));
    name.style.cssText = "overflow: hidden; text-overflow: ellipsis; flex: 1;";
    if (it.kind === "tag") {
      name.textContent = core.tagDisplayName(it.tag.name);
      name.style.color = CATEGORY_COLORS[it.tag.category] ?? "";
      const count = top.appendChild(document.createElement("span"));
      count.style.cssText = "opacity: .6; font-size: 11px;";
      count.textContent = core.formatPostCount(it.tag.postCount);
      const sum = core.tagSummary(it.tag);
      if (sum) {
        const sub = row.appendChild(document.createElement("div"));
        sub.style.cssText = "opacity: .6; font-size: 11px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;";
        sub.textContent = sum;
      }
    } else {
      if (it.w.fixed) {
        const b = top.appendChild(document.createElement("span"));
        b.style.cssText = "font-size: 10px; padding: 0 4px; border: 1px solid currentColor; border-radius: 3px; opacity: .8;";
        b.textContent = "고정";
        top.insertBefore(b, name);
      }
      name.textContent = it.w.name;
    }
  });
  p.style.display = S.items.length || S.status ? "block" : "none";
  if (p.style.display === "block") place(p);
  p.querySelector(`[data-i="${S.sel}"]`)?.scrollIntoView({ block: "nearest" });
}

/** 커서 좌표: 같은 글꼴·폭의 거울 div. Vue 노드의 확대 배율(CSS transform)은 화면 폭 / 레이아웃 폭으로 보정한다 */
function caretXY(ta, pos) {
  const cs = getComputedStyle(ta);
  const m = document.createElement("div");
  for (const k of ["fontFamily", "fontSize", "fontWeight", "fontStyle", "lineHeight", "letterSpacing", "tabSize", "paddingTop", "paddingRight", "paddingBottom", "paddingLeft", "borderTopWidth", "borderRightWidth", "borderBottomWidth", "borderLeftWidth", "boxSizing", "textTransform", "wordSpacing"]) m.style[k] = cs[k];
  m.style.width = ta.offsetWidth + "px";
  m.style.position = "absolute";
  m.style.visibility = "hidden";
  m.style.whiteSpace = "pre-wrap";
  m.style.overflowWrap = "break-word";
  m.style.top = "0";
  m.style.left = "-9999px";
  m.textContent = ta.value.slice(0, pos);
  const s = m.appendChild(document.createElement("span"));
  s.textContent = "\u200b";
  document.body.appendChild(m);
  const r = ta.getBoundingClientRect();
  const scale = ta.offsetWidth ? r.width / ta.offsetWidth : 1;
  const x = r.left + (s.offsetLeft - ta.scrollLeft) * scale;
  const y = r.top + (s.offsetTop - ta.scrollTop) * scale;
  const h = s.offsetHeight * scale;
  m.remove();
  return { x, y, h };
}

function place(p) {
  if (!S.ta || !S.comp) return;
  const { x, y, h } = caretXY(S.ta, S.comp.from);
  const w = p.offsetWidth;
  const ph = p.offsetHeight;
  const left = Math.max(4, Math.min(x, window.innerWidth - w - 4));
  const below = y + h + 2;
  p.style.left = left + "px";
  p.style.top = (below + ph > window.innerHeight - 4 && y - ph - 2 > 4 ? y - ph - 2 : below) + "px";
}

// ---------------------------------------------------------------- 후보 받기

let tagStatus = { at: 0, v: null };
async function tagIndexStatus() {
  if (Date.now() - tagStatus.at < 5000 && tagStatus.v) return tagStatus.v;
  const v = await api.fetchApi("/wakaaka/tags/status").then((r) => r.json()).catch(() => null);
  tagStatus = { at: Date.now(), v };
  return v;
}

const REF_STATUS = {
  no_user: "설정 → Wakaaka에서 유저 아이디를 넣으면 ${ 뒤에서 그 유저의 와일드카드를 찾습니다",
  bad_user: "유저 아이디 설정이 올바르지 않습니다(영문·숫자·_- 3~32자)",
  not_found: "설정한 유저 아이디가 와카아카에 없습니다",
  unavailable: "와카아카 서버에서 유저 목록을 받을 수 없습니다",
};

async function query() {
  const ta = S.ta;
  if (!ta || ta.selectionStart !== ta.selectionEnd) return close();
  const comp = core.completionAt(ta.value, ta.selectionStart);
  if (!comp || (comp.kind === "tag" && comp.query.trim() === "")) return close();
  const seq = ++S.seq;
  let items = [];
  let status = "";
  if (comp.kind === "tag") {
    const r = await api.fetchApi(`/wakaaka/tags/suggest?q=${encodeURIComponent(comp.query)}`).then((x) => x.json()).catch(() => null);
    items = (r?.tags ?? []).map((tag) => ({ kind: "tag", tag }));
    if (!items.length) {
      const st = await tagIndexStatus();
      if (st && st.state !== "ready") status = st.state === "downloading" ? "태그 목록을 받는 중입니다(처음 한 번, 약 30초)" : `태그 목록이 없습니다${st.error ? ": " + st.error : ""}`;
    }
  } else {
    const r = await api.fetchApi(`/wakaaka/wildcards/suggest?q=${encodeURIComponent(comp.query)}`).then((x) => x.json()).catch(() => null);
    items = (r?.wildcards ?? []).map((w) => ({ kind: "ref", w }));
    status = REF_STATUS[r?.state] ?? (r ? "" : "ComfyUI에 물어보지 못했습니다");
    if (r?.state === "stale" && items.length) status = "서버에 연결하지 못해 마지막으로 받은 목록입니다";
  }
  if (seq !== S.seq || ta !== S.ta) return; // 그 사이 다시 쳤다
  // 늦게 온 응답: 받는 동안 다른 곳을 눌렀거나 커서를 옮겼으면 그 자리의 목록이 아니다(검토 1번)
  if (!core.completionStillValid(comp, ta.value, ta.selectionStart, ta.selectionEnd)) return close();
  S.comp = comp;
  S.items = items;
  S.status = status;
  S.sel = 0;
  render();
}

function schedule() {
  clearTimeout(S.timer);
  S.timer = setTimeout(query, DEBOUNCE);
}

/** 고른 항목을 넣는다. 자리가 요청 때와 달라졌으면 넣지 않고 false(검토 1번) */
function pick(it = S.items[S.sel], comp = S.comp) {
  const { ta, node } = S;
  if (!it || !ta || !comp) return false;
  if (!core.completionStillValid(comp, ta.value, ta.selectionStart, ta.selectionEnd)) {
    close();
    return false;
  }
  const to = comp.to;
  let insert;
  if (it.kind === "tag") insert = core.tagCompletionText(it.tag, comp, setting("Wakaaka.ArtistAt", false) === true);
  else insert = "${" + it.w.name + "}";
  close();
  ta.focus();
  ta.setRangeText(insert, comp.from, to, "end");
  S.suppress = true;
  ta.dispatchEvent(new Event("input", { bubbles: true })); // 위젯 값(캔버스·Vue 모두)이 따라오게
  S.suppress = false;
  if (it.kind === "ref") {
    const refs = widget(node, "refs");
    if (refs) {
      refs.value = core.withRef(refs.value ?? "", it.w.name, it.w.id);
      refs.callback?.(refs.value);
    }
  }
  refreshInfo(node);
  node.setDirtyCanvas?.(true, true);
  return true;
}

// ---------------------------------------------------------------- 문서 전체 이벤트

document.addEventListener(
  "input",
  (e) => {
    const node = ownerOf(e.target);
    if (!node) return;
    refreshInfoSoon(node);
    if (S.suppress) return;
    S.seq++; // 나간 요청의 응답은 버린다(새로 친 글자로 다시 찾는다)
    S.ta = e.target;
    S.node = node;
    schedule();
  },
  true,
);

document.addEventListener(
  "keydown",
  (e) => {
    if (!S.ta || e.target !== S.ta || !isOpen()) return;
    if (e.isComposing || e.keyCode === 229) return; // 한글 조합 중의 키는 입력기 것이다
    const k = e.key;
    if (k === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      close();
      return;
    }
    if (["ArrowLeft", "ArrowRight", "Home", "End", "PageUp", "PageDown"].includes(k)) return close();
    if (!S.items.length || !["ArrowUp", "ArrowDown", "Enter", "Tab"].includes(k) || e.shiftKey || e.ctrlKey || e.altKey || e.metaKey) return;
    if (!core.completionStillValid(S.comp, S.ta.value, S.ta.selectionStart, S.ta.selectionEnd)) {
      // 목록이 지금 자리의 것이 아니다(더 쳤는데 새 목록이 아직 안 옴). 옛 1순위를 넣지 않는다
      close();
      if (k === "Enter" || k === "Tab") {
        e.preventDefault();
        e.stopPropagation();
        e.stopImmediatePropagation();
        query();
      }
      return;
    }
    e.preventDefault();
    e.stopPropagation();
    e.stopImmediatePropagation();
    if (k === "ArrowDown") S.sel = (S.sel + 1) % S.items.length;
    else if (k === "ArrowUp") S.sel = (S.sel - 1 + S.items.length) % S.items.length;
    else return pick();
    render();
  },
  true,
);

document.addEventListener("compositionstart", (e) => {
  if (e.target === S.ta) S.composing = true;
}, true);
document.addEventListener("compositionend", (e) => {
  if (e.target !== S.ta) return;
  S.composing = false;
  schedule();
}, true);

// 칸 안을 눌러 커서를 옮겼으면 그 자리의 목록이 아니므로 닫는다
document.addEventListener("click", (e) => {
  if (e.target !== S.ta || !isOpen() || !S.comp) return;
  if (!core.completionStillValid(S.comp, S.ta.value, S.ta.selectionStart, S.ta.selectionEnd)) close();
}, true);

// 칸 밖을 누르거나 어디든 스크롤하면 닫는다(목록 안 스크롤은 빼고)
document.addEventListener("pointerdown", (e) => {
  if (!isOpen() || e.target === S.ta || S.popup?.contains(e.target)) return;
  close();
}, true);
// 목록을 누른 채 밖에서 뗐으면(click이 없다) 누름을 풀고, 칸이 포커스를 잃었으면 닫는다
document.addEventListener("pointerup", (e) => {
  if (!S.pressed || S.popup?.contains(e.target)) return;
  S.pressed = null;
  if (document.activeElement !== S.ta) close();
}, true);
document.addEventListener("wheel", (e) => {
  if (isOpen() && !S.popup?.contains(e.target)) close();
}, { capture: true, passive: true });
document.addEventListener("scroll", (e) => {
  if (isOpen() && e.target !== S.popup && e.target !== S.ta) close();
}, true);
window.addEventListener("resize", () => isOpen() && close());
document.addEventListener("focusout", (e) => {
  if (e.target !== S.ta) return;
  setTimeout(() => {
    if (document.activeElement !== S.ta && !S.composing && !S.pressed) close();
  }, 150);
}, true);

// ---------------------------------------------------------------- 등록

app.registerExtension({
  name: "wakaaka.text",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (!NODES.has(nodeData.name)) return;
    const created = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function (...args) {
      const r = created?.apply(this, args);
      setup(this, nodeData.name === SIMPLE);
      return r;
    };
    const configured = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function (...args) {
      const r = configured?.apply(this, args);
      refreshInfo(this);
      return r;
    };
  },
  setup() {
    api.addEventListener("wakaaka.text", ({ detail }) => {
      const node = findNode(detail?.node);
      if (!node) return;
      const result = widget(node, "result");
      const refs = widget(node, "refs");
      if (result) result.value = detail.result;
      if (refs) refs.value = detail.refs;
      node.wakaakaSource = detail.source;
      refreshInfo(node);
      node.setDirtyCanvas?.(true, true);
    });
  },
});
