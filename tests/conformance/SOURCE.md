# 출처

이 폴더(이 파일 제외)는 와카아카 저장소 `packages/core/conformance`를 폴더째 복사한 것이다.
TS 전개기와 이 노드의 Python 전개기가 같은 세트를 통과해야 한다. 여기서 직접 고치지 않는다.
명세가 바뀌면 와카아카 쪽 세트를 먼저 고친 뒤 다시 복사하고 아래 커밋을 갱신한다.

| 항목 | 값 |
|---|---|
| 저장소 | `joykst96/wakaaka` |
| 커밋 | `61410a8` (main, 주석 항목 SPEC 2.7) |
| 복사한 날 | 2026-11-08 |

`tests/data/bundles.json`은 `ed1a747`에서 `node fixtures/tools/build-examples.ts`가 만든 `fixtures/out/bundles.json`이다(차등 테스트용 예시 묶음). `61410a8`에서 다시 만든 것과 바이트 단위로 같아 그대로 둔다.

복사하지 않고 와카아카 저장소의 세트로 바로 돌리려면:

```bash
WAKAAKA_CONFORMANCE=<wakaaka>/packages/core/conformance WAKAAKA_BUNDLES=<wakaaka>/fixtures/out/bundles.json python -I -m unittest discover -s tests -t .
```
