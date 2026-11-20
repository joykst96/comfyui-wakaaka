// web/lib/textcore.js 검사. 사례는 와카아카 저장소(커밋 6b28ef5)의 vitest에서 옮겼다:
// apps/web/test/tags.test.ts, apps/web/test/syntax.test.ts, apps/web/test/comment.test.ts, packages/core/test/tagtext.test.ts.
// 노드 전용(여러 줄 칸) 사례는 아래쪽에 따로 둔다.
//
//     node --test tests/js

import assert from "node:assert/strict";
import { describe, test } from "node:test";
import {
  completionAt,
  escapeText,
  formatPostCount,
  nameRefs,
  openRefBefore,
  parseRefsValue,
  promptTag,
  stripCommentLines,
  tagCompletionText,
  tagDisplayName,
  tagFragmentBefore,
  tagInsertText,
  tagSummary,
  tokenize,
  unresolvedNames,
  withRef,
} from "../../web/lib/textcore.js";

// ---------------------------------------------------------------- apps/web/test/tags.test.ts

describe("tagFragmentBefore: 자동완성이 뜨는 자리", () => {
  const ok = [
    ["lon", { from: 0, query: "lon", inChoice: false }],
    ["1girl, lon", { from: 7, query: "lon", inChoice: false }],
    ["1girl,lon", { from: 6, query: "lon", inChoice: false }],
    ["1girl,  long h", { from: 8, query: "long h", inChoice: false }],
    ["{red|lo", { from: 5, query: "lo", inChoice: true }],
    ["{lo", { from: 1, query: "lo", inChoice: true }],
    ["{3*lo", { from: 3, query: "lo", inChoice: true }],
    ["{a|{b|c}|lo", { from: 9, query: "lo", inChoice: true }],
    ["{a|b}, lo", { from: 7, query: "lo", inChoice: false }],
    ["a, (long", { from: 4, query: "long", inChoice: false }],
    ["a, ((long", { from: 5, query: "long", inChoice: false }],
    ["1girl, 긴머", { from: 7, query: "긴머", inChoice: false }],
    ["${w:AAAAAAAAAAAA}, lo", { from: 19, query: "lo", inChoice: false }],
    ["<lora:x,y:0.8>, lo", { from: 16, query: "lo", inChoice: false }],
  ];
  for (const [before, expected] of ok) test(JSON.stringify(before), () => assert.deepEqual(tagFragmentBefore(before), expected));
  const no = ["", "1girl, ", "a, ", "${hair", "{a|b} hai", "a, \\{x", "a, x\\", "a, $x", "<lora:x,y> lo", "a, " + "x".repeat(65)];
  for (const before of no) test(`${JSON.stringify(before)} → 자리 아님`, () => assert.equal(tagFragmentBefore(before), null));
});

describe("넣는 표기 (core tagtext 확정 규칙)", () => {
  test("밑줄 → 공백, 괄호 이스케이프, 이모티콘은 그대로, 선택지 안이면 | 이스케이프", () => {
    assert.equal(tagInsertText("long_hair", false), "long hair");
    assert.equal(tagInsertText("hatsune_miku_(cosplay)", false), "hatsune miku \\(cosplay\\)");
    assert.equal(tagInsertText("asuna_(bunny)_(blue_archive)", true), "asuna \\(bunny\\) \\(blue archive\\)");
    assert.equal(tagInsertText("^_^", false), "^_^");
    assert.equal(tagInsertText(":|", false), ":|");
    assert.equal(tagInsertText(":|", true), ":\\|");
    assert.equal(tagInsertText("\\m/", false), "\\m/");
  });
  test("목록 표시는 괄호 이스케이프 없이", () => {
    assert.equal(tagDisplayName("hatsune_miku_(cosplay)"), "hatsune miku (cosplay)");
    assert.equal(tagDisplayName("^_^"), "^_^");
  });
  for (const [n, s] of [[50, "50"], [999, "999"], [1000, "1K"], [1300, "1.3K"], [131891, "132K"], [5589043, "5.6M"], [7_000_000, "7M"]]) {
    test(`포스트 수 ${n} → ${s}`, () => assert.equal(formatPostCount(n), s));
  }
});

// ---------------------------------------------------------------- packages/core/test/tagtext.test.ts

describe("promptTag", () => {
  const cases = [
    ["long_hair", "long hair"],
    ["hatsune_miku_(cosplay)", "hatsune miku \\(cosplay\\)"],
    ["^_^", "^_^"],
    ["o_o", "o_o"],
    ["<|>_<|>", "<|>_<|>"],
    ["\\m/", "\\m/"],
    [":d", ":d"],
    ["a_b", "a_b"],
    ["asuna_(bunny)_(blue_archive)", "asuna \\(bunny\\) \\(blue archive\\)"],
    ["aikatsu!_(series)", "aikatsu! \\(series\\)"],
    ["mr._c.b._(umamusume)", "mr. c.b. \\(umamusume\\)"],
    ["zero(z)_(mega_man)", "zero\\(z\\) \\(mega man\\)"],
    ["100%_orange_juice", "100% orange juice"],
    ["@_@", "@_@"],
    ["<o>_<o>", "<o>_<o>"],
    [">_o", ">_o"],
    ["+_-", "+_-"],
    ["6_9", "6_9"],
    ["t_k_g", "t_k_g"],
    ["m_a_a_m_8", "m_a_a_m_8"],
    ["k-on!", "k-on!"],
  ];
  for (const [name, expected] of cases) test(`${name} → ${expected}`, () => assert.equal(promptTag(name), expected));
});

/**
 * 원본 테스트는 core parse로 "이스케이프한 글자를 읽으면 원래 글자"인지 본다. 여기서는 SPEC 2.2 규칙대로 글자만 읽는 작은 판독기를 쓴다.
 * 글자 노드 하나가 아닌 것(참조, 선택지 기호, 주석, 보기 가중치)이 나오면 null.
 */
function literalOf(text, inChoice) {
  if (!inChoice && text.startsWith("#")) return null; // 주석
  let i = 0;
  let out = "";
  if (!inChoice && text.startsWith("\\#")) {
    out = "#";
    i = 2;
  }
  if (inChoice && /^[0-9.]+\*/.test(text)) return null; // 보기 가중치
  for (; i < text.length; i++) {
    const c = text[i];
    const n = text[i + 1];
    if (c === "\\" && n !== undefined && ("{}$\\".includes(n) || (inChoice && n === "|"))) {
      out += n;
      i++;
    } else if (c === "$" && n === "{") return null;
    else if (c === "{" || c === "}" || (inChoice && c === "|")) return null;
    else out += c;
  }
  return out;
}

describe("escapeText 왕복", () => {
  const samples = ["{a|b}", "${x}", "$5", "a\\", "\\{", "\\\\", "\\m/", "\\||/", ":|", "|_|", "double_\\||/", "\\(cosplay\\)", "a|b", "}{", "", "😀 {"];
  for (const s of samples) {
    for (const inChoice of [false, true]) test(`${JSON.stringify(s)} inChoice=${inChoice}`, () => assert.equal(literalOf(escapeText(s, inChoice), inChoice), s));
  }
  test("무작위 문자열 2만 개", () => {
    const alphabet = ["{", "}", "$", "\\", "|", "a", " ", "(", ")", "_"];
    let x = 12345;
    const rnd = () => ((x = (x * 1103515245 + 12345) % 2 ** 31), x);
    for (let i = 0; i < 20000; i++) {
      let s = "";
      for (let n = rnd() % 9; n > 0; n--) s += alphabet[rnd() % alphabet.length];
      for (const inChoice of [false, true]) assert.equal(literalOf(escapeText(s, inChoice), inChoice), s, JSON.stringify(s));
    }
  });
  test("항목 맨 앞(atStart): # 와 \\# 로 시작해도 주석이 되지 않고 글자 그대로 (SPEC 2.7)", () => {
    assert.equal(escapeText("#compass", false, true), "\\#compass");
    assert.equal(escapeText("#compass"), "#compass");
    assert.equal(escapeText("\\#a", false, true), "\\\\#a");
    assert.equal(escapeText("a#b", false, true), "a#b");
    for (const s of ["#compass", "#", "##", "\\#a", "\\#", "\\\\#", "#{a}", " #a", "a#b", "\\", "{a|b}", "${x}", ""]) {
      assert.equal(literalOf(escapeText(s, false, true), false), s, JSON.stringify(s));
    }
  });
  test("이스케이프가 필요 없는 백슬래시는 겹치지 않는다", () => {
    assert.equal(escapeText("\\(a\\)"), "\\(a\\)");
    assert.equal(escapeText("\\||/"), "\\||/");
    assert.equal(escapeText("\\||/", true), "\\\\\\|\\|/");
  });
});

// ---------------------------------------------------------------- apps/web/test/syntax.test.ts

const show = (text) =>
  tokenize(text).map((t) => {
    const s = text.slice(t.from, t.to);
    if (t.type === "brace" || t.type === "bar") return `${s}${t.depth}`;
    if (t.type === "foreign") return `${t.kind}:${s}`;
    return `${t.type}:${s}`;
  });

describe("tokenize", () => {
  test("선택지 겹 수", () => {
    assert.deepEqual(show("{a|{b|c}|d}"), ["{1", "|1", "{2", "|2", "}2", "|1", "}1"]);
    assert.deepEqual(show("a} \\{x\\}"), ["}1", "escape:\\{", "escape:\\}"]);
  });
  test("보기 가중치", () => {
    assert.deepEqual(show("{3*a|b|1.5*c} 3*d { 4*e}"), ["{1", "weight:3*", "|1", "|1", "weight:1.5*", "}1", "{1", "}1"]);
    assert.deepEqual(show("{2*1.2::wlop ::|x}"), ["{1", "weight:2*", "nai:1.2::", "nai:::", "|1", "}1"]);
  });
  test("바깥 프롬프트 표기", () => {
    assert.deepEqual(show("(red hair:1.2), ((smile:0.8))"), ["comfy:(", "comfy::1.2)", "comfy:(", "comfy::0.8)"]);
    assert.deepEqual(show("hatsune miku \\(cosplay\\)"), []);
    assert.deepEqual(show("(miku \\(cosplay\\):1.1)"), ["comfy:(", "comfy::1.1)"]);
    assert.deepEqual(show("<lora:detail:0.6> BREAK 1girl"), ["lora:<lora:detail:0.6>", "break:BREAK"]);
    assert.deepEqual(show("1.2::artist:wlop ::, -1::bad::"), ["nai:1.2::", "nai:::", "nai:-1::", "nai:::"]);
    assert.deepEqual(show("{1.2::artist:wlop ::|(red:1.1)}"), ["{1", "nai:1.2::", "nai:::", "|1", "comfy:(", "comfy::1.1)", "}1"]);
    assert.deepEqual(show("${a::b}"), ["badref:${a::b}"]);
  });
  test("자동완성 판단(열린 ${)은 바깥 표기가 있어도 그대로", () => {
    assert.deepEqual(openRefBefore("(red:1.2), ${hai"), { from: 11, query: "hai" });
  });
});

// ---------------------------------------------------------------- apps/web/test/comment.test.ts

describe("주석 (SPEC 2.7)", () => {
  const showC = (text) => tokenize(text).map((t) => `${t.type}:${text.slice(t.from, t.to)}`);
  test("# 로 시작하면 텍스트 전체가 주석 토큰 하나", () => {
    assert.deepEqual(showC("# 메모 {a|${w:AAAAAAAAAAAA}} (x:1.2) BREAK"), ["comment:# 메모 {a|${w:AAAAAAAAAAAA}} (x:1.2) BREAK"]);
    assert.deepEqual(showC("#"), ["comment:#"]);
  });
  test("맨 앞이 아니면 #는 글자, 맨 앞의 \\#은 이스케이프", () => {
    assert.deepEqual(showC(" #a {b}"), ["brace:{", "brace:}"]);
    assert.deepEqual(showC("\\#compass, {a}"), ["escape:\\#", "brace:{", "brace:}"]);
    assert.deepEqual(showC("a\\#b"), []);
  });
  test("주석 안에서는 태그·참조 자동완성이 뜨지 않는다", () => {
    assert.equal(tagFragmentBefore("# lon"), null);
    assert.equal(tagFragmentBefore("#lon"), null);
    assert.equal(openRefBefore("# ${ha"), null);
    assert.equal(tagFragmentBefore("x, #lon").query, "#lon");
  });
  test("항목 맨 앞에 #로 시작하는 태그를 넣으면 \\#로", () => {
    assert.equal(tagInsertText("#compass", false, true), "\\#compass");
    assert.equal(tagInsertText("#compass", false, false), "#compass");
    assert.equal(tagInsertText("#unicus_(idolmaster)", false, true), "\\#unicus \\(idolmaster\\)");
    assert.equal(tagFragmentBefore("컴파").from, 0);
  });
});

// ---------------------------------------------------------------- 노드 전용: 여러 줄 칸

describe("stripCommentLines (wakaaka_node/text.py와 같은 사례)", () => {
  const cases = [
    ["a\n#c\nb", "a\nb"],
    ["#c\nb", "b"],
    ["a\n#c", "a"],
    ["#only", ""],
    [" #a\n#b", " #a"],
    ["\\#compass, a", "\\#compass, a"],
    ["#c\n\\#compass", "\\#compass"],
    ["a\n\\#compass", "a\n#compass"],
    ["a\n\\\\#b", "a\n\\\\#b"],
    ["x\r\n#c\r\ny", "x\r\ny"],
    ["{a|\n#b}", "{a|"],
  ];
  for (const [src, want] of cases) test(JSON.stringify(src), () => assert.equal(stripCommentLines(src), want));
});

describe("completionAt", () => {
  const at = (text) => completionAt(text.replace("|^", ""), text.indexOf("|^"));
  test("줄 하나가 웹의 칸 하나: 줄 맨 앞, 쉼표 뒤", () => {
    assert.deepEqual(at("1girl, smile\nlo|^"), { kind: "tag", from: 13, to: 15, query: "lo", inChoice: false, atStart: true });
    assert.deepEqual(at("1girl, lo|^\nsmile"), { kind: "tag", from: 7, to: 9, query: "lo", inChoice: false, atStart: false });
  });
  test("앞 줄에서 연 선택지 안이면 inChoice (주석 줄의 {는 세지 않는다)", () => {
    assert.equal(at("{a|\nlo|^").inChoice, true);
    assert.equal(at("{a|b}\nlo|^").inChoice, false);
    assert.equal(at("# {a|\nlo|^").inChoice, false);
  });
  test("주석 줄에서는 뜨지 않는다", () => {
    assert.equal(at("a\n# lo|^"), null);
    assert.equal(at("a\n#lo|^"), null);
    assert.equal(at("a\n${x}, # lo|^").query, "# lo");
  });
  test("열린 ${ 뒤는 와일드카드", () => {
    assert.deepEqual(at("1girl, ${ha|^"), { kind: "ref", from: 7, to: 11, query: "ha" });
    assert.deepEqual(at("a\n{x|${|^"), { kind: "ref", from: 5, to: 7, query: "" });
    assert.equal(at("${hair} lo|^"), null);
  });
  test("작가 태그 @ (Wakaaka.ArtistAt)", () => {
    const c = { inChoice: false, atStart: true };
    assert.equal(tagCompletionText({ name: "wlop", category: 1 }, c, true), "@wlop");
    assert.equal(tagCompletionText({ name: "wlop", category: 1 }, c, false), "wlop");
    assert.equal(tagCompletionText({ name: "long_hair", category: 0 }, c, true), "long hair");
    assert.equal(tagCompletionText({ name: "#compass", category: 0 }, c, true), "\\#compass");
    assert.equal(tagCompletionText({ name: "ask_(askzy)", category: 1 }, { inChoice: true, atStart: false }, true), "@ask \\(askzy\\)");
  });
});

describe("이름 참조와 refs 표", () => {
  test("nameRefs: 주석 줄 제외, 순서대로 겹침 없이", () => {
    assert.deepEqual(nameRefs("${hair}, ${w:AAAAAAAAAAAA}\n# ${memo}\n{${ eyes }|${hair}}, ${bad=1}"), ["hair", "eyes"]);
  });
  test("withRef, unresolvedNames: 대소문자 무시", () => {
    let v = withRef("", "Hair", "AAAAAAAAAAAA");
    v = withRef(v, "hair", "BBBBBBBBBBBB");
    assert.deepEqual(parseRefsValue(v), { refs: { hair: "BBBBBBBBBBBB" }, lock: {} });
    assert.deepEqual(unresolvedNames(["HAIR", "eyes"], v), ["eyes"]);
    assert.deepEqual(parseRefsValue("not json"), { refs: {}, lock: {} });
    assert.deepEqual(parseRefsValue('{"refs":{"a":"x"},"lock":{"x":"y"}}'), { refs: { a: "x" }, lock: { x: "y" } });
  });
  test("tagSummary: 한글 본문, 없으면 키워드", () => {
    assert.equal(tagSummary({ body: "긴 머리.", keywords: ["a"] }), "긴 머리.");
    assert.equal(tagSummary({ body: "long hair", keywords: ["긴 머리", "장발"] }), "긴 머리, 장발");
  });
});

// ---------------------------------------------------------------- 늦게 온 응답 (검토 1번)

import { completionStillValid, sameCompletion } from "../../web/lib/textcore.js";

describe("completionStillValid: 응답을 그리거나 고르기 직전에 자리가 그대로인지", () => {
  const text = "1girl, ${fo\nsmile, cat ears\nlast line";
  const at = text.indexOf("\n"); // ${fo 끝
  const comp = completionAt(text, at);
  test("요청 때와 같은 값·커서면 그대로", () => {
    assert.deepEqual(comp, { kind: "ref", from: 7, to: 11, query: "fo" });
    assert.equal(completionStillValid(comp, text, at, at), true);
  });
  test("시나리오 A: 받는 동안 같은 칸의 아래 줄을 눌렀다", () => {
    assert.equal(completionStillValid(comp, text, text.length, text.length), false);
    assert.equal(completionStillValid(comp, text, at + 3, at + 3), false);
  });
  test("시나리오 B: 커서를 자리 앞으로 옮겼다", () => {
    for (const c of [0, 3, 7, 8, 9, 10]) assert.equal(completionStillValid(comp, text, c, c), false, String(c));
  });
  test("시나리오 C: ${fo → ${foo2로 더 쳤다", () => {
    const more = text.replace("${fo", "${foo2");
    const c = more.indexOf("\n");
    assert.equal(completionStillValid(comp, more, c, c), false);
    assert.equal(completionStillValid(completionAt(more, c), more, c, c), true);
  });
  test("범위를 골랐거나 글자를 지웠다", () => {
    assert.equal(completionStillValid(comp, text, at - 2, at), false);
    const less = text.replace("${fo", "${f");
    assert.equal(completionStillValid(comp, less, at - 1, at - 1), false);
  });
  test("태그 자리도 선택지 안 여부까지 같아야 한다", () => {
    const t = "{a|lo";
    const c = completionAt(t, t.length);
    assert.equal(completionStillValid(c, t, t.length, t.length), true);
    assert.equal(sameCompletion(c, { ...c, inChoice: false }), false);
    assert.equal(completionStillValid(c, "{a}|lo", 6, 6), false);
    assert.equal(sameCompletion(null, c), false);
  });
});

import { completionFor, kindEnabled } from "../../web/lib/textcore.js";

describe("completionFor: 종류별 켜고 끄기 (DESIGN 8절 \"켜고 끄기\")", () => {
  // 자리마다 completionAt이 내는 종류: 태그 자리, ${ 자리, ${ 뒤 쉼표(여전히 ${ 안), 아무것도 아닌 자리, 주석 줄
  const spots = {
    tag: "1girl, long h|^",
    tagStart: "lo|^",
    ref: "1girl, ${가족|^",
    refAfterComma: "a, ${x, y|^",
    choiceTag: "{a|bl|^",
    none: "1girl,|^",
    comment: "# a, b|^",
    closedRef: "${가족}|^",
  };
  const COMBOS = {
    "켬/켬": { tag: true, wildcard: true },
    "태그만": { tag: true, wildcard: false },
    "와일드카드만": { tag: false, wildcard: true },
    "둘 다 끔": { tag: false, wildcard: false },
  };
  const at = (s, en) => completionFor(s.replace("|^", ""), s.indexOf("|^"), en)?.kind ?? null;
  const all = (s) => completionAt(s.replace("|^", ""), s.indexOf("|^"))?.kind ?? null;
  const want = {
    "켬/켬": { tag: "tag", tagStart: "tag", ref: "ref", refAfterComma: "ref", choiceTag: "tag", none: null, comment: null, closedRef: null },
    "태그만": { tag: "tag", tagStart: "tag", ref: null, refAfterComma: null, choiceTag: "tag", none: null, comment: null, closedRef: null },
    "와일드카드만": { tag: null, tagStart: null, ref: "ref", refAfterComma: "ref", choiceTag: null, none: null, comment: null, closedRef: null },
    "둘 다 끔": { tag: null, tagStart: null, ref: null, refAfterComma: null, choiceTag: null, none: null, comment: null, closedRef: null },
  };
  for (const [name, en] of Object.entries(COMBOS)) {
    test(name, () => {
      for (const [k, s] of Object.entries(spots)) assert.equal(at(s, en), want[name][k], `${name} ${k}`);
    });
  }
  test("켬/켬은 completionAt과 같다(기존 동작 그대로)", () => {
    for (const s of Object.values(spots)) assert.deepEqual(completionFor(s.replace("|^", ""), s.indexOf("|^")), completionAt(s.replace("|^", ""), s.indexOf("|^")));
    for (const s of Object.values(spots)) assert.equal(at(s, COMBOS["켬/켬"]), all(s));
  });
  test("와일드카드를 끄면 ${ 자리에 태그 목록을 대신 띄우지 않는다", () => {
    // ${ 안의 글자는 태그 조각으로도 읽힐 수 있지만 그 자리는 null이어야 한다
    for (const s of ["${long h|^", "a, ${blue|^", "{x|${hai|^"]) {
      assert.equal(all(s), "ref", s);
      assert.equal(at(s, COMBOS["태그만"]), null, s);
    }
  });
  test("여러 줄: 줄마다 따로", () => {
    const text = "${가족\nlong h";
    assert.equal(completionFor(text, 4, COMBOS["태그만"]), null);
    assert.equal(completionFor(text, text.length, COMBOS["태그만"]).kind, "tag");
    assert.equal(completionFor(text, text.length, COMBOS["와일드카드만"]), null);
    assert.equal(completionFor(text, 4, COMBOS["와일드카드만"]).kind, "ref");
  });
  test("completionStillValid: 응답이 오는 사이 그 종류를 껐으면 그리지 않는다", () => {
    const text = "1girl, ${fo";
    const comp = completionAt(text, text.length);
    assert.equal(completionStillValid(comp, text, text.length, text.length, COMBOS["켬/켬"]), true);
    assert.equal(completionStillValid(comp, text, text.length, text.length, COMBOS["태그만"]), false);
    assert.equal(completionStillValid(comp, text, text.length, text.length, COMBOS["둘 다 끔"]), false);
    assert.equal(completionStillValid(comp, text, text.length, text.length), true); // 넷째 인자 없으면 켬/켬
  });
  test("kindEnabled: 틀린 값은 켜짐(기본값)", () => {
    assert.equal(kindEnabled("tag", { tag: undefined, wildcard: false }), true);
    assert.equal(kindEnabled("ref", { tag: false, wildcard: null }), true);
    assert.equal(kindEnabled("ref", { wildcard: false }), false);
    assert.equal(kindEnabled("other"), false);
  });
});
