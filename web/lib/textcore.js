// Wakaaka Text 자동완성의 순수 함수 (DOM·네트워크 없음). node --test로 웹과 같은 입력에 같은 출력임을 검사한다(tests/js).
//
// 와카아카 저장소(커밋 6b28ef5)에서 옮긴 것. 규칙을 바꿀 때는 원본과 함께 바꾼다.
// - packages/core/src/tagtext.ts: promptTag, escapeText
// - packages/core/src/parser.ts: isComment, classifyRefBody (참조 본문 판정, SPEC 2.3)
// - packages/core/src/cleanup.ts: isSpecWhitespace, trimSpecWhitespace
// - apps/web/src/editor/syntax.ts: tokenize, foreignTokens, openRefBefore
// - apps/web/src/editor/tags.ts: tagFragmentBefore, tagInsertText, tagDisplayName, formatPostCount, TAG_CATEGORY_NAMES
// - apps/web/src/editor/cm.ts: tagSummary (목록의 한 줄 설명)
// 그 아래 "노드 전용"은 여러 줄 칸에 맞춘 것이다(웹은 줄 하나가 항목 하나다).

// ---------------------------------------------------------------- core/cleanup.ts

export function isSpecWhitespace(code) {
  return code === 0x20 || code === 0x09 || code === 0x0a || code === 0x0d;
}

export function trimSpecWhitespace(s) {
  let a = 0;
  let b = s.length;
  while (a < b && isSpecWhitespace(s.charCodeAt(a))) a++;
  while (b > a && isSpecWhitespace(s.charCodeAt(b - 1))) b--;
  return s.slice(a, b);
}

// ---------------------------------------------------------------- core/tagtext.ts

/**
 * Danbooru 태그 이름 → 프롬프트 표기. 밑줄은 공백으로(영숫자 2글자 연속이 없는 이름은 그대로), 괄호는 `\(` `\)`로.
 */
export function promptTag(name) {
  const s = /[0-9A-Za-z]{2}/.test(name) ? name.replaceAll("_", " ") : name;
  return s.replaceAll("(", "\\(").replaceAll(")", "\\)");
}

/** 글자 그대로 나오도록 와카아카 문법 이스케이프(SPEC 2.2). inChoice면 `|`도. atStart면 맨 앞 `#`·`\#`도(SPEC 2.7) */
export function escapeText(s, inChoice = false, atStart = false) {
  let out = "";
  for (let i = 0; i < s.length; i++) {
    const c = s[i];
    const n = s[i + 1];
    if (atStart && i === 0 && c === "#") out += "\\#";
    else if (atStart && i === 0 && c === "\\" && n === "#") out += "\\\\";
    else if ("{}$".includes(c) || (inChoice && c === "|")) out += "\\" + c;
    else if (c === "\\" && (n === undefined || "{}$\\".includes(n) || (inChoice && n === "|"))) out += "\\\\";
    else out += c;
  }
  return out;
}

// ---------------------------------------------------------------- core/parser.ts

export const isComment = (text) => text.startsWith("#");

const STORED_REF = /^w:[0-9A-Za-z]{12}$/;

/** 참조 본문 판정 (SPEC 2.3 표의 순서) */
export function classifyRefBody(body, mode) {
  let blank = true;
  for (let i = 0; i < body.length; i++) if (!isSpecWhitespace(body.charCodeAt(i))) blank = false;
  if (blank) return { error: "E_REF_EMPTY" };
  if (body.includes("=")) return { error: "E_REF_RESERVED" };
  if (/[{$\\|]/.test(body)) return { error: "E_REF_INVALID" };
  if (body.startsWith("h:")) return { error: "E_REF_RESERVED" };
  if (STORED_REF.test(body)) return { kind: "w", id: body.slice(2) };
  if (body.includes(":")) return { error: "E_REF_INVALID" };
  if (mode === "stored") return { error: "E_REF_NAME" };
  return { kind: "name", name: trimSpecWhitespace(body) };
}

// ---------------------------------------------------------------- web/editor/syntax.ts

const ALT_WEIGHT = /[0-9.]+[*]/y;

/** 색칠용 토큰(위치순). 오류가 있어도 끝까지 훑는다(닫히지 않은 참조는 끝까지 badref) */
export function tokenize(text) {
  if (isComment(text)) return [{ type: "comment", from: 0, to: text.length }];
  const out = [];
  let depth = 0;
  let altStart = false;
  let i = 0;
  if (text.startsWith("\\#")) {
    out.push({ type: "escape", from: 0, to: 2 });
    i = 2;
  }
  for (; i < text.length; ) {
    if (altStart) {
      altStart = false;
      ALT_WEIGHT.lastIndex = i;
      const m = ALT_WEIGHT.exec(text);
      if (m) {
        out.push({ type: "weight", from: i, to: i + m[0].length });
        i += m[0].length;
        continue;
      }
    }
    const c = text[i];
    const n = text[i + 1];
    if (c === "\\" && n !== undefined && (n === "{" || n === "}" || n === "$" || n === "\\" || (depth > 0 && n === "|"))) {
      out.push({ type: "escape", from: i, to: i + 2 });
      i += 2;
    } else if (c === "$" && n === "{") {
      const close = text.indexOf("}", i + 2);
      if (close < 0) {
        out.push({ type: "badref", from: i, to: text.length });
        break;
      }
      const body = classifyRefBody(text.slice(i + 2, close), "edit");
      if ("error" in body) out.push({ type: "badref", from: i, to: close + 1 });
      else if (body.kind === "w") out.push({ type: "wref", from: i, to: close + 1, id: body.id });
      else out.push({ type: "nameref", from: i, to: close + 1, name: body.name });
      i = close + 1;
    } else if (c === "{") {
      depth++;
      out.push({ type: "brace", from: i, to: i + 1, depth });
      i++;
      altStart = true;
    } else if (c === "}") {
      out.push({ type: "brace", from: i, to: i + 1, depth: Math.max(depth, 1) });
      if (depth > 0) depth--;
      i++;
    } else if (c === "|" && depth > 0) {
      out.push({ type: "bar", from: i, to: i + 1, depth });
      i++;
      altStart = true;
    } else i++;
  }
  const ours = out.slice();
  const free = (f, t) => !ours.some((o) => o.from < t && f < o.to);
  for (const x of foreignTokens(text)) if (free(x.from, x.to)) out.push(x);
  return out.sort((a, b) => a.from - b.from || a.to - b.to);
}

/** 바깥 프롬프트 표기(LoRA, ComfyUI 강조, NAI 숫자 강조, BREAK). 색칠용이고 뜻은 바꾸지 않는다 */
export function foreignTokens(text) {
  const out = [];
  const add = (from, to, kind) => out.push({ type: "foreign", from, to, kind });
  const escaped = (j) => {
    let k = j - 1;
    while (k >= 0 && text[k] === "\\") k--;
    return (j - 1 - k) % 2 === 1;
  };
  for (const m of text.matchAll(/<(?:lora|lyco|hypernet|embedding):[^<>\n]*>/gi)) add(m.index, m.index + m[0].length, "lora");
  for (const m of text.matchAll(/:-?\d+(?:\.\d+)?\)/g)) {
    if (escaped(m.index + m[0].length - 1)) continue;
    let level = 0;
    for (let j = m.index - 1; j >= 0; j--) {
      const ch = text[j];
      if ((ch !== "(" && ch !== ")") || escaped(j)) continue;
      if (ch === ")") level++;
      else if (level === 0) {
        add(j, j + 1, "comfy");
        add(m.index, m.index + m[0].length, "comfy");
        break;
      } else level--;
    }
  }
  const openers = [];
  for (const m of text.matchAll(/(?<![\w.:])-?\d+(?:\.\d+)?::/g)) {
    openers.push([m.index, m.index + m[0].length]);
    add(m.index, m.index + m[0].length, "nai");
  }
  for (const m of text.matchAll(/::/g)) {
    if (openers.some(([f, t]) => f <= m.index && m.index < t)) continue;
    add(m.index, m.index + 2, "nai");
  }
  for (const m of text.matchAll(/\bBREAK\b/g)) add(m.index, m.index + 5, "break");
  return out;
}

/** 자동완성: 커서 앞이 열린 `${…`이면 그 시작과 입력한 글자 */
export function openRefBefore(textBefore) {
  const at = textBefore.lastIndexOf("${");
  if (at < 0) return null;
  const query = textBefore.slice(at + 2);
  if (query.includes("}")) return null;
  const toks = tokenize(textBefore);
  const last = toks[toks.length - 1];
  if (!last || last.from !== at || last.type !== "badref") return null;
  return { from: at, query };
}

// ---------------------------------------------------------------- web/editor/tags.ts

export const TAG_QUERY_MAX = 64;

/** Danbooru 기본 타입 → 이름 */
export const TAG_CATEGORY_NAMES = { 0: "general", 1: "artist", 3: "copyright", 4: "character", 5: "meta" };

const inToken = (toks, i) => toks.some((t) => t.from <= i && i < t.to);

/**
 * 커서 앞이 태그를 칠 자리이면 그 조각. 자리: 칸 맨 앞, 쉼표 뒤, 선택지 `{` `|`(와 보기 가중치 `3*`) 뒤.
 * 앞 공백과 ComfyUI 강조의 여는 `(`는 조각에 넣지 않는다. 조각에 와카아카 문법이 있으면 아니다.
 */
export function tagFragmentBefore(before) {
  if (openRefBefore(before)) return null;
  const toks = tokenize(before);
  let start = 0;
  let depth = 0;
  for (const t of toks) {
    if (t.type === "brace") {
      if (before[t.from] === "{") {
        depth++;
        start = t.to;
      } else depth = Math.max(0, depth - 1);
    } else if (t.type === "bar" || t.type === "weight") start = t.to;
  }
  for (let i = before.length - 1; i >= start; i--) {
    if (before[i] === "," && !inToken(toks, i)) {
      start = i + 1;
      break;
    }
  }
  let from = start;
  while (from < before.length && (before[from] === " " || before[from] === "\t" || before[from] === "(")) from++;
  const query = before.slice(from);
  if (query === "" || query.length > TAG_QUERY_MAX || /[\\{}$|]/.test(query)) return null;
  if (toks.some((t) => t.to > from)) return null;
  return { from, query, inChoice: depth > 0 };
}

/** 자동완성이 넣는 글자: 표기 규칙대로 바꾸고 와카아카 이스케이프. 뒤에 쉼표를 붙이지 않는다 */
export const tagInsertText = (name, inChoice, atStart = false) => escapeText(promptTag(name), inChoice, atStart);

/** 목록에 보이는 이름: 밑줄 규칙만(괄호 이스케이프 없이) */
export const tagDisplayName = (name) => promptTag(name).replaceAll("\\(", "(").replaceAll("\\)", ")");

export function formatPostCount(n) {
  if (n < 1000) return String(n);
  if (n < 1_000_000) return (n / 1000).toFixed(n < 10_000 ? 1 : 0).replace(/\.0$/, "") + "K";
  return (n / 1_000_000).toFixed(1).replace(/\.0$/, "") + "M";
}

const HANGUL = /[가-힣]/;

/** 목록의 한 줄 설명(cm.ts tagSummary): 한글이 있는 본문, 없으면 키워드 */
export function tagSummary(t) {
  return HANGUL.test(t.body) ? t.body : t.keywords.join(", ");
}

// ---------------------------------------------------------------- 노드 전용 (여러 줄 칸)

/** 줄 주석(DESIGN 8절, wakaaka_node/text.py strip_comment_lines와 같다): 첫 글자가 `#`인 줄을 지우고, 둘째 줄부터의 맨 앞 `\#`은 `#`로 */
export function stripCommentLines(text) {
  const kept = [];
  for (let line of text.split("\n")) {
    if (line.startsWith("#")) continue;
    if (kept.length > 0 && line.startsWith("\\#")) line = line.slice(1);
    kept.push(line);
  }
  return kept.join("\n");
}

/** 텍스트(주석 줄 뺀 것)가 끝났을 때 열려 있는 선택지 겹 수 */
function openDepth(text) {
  let depth = 0;
  for (const t of tokenize(text)) {
    if (t.type !== "brace") continue;
    depth = text[t.from] === "{" ? depth + 1 : Math.max(0, depth - 1);
  }
  return depth;
}

/**
 * 여러 줄 칸의 커서 자리 → 자동완성 종류와 바꿀 범위. 줄 하나를 웹의 칸 하나처럼 본다(줄 맨 앞이 "칸 맨 앞").
 * - 주석 줄(`#`로 시작)에서는 뜨지 않는다.
 * - `{`가 앞 줄에서 열렸으면 선택지 안이다(`|`를 이스케이프해 넣는다).
 * 반환: {kind:"ref", from, to, query} | {kind:"tag", from, to, query, inChoice, atStart} | null
 */
export function completionAt(text, cursor) {
  const lineStart = text.lastIndexOf("\n", cursor - 1) + 1;
  const before = text.slice(lineStart, cursor);
  if (isComment(text.slice(lineStart))) return null;
  const ref = openRefBefore(before);
  if (ref) return { kind: "ref", from: lineStart + ref.from, to: cursor, query: ref.query };
  const frag = tagFragmentBefore(before);
  if (!frag) return null;
  const inChoice = frag.inChoice || openDepth(stripCommentLines(text.slice(0, lineStart))) > 0;
  return { kind: "tag", from: lineStart + frag.from, to: cursor, query: frag.query, inChoice, atStart: frag.from === 0 };
}

/** 넣을 태그 글자. artistAt이면 작가 태그(분류 1) 앞에 `@`(DESIGN 8절 `Wakaaka.ArtistAt`) */
export function tagCompletionText(tag, c, artistAt) {
  const s = tagInsertText(tag.name, c.inChoice, c.atStart && !(artistAt && tag.category === 1));
  return artistAt && tag.category === 1 ? "@" + s : s;
}

/** 텍스트(주석 줄 제외)의 이름 참조들, 나오는 순서로 겹침 없이 */
export function nameRefs(text) {
  const out = [];
  for (const t of tokenize(stripCommentLines(text))) if (t.type === "nameref" && !out.includes(t.name)) out.push(t.name);
  return out;
}

/** refs 위젯 값(JSON) → {refs, lock}. 틀리면 빈 표 */
export function parseRefsValue(value) {
  try {
    const v = JSON.parse(value);
    if (v && typeof v === "object" && !Array.isArray(v)) {
      const refs = v.refs && typeof v.refs === "object" && !Array.isArray(v.refs) ? v.refs : {};
      const lock = v.lock && typeof v.lock === "object" && !Array.isArray(v.lock) ? v.lock : {};
      return { refs: { ...refs }, lock: { ...lock } };
    }
  } catch {}
  return { refs: {}, lock: {} };
}

/** 이름 → ID를 표에 적는다. 대소문자만 다른 옛 키는 지운다(훅은 대소문자를 무시하고 찾는다) */
export function withRef(value, name, id) {
  const v = parseRefsValue(value);
  for (const k of Object.keys(v.refs)) if (k.toLowerCase() === name.toLowerCase()) delete v.refs[k];
  v.refs[name] = id;
  return JSON.stringify(v);
}

/** 이름 중 표에 ID가 없는 것(큐에 넣을 때 유저 목록에서 찾을 것) */
export function unresolvedNames(names, value) {
  const known = new Set(Object.keys(parseRefsValue(value).refs).map((k) => k.toLowerCase()));
  return names.filter((n) => !known.has(n.toLowerCase()));
}

/** 두 자동완성 자리가 같은지(종류, 범위, 질의, 선택지 안, 줄 맨 앞) */
export function sameCompletion(a, b) {
  if (!a || !b) return false;
  return a.kind === b.kind && a.from === b.from && a.to === b.to && a.query === b.query && a.inChoice === b.inChoice && a.atStart === b.atStart;
}

/**
 * 요청할 때의 자리(comp)가 지금 칸의 값·커서에서도 그대로인지. 응답을 그리기 직전과 고르기 직전에 본다.
 * 그 사이 더 쳤거나(질의가 바뀜), 다른 곳을 눌렀거나(자리가 바뀜), 범위를 골랐으면 false다.
 */
export function completionStillValid(comp, text, selStart, selEnd, enabled = ALL_ON) {
  if (selStart !== selEnd) return false;
  return sameCompletion(comp, completionFor(text, selStart, enabled));
}

const ALL_ON = Object.freeze({ tag: true, wildcard: true });

/** 이 종류의 자동완성이 켜져 있는지(설정 Wakaaka.TagAutocomplete, Wakaaka.WildcardAutocomplete) */
export function kindEnabled(kind, enabled = ALL_ON) {
  return kind === "tag" ? enabled.tag !== false : kind === "ref" ? enabled.wildcard !== false : false;
}

/**
 * 켜진 종류만 남긴 completionAt (DESIGN 8절 "켜고 끄기", 노드 규칙이라 웹 원본에는 없다).
 * 꺼진 종류의 자리면 null이다. `${` 뒤에서 와일드카드가 꺼져 있으면 태그로 바꿔 찾지 않는다(그 자리는 null).
 * 둘 다 꺼져 있으면 언제나 null.
 */
export function completionFor(text, cursor, enabled = ALL_ON) {
  if (!kindEnabled("tag", enabled) && !kindEnabled("ref", enabled)) return null;
  const c = completionAt(text, cursor);
  return c && kindEnabled(c.kind, enabled) ? c : null;
}
