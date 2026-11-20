# comfyui-wakaaka — Wakaaka Wildcard, Wakaaka Text

[와카아카](https://wakaaka.baepoyong.com) 와일드카드를 전개해 프롬프트 문자열(STRING)을 내는 ComfyUI 커스텀 노드.

- **Wakaaka Wildcard**: 와일드카드 ID 하나를 seed로 전개한다. 웹 미리보기와 같은 결과.
- **Wakaaka Text**: 여러 줄 프롬프트 칸. 태그와 와일드카드 자동완성이 붙고, 와카아카 문법(`${이름}`, `{a|b}`, `{2*a|b}`)을 전개한다.
- **Wakaaka Text (Simple)**: Wakaaka Text와 같지만 텍스트 칸과 seed만 보인다.

와카아카는 프롬프트 와일드카드를 웹에서 편집·공유하는 서비스다. 웹, 서버, 이 노드가 같은 와일드카드와 seed에서 글자 단위로 같은 결과를 낸다. 서비스와 명세(`docs/SPEC.md`), 설계(`docs/DESIGN.md` 8절)는 와카아카 저장소 [joykst96/wakaaka](https://github.com/joykst96/wakaaka)에 있다.

표준 라이브러리만 쓴다. 설치할 Python 패키지가 없다.

## 설치

`ComfyUI/custom_nodes`에서 clone하고 ComfyUI를 다시 시작한다. 설치할 Python 패키지는 없다.

```bash
git clone https://github.com/joykst96/comfyui-wakaaka
```

업데이트는 그 폴더에서 `git pull`. ComfyUI 레지스트리(Manager의 노드 검색)에는 올리지 않는다.

ComfyUI Manager의 "Install via Git URL"에 같은 주소를 넣어도 된다. 다만 최신 Manager는 이 기능이 기본으로 꺼져 있어서, `ComfyUI/user/__manager/config.ini`의 `[default]`에 `allow_git_url_install = true`를 넣고 ComfyUI를 다시 시작해야 한다. ComfyUI가 루프백 주소(`127.0.0.1` 등)로만 열려 있을 때만 동작한다(Manager README "Dedicated install flags"). 예전 버전의 `security_level = weak`로는 더 이상 풀리지 않는다.

개발할 때는 다른 곳에 clone하고 `custom_nodes` 아래에 링크로 연결해도 된다(Windows는 junction).

```powershell
New-Item -ItemType Junction -Path <ComfyUI>\custom_nodes\comfyui-wakaaka -Target <clone한 폴더>
```

## 쓰는 법

노드 **Wakaaka Wildcard**(분류 `wakaaka`)를 놓고 출력 `text`를 `CLIPTextEncode`의 text 입력 등에 잇는다. 품질 태그 같은 고정 문구는 와카아카 웹에서 "장면 프리셋" 와일드카드로 만들거나 문자열 연결 노드로 붙인다.

| 위젯 | 내용 |
|---|---|
| `wildcard` | 와일드카드 ID(12자). 웹 주소(`…/w/<ID>`)를 붙여 넣어도 된다. 아래 정보 줄에 "이름 · 소유자 · v번호" |
| `seed` | 표준 seed 위젯(실행 후 고정/증가/무작위) |
| `follow_latest` | 켜짐(기본): 큐에 넣을 때마다 최신 버전으로. 꺼짐: 지난번 lock 그대로(재현) |
| `text` | 읽기 전용. 큐에 넣을 때 전개 결과가 채워진다 |
| `lock` | 숨김. 정보 줄에 "v12에 고정 · 하위 3개"처럼 보인다 |

"웹에서 열기" 버튼은 설정의 서버 주소로 그 와일드카드 페이지를 연다.

### Wakaaka Text

노드 **Wakaaka Text**(분류 `wakaaka`)는 ComfyUI 기본 여러 줄 문자열 노드처럼 쓴다. 출력 `text`를 `CLIPTextEncode` 등에 잇는다.

| 위젯 | 내용 |
|---|---|
| `text` | 프롬프트. 와카아카 문법 전체를 쓴다. 첫 글자가 `#`인 줄은 주석(지워진다). 글자 `#`로 시작하는 줄은 `\#`로 쓴다 |
| `seed` | 표준 seed 위젯 |
| `result` | 읽기 전용. 큐에 넣을 때 전개 결과(최종 프롬프트)가 채워진다 |
| `refs` | 숨김. 참조한 이름 → 와일드카드 ID와 그때의 lock |

- **자동완성**: 칸 맨 앞·쉼표 뒤·선택지 `{` `|` 뒤에서 태그를 영문이나 한국어로 찾는다(`long h`, `긴 머리`). 고르면 밑줄은 공백, 괄호는 `\(` `\)`로 넣는다. 닫히지 않은 `${` 뒤에서는 설정한 유저의 와일드카드를 찾고, 고르면 `${이름}`을 넣고 `refs`에 ID를 적는다. ↑↓로 고르고 Enter·Tab으로 넣고 Esc로 닫는다. 캔버스 렌더링과 새 노드 렌더링(Vue) 모두에서 된다.
- **이름과 ID**: 칸에는 `${이름}`이 보이고 `refs`에 ID가 남는다. 손으로 친 이름은 큐에 넣을 때 유저 목록에서 찾는다(노드 아래 정보 줄에 "ID를 찾을 이름"으로 보인다). PNG에 이름과 ID가 같이 남으므로, 이름이 바뀌거나 유저 아이디 설정이 다른 PC에서도 ID로 전개한다. `${w:ID}`를 직접 써도 된다.
- **전개**: 항상 노드에서, 참조한 와일드카드의 최신 버전으로 한다(모드 설정과 상관없음). 서버에 연결할 수 없으면 캐시로 전개한다. `${X}` 하나뿐인 텍스트도 같은 seed의 Wakaaka Wildcard(ID X)와 결과가 다르다(텍스트가 한 단계 위의 문서로 전개되기 때문, 설계 8절). 웹 미리보기와 같은 결과가 필요하면 Wakaaka Wildcard를 쓴다.
- **띄어쓰기 다듬기**: 텍스트에 펼 것(참조 `${…}`나 선택지 `{…}`)이 하나라도 있으면 전개 결과 전체를 줄마다 다듬는다. 없으면 친 대로 나간다(빈 조각과 맨 앞뒤 쉼표만 지운다). 스페이스·탭이 이어진 것은 한 칸, 쉼표 앞 공백은 지우고 쉼표 뒤는 한 칸(줄 끝 제외), 줄 앞뒤 공백은 지운다(`a ,b  c` → `a, b c`). 줄바꿈과 빈 줄, 전각 쉼표·전각 공백은 그대로다. 두 텍스트 노드에만 있는 규칙이라 Wakaaka Wildcard와 웹 미리보기의 결과는 다듬지 않는다(설계 8절 "결과 다듬기").
- 문법 오류, 찾을 수 없는 이름(모두 적는다), 받을 수 없는 와일드카드는 노드 오류로 낸다. 옛 결과가 조용히 나가지 않는다.

### Wakaaka Text (Simple)

노드 **Wakaaka Text (Simple)**(종류 `WakaakaTextSimple`)은 기본 여러 줄 문자열 노드에 seed와 "생성 후 제어"만 붙은 모양이다. 하는 일은 Wakaaka Text와 같다. 문법, 자동완성(태그, `${` 와일드카드), 이름 해석, 전개 규칙이 같아서 같은 `text`·seed면 결과가 같고 두 노드를 바꿔 끼워도 프롬프트가 달라지지 않는다. 결과 칸과 노드 아래 정보 줄이 없을 뿐, `result`(최종 프롬프트)와 `refs`(이름 → ID, lock)는 숨긴 값으로 있어서 큐에 넣을 때 채워지고 PNG에 남는다. 전개 결과를 화면에서 보려면 출력을 문자열 표시 노드(예: Preview Any)에 잇거나 Wakaaka Text를 쓴다. 찾을 수 없는 이름 같은 문제는 큐에 넣을 때 노드 오류로만 보인다.

큐에 넣을 때 노드가 text와 lock을 채워 실행 입력과 PNG 워크플로에 써 넣는다. 그래서 모든 이미지에 그때의 결과와 lock이 남는다. 옛 이미지를 다시 만들려면 PNG를 불러와 `follow_latest`를 끄고 큐에 넣는다.

`wildcard`나 `seed`를 다른 노드에서 링크로 받으면 큐에 넣을 때 값을 알 수 없어 실행할 때 채운다. 이때도 PNG 워크플로에는 남지만, 화면의 text는 실행이 끝난 뒤에 바뀐다.

## 설정 (ComfyUI 설정 → Wakaaka)

전개 모드, 서버 주소, 유저 아이디, 자동완성 켜고 끄기는 노드 위젯이 아니라 ComfyUI 설정 창에 있다. 두 모드는 결과가 같아서 워크플로마다 다르게 둘 이유가 없기 때문이다.

- 여는 곳: 왼쪽 아래 톱니바퀴(설정) → 왼쪽 목록 아래쪽 "기타"의 **Wakaaka** → "노드" 아래 **자동완성 (와일드카드)**, **자동완성 (태그)**, **작가 태그 앞에 골뱅이(at) 붙이기**, **유저 아이디**, **서버 주소**, **전개 모드**
- 설정 창 위쪽 검색에 `wakaaka`나 `전개 모드`를 쳐도 된다.

| 설정 | 기본값 | 내용 |
|---|---|---|
| 전개 모드 `Wakaaka.Mode` | 서버 | 서버: 서버가 전개한 결과(`/api/w/{id}/resolve`)를 받는다. 로컬: 묶음(`/bundle`)을 받아 해시를 확인하고 이 PC에서 전개한다. 결과는 같다 |
| 서버 주소 `Wakaaka.ServerUrl` | `https://wakaaka.baepoyong.com` | http(s)만. 직접 띄운 서버를 쓰려면 그 주소로 바꾼다 |
| 유저 아이디 `Wakaaka.UserId` | 빈 값 | Wakaaka Text의 `${` 자동완성과 이름 해석에 쓸 와카아카 아이디 하나(영문·숫자·`_-` 3~32자). 비밀번호는 받지 않는다(모든 와일드카드가 공개). 유저 목록은 60초 캐시 |
| 작가 태그 앞에 골뱅이(at) 붙이기 `Wakaaka.ArtistAt` | 꺼짐 | 켜면 태그 자동완성에서 작가 태그(분류 1)를 `@이름`으로 넣는다. 태그 자동완성이 켜져 있을 때만 뜻이 있다 |
| 자동완성 (태그) `Wakaaka.TagAutocomplete` | 켜짐 | 텍스트 칸의 태그 목록. 다른 자동완성 확장과 함께 쓰면 끈다. 꺼져 있으면 ComfyUI를 띄울 때 태그 인덱스를 확인하지 않는다(아래) |
| 자동완성 (와일드카드) `Wakaaka.WildcardAutocomplete` | 켜짐 | `${` 뒤의 와일드카드 목록. 꺼도 손으로 친 `${이름}`은 큐에 넣을 때 그대로 찾는다(끄는 것은 목록뿐) |

- 자동완성 두 설정은 서로 따로 켜고 끄며, 바꾸면 새로 고침 없이 바로 적용된다. 꺼진 종류의 자리에서는 목록을 띄우지 않고 ↑↓·Enter·Tab·Esc를 가로채지 않으며 ComfyUI에 묻지도 않는다(Enter는 줄바꿈 그대로). 와일드카드만 끄면 `${` 뒤에서는 아무것도 뜨지 않는다(태그 목록을 대신 띄우지 않음). 둘 다 끄면 텍스트 칸 입력에 전혀 끼어들지 않는다.
- 설정 창의 항목 이름에는 `@`, `{`, `}`를 쓰지 않는다. 프런트가 이름을 번역 메시지로 읽어서 이 글자가 있으면 그 항목이 설정 창에서 빠진다(1.57.4에서 확인).
- 큐에 넣을 때의 훅은 누가 넣었는지 모르므로 `default` 사용자의 설정을 쓴다(`--multi-user`에서 사용자마다 다르게 둘 수 없다).
- Windows에서 `localhost`는 IPv6(`::1`)를 먼저 시도해 서버가 IPv4에서만 들으면 요청마다 약 2초 늦다. 로컬 서버는 `http://127.0.0.1:포트`로 적는다.

## 캐시와 오프라인

- 받은 문서는 해시 단위로 `ComfyUI/user/__wakaaka/cache`에 둔다(노드를 업데이트해도 남는다). `docs/<해시>.json`과 마지막 최신 lock `latest/<ID>.json`.
- 서버에 연결할 수 없으면 캐시로 전개한다. `follow_latest`가 켜져 있으면 마지막으로 받은 버전으로, 꺼져 있으면 고정한 lock으로. 정보 줄에 "서버에 연결하지 못해 캐시로 전개함"이 붙는다. 캐시도 없으면 "와일드카드를 찾을 수 없음: ID" 오류가 난다.
- 서버 모드도 성공한 뒤 그 버전의 묶음을 백그라운드로 받아 캐시한다.
- 요청은 User-Agent `wakaaka-comfy-node/<버전>`, 타임아웃 3초. 연결에 실패하면 10초 동안 그 서버를 부르지 않고 바로 캐시로 간다(큐에 넣는 동안 ComfyUI가 멈춰 있기 때문).

## 태그 인덱스 (자동완성용)

- ComfyUI가 노드를 로딩할 때 백그라운드에서 Hugging Face [`mengod-hf/danbooru-tags-kr`](https://huggingface.co/datasets/mengod-hf/danbooru-tags-kr)의 최신 통합본(`danbooru-tags-kr-YYYY-MM-DD.csv`)을 확인한다. 파일 목록의 SHA-256이 지금 인덱스와 다를 때만 받아(약 27MB) SQLite 인덱스(약 110MB)로 바꾼다. 형식 검증을 통과해야 바꿔 넣고, 실패하거나 오프라인이면 기존 인덱스를 그대로 쓴다. ComfyUI 시작을 막지 않는다.
- 태그 자동완성(`Wakaaka.TagAutocomplete`)이 꺼져 있으면(`default` 사용자의 설정) 로딩 때 확인하지 않는다. 이미 만든 인덱스는 지우지 않는다. 설정을 켠 뒤 첫 태그 검색 때 백그라운드로 한 번 확인한다(다시 시작할 필요 없음). `/wakaaka/tags/status`의 `check`가 `off`(꺼져 있어 확인하지 않음) 또는 `started`다.
- 위치는 `ComfyUI/user/__wakaaka/tags/tags-<SHA-256 앞 16자>.sqlite`(약 110MB, 부분 일치용 FTS5 trigram 포함). 받은 CSV는 변환 뒤 지우고 데이터셋 LICENSE(`LICENSE-danbooru-tags-kr`)를 옆에 둔다.
- 태그 데이터 출처: Hugging Face [`mengod-hf/danbooru-tags-kr`](https://huggingface.co/datasets/mengod-hf/danbooru-tags-kr). 한국어 번역·설명·분류·키워드는 CC BY-SA 4.0이다(데이터셋 LICENSE). 태그 이름과 포스트 수는 Danbooru에서 온 것이다.
- 경로: `GET /wakaaka/tags/suggest?q=`(와카아카 서버 `/api/tags/suggest`와 같은 규칙과 응답, 최대 20개), `GET /wakaaka/tags/status`.
- 이 Python의 SQLite가 FTS5 trigram을 만들 수 없으면 받지 않고 상태에 이유를 남긴다. LICENSE를 받지 못하면 인덱스는 만들고 상태의 `licenseError`에 남긴 뒤 다음 확인 때 다시 받아 본다.

## 한계

- **`--multi-user`**: 자동완성(`${` 후보)은 요청한 사용자의 `Wakaaka.UserId`를 보지만, 큐에 넣을 때의 훅은 누가 넣었는지 몰라 `default` 사용자의 설정을 본다. 팝업으로 고른 이름은 `refs`에 ID가 있어 상관없지만, 손으로 친 이름은 `default` 사용자의 유저 아이디로 찾는다.
- **큐에 넣을 때 화면이 멈출 수 있다**: 훅은 ComfyUI 이벤트 루프 안에서 동기로 돈다. Wakaaka Text(Simple도 같다) 하나를 큐에 넣을 때 부르는 것은 참조한 와일드카드마다 묶음 1번(최신으로 전개하므로 캐시가 있어도 부른다)과, `refs`에 ID가 없는 이름이 있을 때만 유저 목록 1번(60초 메모리 캐시)이다(`tests/test_text.py` RequestCount). 요청마다 타임아웃은 3초다. 서버가 느리게라도 답하면 참조 수만큼 기다리고, 연결이 안 되면 첫 실패 뒤 10초 동안 그 서버를 부르지 않으므로 한 번(최대 3초)만 기다린 뒤 캐시로 전개한다.
- Wakaaka Wildcard의 로컬 모드도 큐마다 `/meta`와 `/bundle`을 부른다(서버 모드는 `/meta`와 `/resolve`).

## 구조

| 파일 | 내용 |
|---|---|
| `__init__.py` | ComfyUI에 붙이는 곳: on-prompt 훅, `/wakaaka/meta`·`/wakaaka/tags/*` 경로, 노드 등록, 캐시·태그 인덱스 위치 |
| `wakaaka_node/node.py` | 노드 정의와 실행 |
| `wakaaka_node/hook.py` | 큐에 넣을 때 prompt·PNG 워크플로·화면에 text와 lock을 쓴다 |
| `wakaaka_node/fill.py` | 모드별로 노드 하나를 채운다(서버, 로컬, 캐시) |
| `wakaaka_node/client.py` | 서버 API 호출 |
| `wakaaka_node/expander.py` | 전개기(와카아카 `docs/SPEC.md`). TS 전개기와 글자 단위로 같은 결과 |
| `wakaaka_node/jcs.py`, `bundle.py`, `cache.py` | 내용 해시, 묶음 검증, 캐시 |
| `wakaaka_node/tagcsv.py`, `tagindex.py`, `tagupdate.py` | 태그 CSV 읽기, SQLite 인덱스와 검색, 받기·검증·교체 |
| `wakaaka_node/text.py`, `userlist.py` | Wakaaka Text 전개(줄 주석, 이름 → ID, 묶음 합치기), 유저 와일드카드 목록 |
| `web/wakaaka_text.js`, `web/lib/textcore.js` | Wakaaka Text·Simple 화면(자동완성 팝업, 정보 줄, Simple의 숨김), 자동완성 순수 함수(웹 편집기에서 옮김) |
| `web/wakaaka.js` | 설정, 정보 줄, 읽기 전용 text, "웹에서 열기" |
| `tests/` | 단위 테스트, 공통 테스트 세트 사본(`conformance/`), 예시 묶음(`data/`) |

## 테스트

ComfyUI가 쓰는 Python(포터블이면 `python_embeded/python.exe`)으로 표준 `unittest`만 쓴다.

```bash
python -I -m unittest discover -s tests -t .
```

- `test_conformance.py`: 공통 테스트 세트 전부와 SPEC 4.3 성질. 세트는 와카아카 `packages/core/conformance`의 사본(`tests/conformance`, 출처와 갱신 방법은 `tests/conformance/SOURCE.md`)
- `test_differential.py`: 참조 모델(`refmodel.py`)과 무작위 묶음, 예시 묶음(`tests/data/bundles.json`) 차등 비교
- `test_basics.py`, `test_server_mode.py`, `test_local_mode.py`: ID·설정, 가짜 서버로 HTTP·훅·노드·캐시·오프라인
- `test_live.py`: 실제 서버와 curl 응답 비교. `WAKAAKA_LIVE_URL`(예: `http://127.0.0.1:3000`)을 줄 때만 돈다
- `test_tags.py`: 태그 CSV 파서(두 키워드 표기), 검색 규칙, 가짜 Hugging Face로 받기·건너뛰기·교체·실패 시 유지
- `test_tags_live.py`: 태그 검색을 실제 서버 `/api/tags/suggest`와 비교. `WAKAAKA_LIVE_URL`과 서버가 가져온 CSV(`WAKAAKA_TAGS_CSV`)를 줄 때만 돈다
- `test_text.py`: Wakaaka Text(이름 해석, 줄 주석, 난수 경로, 묶음 엇갈림, 오프라인, 훅과 노드), Simple이 훅을 거쳐 Text와 같은 결과·기록을 내는지

자동완성 순수 함수는 Node 22로 따로 검사한다(의존성 없음).

```bash
node --test
```

- `tests/js/textcore.test.mjs`: 와카아카 웹 편집기 vitest에서 옮긴 사례와 여러 줄 칸 사례
- `tests/js/webdiff.test.mjs`: 와카아카 저장소의 원본 TS와 무작위 입력으로 직접 비교. `WAKAAKA_REPO`(와카아카 저장소 경로)를 줄 때만 돈다

## 라이선스

[MIT](LICENSE)
