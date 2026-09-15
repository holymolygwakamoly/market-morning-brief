# Market Morning Brief — 시장 아침 브리핑 생성기 (로컬 대시보드)

폴더 안의 **`시장브리핑 대시보드`** 바로가기를 누르면 브라우저에 대시보드가 열립니다. 날짜(오늘 KST ~ 3일 전)를 고르고 **[보고서 생성]** 을 누르면 미국·한국 증시 뉴스와 거시 이슈를 무료 공개 소스에서 수집하고, **Claude Code(구독)** 로 섹터 중심 한국어 해석(AI 섹터 매일 필수)을 만들어 **핵심 요약 / 상세 분석 / 참고 자료** 3부 HTML 보고서를 `docs/`에 생성합니다. 약 3~5분 걸립니다.

- 계획·설계: `PLAN.md` (v2.1 + §10 v3) · 진행 맥락: `HANDOFF.md`
- 비용: **추가 결제 없음** — LLM은 이 PC에 로그인된 Claude Code CLI(`claude -p`)를 사용하므로 Max/Pro 구독 사용량만 소모합니다. API 키 불필요.
- **투자 조언이 아닙니다.** 매매 판단의 참고 자료일 뿐입니다.

---

## 1. 설치 (최초 1회)

1. **Claude Code 설치 + 로그인** — 터미널에서 `claude` 실행 후 `/login` (구독 계정). `claude --version` 이 찍히면 OK.
2. **Python 3.12** 설치 후 이 폴더에서:
   ```bat
   python -m venv .venv
   .venv\Scripts\pip install -r requirements.lock -e .
   ```
3. (선택) 바로가기 아이콘이 없으면 `powershell -ExecutionPolicy Bypass -File scripts\make_shortcut.ps1` 로 `시장브리핑 대시보드.lnk` 를 다시 만듭니다.

## 2. 사용법

1. `시장브리핑 대시보드.lnk` (또는 `.bat`) 더블클릭 → 콘솔 창이 최소화된 채로 뜨고 브라우저에 `http://127.0.0.1:8765/` 가 열립니다.
2. 날짜 선택 → **[보고서 생성]**. 진행 단계(collect → stage1 → stage2 → render)와 로그가 2초마다 갱신됩니다.
3. 완료되면 **[보고서 열기]** 링크가 나타납니다. `docs/index.html` 은 항상 **가장 최신 날짜** 보고서이고, `docs/reports/YYYY-MM-DD.html` 에 날짜별로 쌓입니다(`docs/archive.html` 목록).
4. (선택) **[GitHub에 게시]** → `docs/` 를 커밋·push 합니다. 리포의 *Settings → Pages → Deploy from a branch → main / /docs* 가 설정돼 있으면 `https://<계정>.github.io/<리포>/` 에서 폰으로도 볼 수 있습니다. git push 자격 증명이 이 PC에 있어야 합니다.
5. 끝나면 콘솔 창을 닫거나 `Ctrl+C`.

### 날짜 선택 규칙
- **오늘(KST) 기준 3일 전까지만** 선택할 수 있습니다(서버에서도 검증, 벗어나면 400).
- 과거 날짜는 "직전 영업일 06:50 KST ~ 해당일 09:50 KST" 기사 중 **RSS에 아직 남아 있는 것만** 수집되므로 오늘보다 내용이 적을 수 있습니다. 월요일은 금요일 미국장 마감분까지(약 72시간) 포함합니다.

### 명령줄
```bat
.venv\Scripts\python -m brief.serve                 :: 대시보드 (포트: 환경변수 BRIEF_PORT, 기본 8765)
.venv\Scripts\python -m brief.run --date 2026-09-16 :: 대시보드 없이 바로 생성
.venv\Scripts\python -m brief.run --dry-run         :: LLM·네트워크 없이 픽스처로 HTML 생성(레이아웃 확인용)
.venv\Scripts\python scripts\probe.py               :: 소스 접근 진단
.venv\Scripts\python -m pytest -q
```
모델: 모든 단계 기본 `opus` (환경변수 `BRIEF_MODEL`, 단계별로는 `STAGE1_MODEL`/`STAGE2_MODEL`).

---

## 3. 문제가 생기면 (런북)

| 증상 | 확인 | 조치 |
|------|------|------|
| 대시보드가 안 열림 | `.bat` 콘솔에 `.venv 가 없습니다` | §1 2번 다시 실행 |
| 생성 버튼 → 오류 `Claude Code CLI를 찾을 수 없음` | 터미널 `claude --version` | Claude Code 설치, PATH 확인. 필요하면 환경변수 `CLAUDE_BIN` 에 전체 경로 |
| 로그에 `Not logged in` | `claude` 실행 후 `/login` | 로그인 후 다시 생성 |
| 로그에 `rate limit` / 사용량 한도 | 구독 사용량 창(5시간) 소진 | 시간 지나서 재시도 |
| 빨간 "보고서 생성 실패" 배너 | `docs/status/YYYY-MM-DD.json` 의 `error`, 대시보드 로그 | 일시 오류면 다시 생성. 같은 오류 반복이면 로그를 이슈로 |
| "누락 소스: …" 경고 배너 | `status.json` 의 `sources[].error` | 개별 소스 장애는 무시 가능. 더벨이 계속 누락이면 `scripts\probe.py` 로 `news.google.com` 접속 확인(사내망 차단 등) |
| 시그널 표 "데이터 없음" | Yahoo 차트 API 429/장애 | 보고서는 그대로 유효, 다음 생성 때 재시도 |
| [GitHub에 게시] 실패 | 출력의 git 메시지 | 자격 증명(`git push` 가 터미널에서 되는지) 확인, 충돌이면 `git pull --rebase` 후 재시도 |

`docs/status/<날짜>.json` 에 소스별 성공/실패, 단계별 토큰(참고용 추정 비용, 청구 없음), 경고가 남습니다.

---

## 4. 소스 정책

- **무료 공개 소스만**: RSS / 공식 보도자료 피드 / Google News RSS / Yahoo 차트 JSON. 유료 API·유료 벽 우회 없음. 목록은 `brief/sources.yaml`.
- **더벨(thebell.co.kr)은 직접 접근하지 않습니다.** `robots.txt`가 검색봇 외 전면 차단이므로 Google News RSS(`site:thebell.co.kr`)로 제목·링크·발행시각만 수집합니다. 코드가 `thebell.co.kr` 호스트를 호출하지 않는지 테스트로 강제합니다.
- 어댑터 타입은 `rss` / `google_news` / `yahoo_chart` 화이트리스트로 제한합니다(페이지 크롤링 없음). **새로 페이지 크롤링 타입을 추가할 때는 대상 사이트의 `robots.txt`와 이용약관을 반드시 확인하고 이 절에 근거를 남겨야 합니다.**

## 5. 구조

```
시장브리핑 대시보드.lnk/.bat   → .venv\Scripts\python -m brief.serve
brief/serve.py                로컬 HTTP 서버(127.0.0.1:8765): 대시보드, /api/generate, /api/status, /api/publish, /docs/*
brief/run.py                  오케스트레이션: collect → window/dedupe → stage1 → select → stage2 → render (데드라인·폴백)
brief/collect/                rss / google_news / yahoo_chart 어댑터
brief/window.py, dedupe.py    수집 윈도(직전 영업일 06:50 KST) + 중복 제거
brief/analyze/                Claude Code CLI 엔진(client.py), stage1 태깅, 쿼터 select, stage2 본문, 스키마
brief/render/                 Jinja 템플릿(report/archive/empty/dashboard) → docs/
docs/                         생성 결과 (index.html = 최신, reports/, status/, archive.html). GitHub Pages 루트(.nojekyll)
```
