// web/lib/textcore.js를 와카아카 저장소의 원본 TS와 같은 입력으로 직접 비교한다 (환경 변수 WAKAAKA_REPO가 있을 때만).
// Node 22는 .ts의 타입을 지우고 바로 읽는다. 원본은 읽기만 한다.
//
//     set WAKAAKA_REPO=C:\workspace\wakaaka
//     node --test tests/js/webdiff.test.mjs

import assert from "node:assert/strict";
import { test } from "node:test";
import { pathToFileURL } from "node:url";
import * as mine from "../../web/lib/textcore.js";

const REPO = process.env.WAKAAKA_REPO;
const load = (p) => import(pathToFileURL(`${REPO}/${p}`).href);

test("웹 원본과 무작위 입력 비교", { skip: REPO ? false : "WAKAAKA_REPO가 없음" }, async () => {
  const tags = await load("apps/web/src/editor/tags.ts");
  const syntax = await load("apps/web/src/editor/syntax.ts");
  const tagtext = await load("packages/core/src/tagtext.ts");
  const parser = await load("packages/core/src/parser.ts");
  const alphabet = ["{", "}", "|", "$", "\\", "#", ",", " ", "(", ")", ":", "1", "2", ".", "*", "a", "b", "_", "w", "<", ">", "가", "=", "\t"];
  let x = 20261119;
  const rnd = () => ((x = (x * 1103515245 + 12345) % 2 ** 31), x);
  const gen = () => {
    let s = "";
    for (let n = rnd() % 14; n > 0; n--) s += rnd() % 9 === 0 ? ["${", "${w:AAAAAAAAAAAA}", "BREAK", "<lora:", "1.2::", "lo"][rnd() % 6] : alphabet[rnd() % alphabet.length];
    return s;
  };
  let n = 0;
  for (let i = 0; i < 30000; i++) {
    const s = gen();
    const m = JSON.stringify(s);
    assert.deepEqual(mine.tagFragmentBefore(s), tags.tagFragmentBefore(s), m);
    assert.deepEqual(mine.openRefBefore(s), syntax.openRefBefore(s), m);
    assert.deepEqual(mine.tokenize(s), syntax.tokenize(s), m);
    assert.deepEqual(mine.classifyRefBody(s, "edit"), parser.classifyRefBody(s, "edit"), m);
    for (const inChoice of [false, true]) {
      for (const atStart of [false, true]) {
        assert.equal(mine.tagInsertText(s, inChoice, atStart), tags.tagInsertText(s, inChoice, atStart), m);
        assert.equal(mine.escapeText(s, inChoice, atStart), tagtext.escapeText(s, inChoice, atStart), m);
      }
    }
    assert.equal(mine.promptTag(s), tagtext.promptTag(s), m);
    assert.equal(mine.tagDisplayName(s), tags.tagDisplayName(s), m);
    n++;
  }
  for (let v = 0; v < 3e7; v = v * 1.37 + 1) assert.equal(mine.formatPostCount(Math.floor(v)), tags.formatPostCount(Math.floor(v)));
  // 여러 줄 칸의 줄 하나는 웹의 칸 하나와 같다
  for (let i = 0; i < 5000; i++) {
    const s = gen().replaceAll("\n", "");
    const c = mine.completionAt("x\n" + s, 2 + s.length);
    const w = syntax.openRefBefore(s) ?? tags.tagFragmentBefore(s);
    if (s.startsWith("#")) assert.equal(c, null);
    else if (w === null) assert.equal(c, null, JSON.stringify(s));
    else assert.equal(c.from, 2 + w.from, JSON.stringify(s));
  }
  console.log(`# webdiff: ${n} random inputs, all equal`);
});
