# HANDOFF — 시장 아침 브리핑 자동 생성기 (Market Morning Brief)

> **이 파일의 목적**: Claude Code 세션이 끊겨도 이 파일 하나만 읽으면 지금까지의 모든 대화 맥락·결정·진행 단계를 그대로 이어갈 수 있게 하는 인수인계 문서.
> 마지막 갱신: 2026-09-17 (수), 세션 3 — **v3 사용자 첫 실행 확인(09-15 14:21 opus, 12/12 소스, 6분27초). 결정 14~18: v4 "리서치 대시보드" 재구성 계획 확정(PLAN.md §11). 코딩은 아직 시작 안 함 — 사용자의 "구현 시작" 승인 대기.**
> 사용자 불러오기 명령: **`HANDOFF.md 읽고 이어서 진행해줘`**
> **새 세션은 이 파일 + 루트 `PLAN.md` 두 개만 읽으면 전체 맥락이 복원된다.** (`PLAN.md` = `.omc/plans/market-morning-brief-plan.md`의 루트 사본, 내용 동일. 계획을 고치면 두 파일 모두 갱신할 것 — ralph/omc 스킬은 `.omc/plans/` 경로를 읽는다.)

---

## 1. 지금 어디까지 왔나 (현재 단계)

```
[완료] 1~5. 딥 인터뷰 → 스펙 → 소스 테스트 → 계획 v1 → Architect/Critic 합의 → 계획 v2.1 확정
[완료] 6.   실행 경로 ralph 승인
[완료] 7.   v2.1 구현 (Step 0~6): 수집·윈도·dedupe·분석·렌더·run.py·워크플로, Architect 코드 검증 반영
[완료] 8.   결정 12 → v3 로컬 대시보드 모드로 전환 구현: Claude Code CLI 엔진, brief.serve 대시보드, 바로가기, GitHub 게시 버튼, cron/API 제거
[완료] 9.   결정 13: 모든 단계 모델 opus
[완료] 10.  사용자 대시보드 첫 생성(US-008): 2026-09-15 14:21~14:27 KST, opus, 12/12 소스, 166건, stage1·stage2 각 1회. result=degraded인데 이유는 `cost_over_soft_cap`뿐(추정 $1.16, billed:false) → 구독 모드에선 무의미한 경고. **미수정**(v4에서 처리)
[대기]  11.  (선택) GitHub Pages 설정(main //docs) 후 [GitHub에 게시] 버튼 확인
[완료] 12.  결정 14~18: v4 리서치 대시보드 계획(PLAN.md §11) 확정(질문 4개 답변 반영)
[대기]  13.  사용자 구현 승인 → Step A(소스 프로브: nasdaq 스크리너·KRX·신규 RSS/Google News) → B~F ralph
```

**v4 한 줄**: 좌측 탭 7개(홈/거시경제/섹터별 주요뉴스/미국/유럽/한국/중국) + 홈 지수 카드·핵심 5(**미국·한국 지수만** 클릭 → 구성종목 등락폭/거래대금/시가총액 순위) + 보고서 6종(opus 각 1회, 병렬 3, 탭별 재생성) + 기준일 스냅샷(`docs/data/YYYY-MM-DD/`) + **업데이트는 당일 KST·하루 1회만**, 과거 기준일은 저장분 로드. 기존 "오늘~3일 전 생성"·단일 보고서는 폐기 예정.

**지금 쓰는 방법**: 폴더의 `시장브리핑 대시보드.lnk` 더블클릭 → 브라우저 `http://127.0.0.1:8765/` → 날짜(오늘~3일 전) → [보고서 생성] → 8~12분 → [보고서 열기]. 결과는 `docs/reports/YYYY-MM-DD.html`, `docs/index.html`(최신), `docs/status/YYYY-MM-DD.json`(소스·토큰·경고).

**코드 상태 (2026-09-15 14:30)**: 커밋 `7cf7c60`까지 push됨(`https://github.com/holymolygwakamoly/market-morning-brief`, main). 테스트 119개 통과(`.venv/Scripts/python -m pytest -q`). `python -m brief.run --dry-run` E2E OK.

**주요 구현 사실 (코드 안 읽어도 되게)**:
- 파이프라인: `brief/run.py` — collect(12 소스, ThreadPool, 3분 예산) → window(직전 영업일 06:50 KST~) → dedupe(소스당 40, 총 200) → stage1 태깅(opus, 호출 ≤2, 480s 예산) → select(쿼터 US25/KR25/MACRO10 + AI≤15 + 더벨≤30) → stage2 본문(opus, 호출 ≤3, 480s 예산, ai_sector≥300자 검증) → render. 내부 데드라인 30분. 어떤 예외든 error 배너 + exit 0.
- LLM 엔진 `brief/analyze/client.py`: `claude -p --model <m> --no-session-persistence --tools "" --output-format json --json-schema <pydantic schema> --system-prompt <sys>` (user 프롬프트는 stdin). `--bare` 금지(로그인 정보 안 읽음). 자식 env에서 `CLAUDE*` 변수 제거. 결과 JSON `structured_output` → pydantic. 비용은 CLI 추정치만 기록(`billed: false`, 청구 없음).
- 모델: `BRIEF_MODEL`(기본 opus) = stage1·stage2. 단계별 `STAGE1_MODEL`/`STAGE2_MODEL`.
- 대시보드 `brief/serve.py`: stdlib http.server 127.0.0.1:8765(`BRIEF_PORT`). `GET /`, `POST /api/generate {date}`(오늘−3~오늘 KST 아니면 400, 실행 중 409), `GET /api/status`(stage·log tail·result·recent), `POST /api/publish`(git add docs → commit → pull --rebase → push ×3), `GET /docs/*`(경로 탈출 차단). 포트 사용 중이면 브라우저만 열고 종료.
- 런처: `시장브리핑 대시보드.bat`(ASCII만 — 한글 넣으면 cmd가 멈춤) + `.lnk`(`scripts/make_shortcut.ps1`로 재생성 가능).
- 렌더: `index.html` = reports 중 **최신 날짜**(과거 날짜 생성이 덮지 않음). 시장 시그널 표는 Yahoo Quote 직접 렌더(일간 등락률 = `regularMarketChangePercent`). 수치 대조 경고, Jinja autoescape, 클라이언트 stale 배너.
- 첫 실제 실행 관측(sonnet, 13:43 KST): 12/12 소스 OK(더벨 Google News 포함 — 오전엔 로컬 네트워크에서 news.google.com 접속 불가였다가 회복), 기사 167건, stage1 sonnet 180s×2 타임아웃 → 이후 예산 480s로 상향, stage2 2분40초·13.6k 토큰, 보고서 39KB·섹터 17개·AI 섹터 645자.

## 1-1. 합의 루프에서 나온 핵심 판단 (리뷰 파일 안 읽어도 되게 요약)

- **Critic v1 (REVISE)**: stage1 `max_tokens 4k`로 200건 태깅 JSON 불가(결정론적 절단) / 24h 필터가 월요일에 금요일 미국장 마감 뉴스를 전부 버림 / thinking 토큰·재시도 중첩으로 5분·$0.50 초과 / stage2 외 실패 시 전일 페이지가 마커 없이 노출 / UTC 러너에서 KST 날짜 오류 / Pages 빌드+CDN 캐시 빠져 "60분 여유"는 실제 ~30분 / stage2 입력 카테고리 편중·AI 기사 미도달 / 수치 환각 가드 없음 / Jinja autoescape 미명시.
- **Architect v1 (APPROVE_WITH_IMPROVEMENTS)**: 위와 같은 지적 + 이중 cron(06:50/07:35) 제안, 클라이언트 stale 배너, 러너 프로브 선행, stage1도 Sonnet 5, 구조화 출력, `story_key`, Google News 제목 접미사 제거.
- **v2**: 전부 반영. 산정표 신설(happy ≈ $0.27/회, 캡 ≈ $0.68, 월 ₩8~10k; cron→URL worst ≈ 34분 → 주 cron 지연 허용 66분, 백업 21분).
- **Architect v2 / Critic v2 (APPROVE)**: 구조화 출력에 원시 `model_json_schema()`를 넣으면 `minLength/minItems/additionalProperties` 제약으로 400 → 매일 폴백. **SDK `output_format=<pydantic 모델>` 헬퍼만 사용**, 전달용/검증용 모델 분리. 주 cron에도 `--skip-if-done`. 러너 SIGKILL 시 배너 누락 → publish 전 `if: always()` 폴백 step. → 모두 v2.1에 반영, 합의 종료.
- **열린 관찰(비차단)**: 더벨 Google News가 Azure 러너에서 429 날지는 Step 0.5에서만 확인 가능 / stage1 `thinking: disabled` vs `adaptive+effort low`는 첫 주 품질로 판단 / 월요일엔 피드 보유량 때문에 금요일 기사가 이미 밀려났을 수 있음(Google News 100건이 완충, 첫 월요일 확인).

## 2. 프로젝트 한 줄 요약

(v3) 사용자가 폴더의 바로가기로 로컬 대시보드를 열고 날짜(오늘 KST~3일 전)를 골라 [보고서 생성]을 누르면 → 미국·한국 증시 뉴스 + 거시경제 이슈를 무료 공개 소스에서 수집 → **Claude Code CLI(Max 구독, 결제 0, 모델 opus)** 로 **섹터 중심** 매매 참고 해석 생성 → **핵심 요약 / 상세 분석 / 참고 자료** 3부 구성 한국어 HTML 보고서를 `docs/`에 생성(선택: [GitHub에 게시]로 GitHub Pages 공개 URL).
(v2.1 원안은 GitHub Actions cron 06:50 KST + Claude API 자동 게시였으나 결정 12로 철회 — 사용자가 API 결제를 원치 않음.)

---

## 3. 사용자가 내린 모든 결정 (시간순)

| # | 질문 | 사용자 답변 (원문 요지) | 결정된 사항 |
|---|------|------------------------|------------|
| 0 | 컴포넌트 구조가 [수집 / 분석 / 보고서 / 스케줄·전달] 4개 맞나? | "맞아요, 이대로 진행" | 4개 컴포넌트 모두 범위 포함 |
| 1 | "나한테 알려준다"가 어떤 모습? | "웹 대시보드/페이지" | 푸시 알림 없음. 웹 페이지 갱신 방식 |
| 2 | 보고서 읽고 뭘 하려는가? | "오늘 매매 판단에 참고" | 단순 요약 아님. 섹터/자산 영향 해석 + 당일 주목 이벤트 필요 |
| 3 | 뉴스 소스와 비용 상한? | "무료 공개 소스만, LLM 비용만 지불" | 유료 API·유료 호스팅 금지. LLM 비용 월 1~2만 원 목표 |
| 4 | "3장"의 각 장에 뭐가 있나? | "우선순위별: 핵심 요약 → 상세 분석 → 참고 자료" | 1부 핵심 5개+시장 시그널 / 2부 뉴스·섹터 상세 해석 / 3부 지표 테이블+원문 링크 |
| 5 | 한국장 09:00 개장인데 "9시"가 맞나? | "컴퓨터를 8시 40분에 켤 수 있어(출근 시간). 그래서 9시라고 한 거야. PC 꺼져 있어도 만들 수 있으면 08:30 전 완료(1순위), 안 되면 PC 켜지자마자, 그것도 안 되면 9시" | **클라우드 실행 채택** → PC 무관, 08:30 KST 이전 완료. 로컬 실행은 백업 |
| 6 | 실제 매매 대상은? (단순화 질문) | "미국·한국 둘 다. 주로 **섹터별 설명** 많이, 개별 주식은 적당히. 짧으면 안 되고 길어져도 상관없음. 섹터 비중 > 개별 주식. 모든 섹터 불필요 — **이슈 있는 섹터, 상승장 섹터, 하락장 섹터, 그 이유, 주도 대표주 몇 개**. **AI 관련은 항상 넣어줘**" | 분석 구조 확정. "3장"은 3부 구성이지 분량 제한 아님 |
| 7 | 페이지 누가 봐도 되나? | "URL을 아는 사람은 봐도 됨" | 공개 정적 호스팅(GitHub Pages). 인증 불필요 |
| 8 | 소스 절반 실패/LLM 실패 시 08:40에 뭘 보고 싶나? | "1번(부분 보고서 발행 + 누락 소스 명시). 접근 가능한 건 다 크롤링해서 정확하게. **더벨은 꼭 한 번씩 크롤링**. 내용이 무조건 들어갈 필요는 없고 보고서에 넣을지는 너가 판단" | 부분 발행 + 누락 배너. LLM 실패 시 재시도 후 전일 보고서 유지 + 실패 표시. 더벨 매 실행 필수 확인 |
| 9 | 스펙 완성 후 진행 방식? | "omc-plan 합의 정제 (Recommended)" | Planner/Architect/Critic 합의 계획 → 별도 실행 승인 |
| 10 | 더벨 robots.txt가 검색봇 외 전면 `Disallow: /`. 직접 크롤링은 정책 위반. Google News RSS로 헤드라인만 가능한데? | "1번으로 해줘 (Google News RSS로 더벨 헤드라인만)" + 이 HANDOFF 파일 요청 | **더벨 직접 크롤링 금지.** `https://news.google.com/rss/search?q=site:thebell.co.kr&hl=ko&gl=KR&ceid=KR:ko` 로 제목·링크·발행시각만 수집. LLM이 제목 기준 중요도 판단 |
| 11 | 계획 v2.1을 어떤 방식으로 구현? (ralph / team / autopilot) | "ralph로 해줘. 그 전에 토큰이 떨어질 수 있으니 HANDOFF.md + PLAN.md 두 개만 읽어도 맥락 전부 복원되게 저장해줘" | **실행 경로 = ralph.** 루트 `PLAN.md` 생성(계획 사본). 세션 재개 시 두 파일만 읽고 ralph 이어서 실행 |
| 12 | (Step 6까지 구현 후) API 결제 없이 갈 수 없나? | "API 호출은 결제가 필요하잖아. 이미 Pro/Max 구독에 10만 원 넘게 씀. 폴더 안 대시보드 바로가기 아이콘 → 날짜 지정(한국시간 오늘 기준 3일 전까지만) → [오늘 보고서 생성] 버튼 → 자동 생성되게. 추가 결제 없이" + 질문 답: "cron 끄되 GitHub Pages 게시 버튼 유지" / "LLM은 Claude Code CLI로 교체, API 코드 제거" | **v3 로컬 대시보드 모드.** LLM = `claude -p --json-schema`(Max 구독, 결제 0). GitHub Actions cron·API 키·anthropic SDK 제거. 로컬 서버(`python -m brief.serve`) + 대시보드(날짜 오늘~3일 전, 생성 버튼, 진행 상태, 과거 목록, [GitHub에 게시] 버튼). 폴더에 바로가기(.bat/.lnk). **"PC 꺼져 있어도 08:30 전 자동" 요구는 사용자가 비용 우선으로 철회**(출근 후 버튼 클릭, 3~5분 소요). PLAN.md §10 참조 |
| 13 | 태깅(stage1)에 haiku를 쓰는 게 맞나? | "stage1도 앞으로 opus 써줘. 저 보고서를 쓸 때 쓰는 모든 토큰은 다 opus 써줘" | **모든 LLM 단계 기본 opus**(`BRIEF_MODEL=opus`). stage1 예산 480s, 내부 데드라인 30분 |
| 14 | (2026-09-17, 사용자 발의) 구성을 바꾸자 — 코딩 말고 계획만 | "지금은 불편해. 왼쪽 탭: 홈/거시경제/섹터별 주요뉴스/미국시장/유럽시장/한국시장/중국시장. 탭마다 세세한 보고서. 이름은 **리서치 대시보드**. 홈엔 전날 지수(S&P500·나스닥·나스닥100·유럽·중국·한국). 지수 클릭 → 등락폭/거래대금/시가총액 탭별 내림차순, 모든 지수. 각 시장 보고서는 흐름·수급·주요 뉴스·이슈 상세히. 섹터별 주요뉴스엔 세계 주요 뉴스(전쟁 재개 → 방산). 맨 위 기준일 + 업데이트 버튼. 과거 기준일은 저장분 자동 로드. **업데이트는 당일만**(9/17 기준은 9/17에만, 지나면 불러올 내역도 없음)" | **v4 계획 = PLAN.md §11**(미구현). Claude 판단: 구조 좋음. 리스크 4개 — 구성종목 순위는 무료 소스 한계(나스닥 종합 불가 → 나스닥100, 유럽·중국은 프로브 후 전체/상위100, 시총은 ETF 비중 대체) / 보고서 6개 → opus 6배(12~20분, worst 45분) / 수급은 한국(KRX)만 진짜 데이터 / 유럽·중국 뉴스 소스 신규 프로브. 질문 4개 → 결정 15~18 |
| 15 | 구성종목 순위 범위? (무료 소스로 나스닥 종합·유럽·중국은 어려움) | "미국·한국만 구성종목" | S&P500·나스닥100·다우30·코스피·코스닥만 구성종목 상세. 나머지 지수는 카드만 |
| 16 | [업데이트] 한 번에 6개 전부(12~20분)? | "한 번에 전부 + 탭별 재생성" | 병렬 3개 생성, 실패·불만 탭은 [재생성] |
| 17 | 홈에 "오늘의 핵심 5" 둘까? | "넣는다" | 6개 보고서 headlines에서 추림, 추가 호출 없음 |
| 18 | 같은 날 재업데이트? | "하루 1회만" | 완료된 날은 [업데이트] 비활성(409). 탭별 [재생성]만 허용 |

**Claude가 제시하고 사용자가 이의 없이 수용한 가정**: 보고서 언어 = 한국어 / LLM = Claude API(비용상 Sonnet급 기본) / 시간대 = KST.

---

## 4. 확정 요구사항 요약

> **v3 변경(결정 12·13)**: 아래 "시간/실행 환경/비용" 항목은 원안. 현재는 **로컬 수동 실행**(PC 켜고 버튼), **비용 0**(구독 CLI, opus), 호스팅은 로컬 `docs/` + 선택적 GitHub Pages 게시. 나머지(소스·분석·보고서·실패 처리·비목표)는 그대로 유효.

**시간**: 평일(KST 월~금) 07:30 시작, 08:30 이전 게시. 주말 미실행. 수동 즉시 실행 가능해야 함.
**실행 환경**: GitHub Actions 무료 티어(주 cron `50 21 * * 0-4` = KST 06:50 + 백업 `35 22 * * 0-4` = KST 07:35, 월~금; 스펙의 07:30에서 마감 안전을 위해 앞당김) + `workflow_dispatch`. PC 의존 없음.
**호스팅**: GitHub Pages 공개 URL. `index.html`=최신, `reports/YYYY-MM-DD.html`=아카이브(과거 보고서 접근 가능).
**비용**: LLM API만. 1회 실행 500~1,000원 이하 목표. 토큰 사용량 로그.
**소스**: 무료 공개(RSS/공식 사이트/무료 API/공개 페이지). robots.txt·이용약관 준수, 유료 벽 우회 금지. 최대한 광범위하게.
  - 미국 후보: CNBC RSS, MarketWatch RSS, Yahoo Finance RSS, Reuters 무료 RSS, Fed 보도자료 RSS, BLS/BEA 일정, 경제 캘린더
  - 한국 후보: 연합뉴스 경제 RSS, 한국경제/매일경제 RSS, 네이버 금융 뉴스(공개), 한국은행/금융위/금감원 보도자료, KRX
  - **더벨: Google News RSS `site:thebell.co.kr` 헤드라인만 (필수, 매 실행)**
  - 시세: yfinance 또는 stooq CSV
**분석**: 섹터 중심(이슈/상승/하락 섹터 + 이유 + 주도 대표주 1~5개). **AI 섹터 매일 필수.** 섹터 서술 > 개별 종목 서술. 미국 뉴스는 미국 관점 + 한국장 영향 관점 둘 다. 분량 상한 없음.
**보고서**: 한국어 HTML(데스크톱·모바일). 1부 핵심 요약(핵심 5개 + 시장 시그널: 지수/환율/금리/유가) 최상단 → 2부 상세 분석 → 3부 참고 자료(지표 테이블 + 수집 기사 원문 링크 + 소스별 성공/실패 목록).
**실패 처리**: 소스별 타임아웃·예외 격리. 일부 실패 → 부분 보고서 + 상단 누락 소스 배너. LLM 실패 → 지수 백오프 재시도 → 그래도 실패면 전일 보고서 유지 + "오늘 생성 실패 HH:MM" 표시.
**비목표**: 매매 추천/자동 매매, 장중 업데이트, 하루 2회, 이메일/메신저 알림, 로그인 대시보드, 유료 API, 더벨 직접 크롤링·본문 수집, 정확한 3페이지 분량.

인수 기준 19개(AC-1~AC-19)는 스펙 파일 참조.

---

## 5. 파일 위치

| 파일 | 용도 |
|------|------|
| `HANDOFF.md` (이 파일) | 세션 인수인계. 결정이 추가되면 3절 표와 1절 진행 단계를 갱신할 것 |
| `PLAN.md` (루트) | **확정 계획 v2.1 + §10 v3 변경표** — 새 세션은 HANDOFF.md + 이 파일만 읽으면 됨 |
| `README.md` | 사용자용 설치·사용법·런북(v3 기준) |
| `.omc/prd.json`, `.omc/progress.txt` | ralph PRD(US-000~012)·진행 로그 (gitignore) |
| `.omc/specs/deep-interview-market-morning-brief.md` | 딥 인터뷰 최종 스펙 (목표/제약/비목표/AC 19개/가정/기술 컨텍스트/온톨로지/전체 Q&A) |
| `.omc/state/deep-interview-state.json` | 인터뷰 상태(점수·토폴로지·온톨로지 스냅샷) |
| `.omc/plans/market-morning-brief-plan.md` | **계획 v2.1 (합의 확정본)** — RALPLAN-DR, 아키텍처, LLM 호출 설계, 토큰·비용·시간 산정표, 소스 표, AC 1~19, Step 0~7, 리스크, ADR, 리뷰 반영 매트릭스, Changelog |
| `.omc/plans/reviews/architect-review-v1.md` / `-v2.md` | Architect 검토 v1(APPROVE_WITH_IMPROVEMENTS) / v2(APPROVE_WITH_IMPROVEMENTS → 필수 3건 v2.1 반영) |
| `.omc/plans/reviews/critic-review-v1.md` / `-v2.md` | Critic 검토 v1(REVISE) / v2(조건부 APPROVE → 필수 2건 v2.1 반영) |
| `.omc/plans/reviews/plan-v1-snapshot.md` | 계획 v1 원본 스냅샷 |
| `.omc/state/ralplan-state.json` | 합의 루프 상태 — **active=false, consensus_reached** |

프로젝트 루트: `C:\Users\jack8\OneDrive\Desktop\project\Research` — git 초기화됨, 원격 `https://github.com/holymolygwakamoly/market-morning-brief.git` (main). 소스: `brief/`(collect, analyze, render, run.py, serve.py, fallback.py, sources.yaml, fixtures), `tests/`(119개), `scripts/`(probe.py, make_shortcut.ps1), `docs/`(생성 결과), `.venv/`(gitignore).

---

## 6. 다음 세션의 Claude에게

- 사용자는 한국어로 대화한다. 질문은 한 번에 하나씩. 토큰/컨텍스트 소모를 신경 쓰므로 이 문서를 근거로 바로 진행.
- **현재 단계: v4 계획 확정, 구현 미시작(사용자가 "코딩하지 말고 계획만"이라고 했음).** 시작하면 (1) `git status`/`git log -1`, (2) PLAN.md §11 읽기, (3) 사용자가 "구현 시작"이라고 하면 Step A(프로브)부터 ralph(PRD는 §11.7 Step A~F로 새로 작성). 구현 승인 없이는 코드를 건드리지 말 것. v3 코드(`brief/`)는 그대로 동작하므로 v4 완성 전까지 기존 대시보드 사용 가능.
- 되묻지 말 것: 더벨은 Google News 헤드라인만 / API 결제 없음(CLI 구독) / 모든 단계 opus / cron·Actions 없음 / v4: 업데이트 당일 KST·하루 1회, 과거 기준일은 저장분만, 구성종목은 미국·한국만, 보고서 6개 한 번에 + 탭별 재생성, 홈 핵심 5.
- 코드 변경 후에는 `.venv/Scripts/python -m pytest -q` → commit → `git push origin main`. 대시보드 서버는 config를 import 시점에 읽으므로 **설정 바꾸면 서버 재시작**(포트 8765 프로세스 종료 후 `.lnk` 실행).
- Claude Code 세션 안에서 `claude -p`를 직접 테스트할 땐 `env -u CLAUDECODE ...` 처럼 `CLAUDE*` 환경변수를 빼야 한다(코드는 이미 그렇게 함).
- 새 결정이 생기면 3절 표에 행 추가, 1절 갱신. 계획 변경은 PLAN.md §10 이어서 쓰고 `.omc/plans/market-morning-brief-plan.md`에 복사.
- 알려진 follow-up(우선순위 낮음): Yahoo 시세 병렬화, RSS bytes 파싱(인코딩), 미파싱 pubDate None 처리, 당일 success 뒤 수동 실패 시 배너 격하, href 스킴 화이트리스트, BOK 피드가 오래됨(MACRO는 사실상 Fed), stage1 opus가 느리면 배치 분할(2회 병렬) 고려.
