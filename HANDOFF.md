# HANDOFF — 시장 아침 브리핑 자동 생성기 (Market Morning Brief)

> **이 파일의 목적**: Claude Code 세션이 끊겨도 이 파일 하나만 읽으면 지금까지의 모든 대화 맥락·결정·진행 단계를 그대로 이어갈 수 있게 하는 인수인계 문서.
> 마지막 갱신: 2026-09-15 (월), 세션 2 — 합의 완료(계획 v2.1) + 실행 경로 ralph 승인. 다음: ralph 실행
> 사용자 불러오기 명령: **`HANDOFF.md 읽고 이어서 진행해줘`**
> **새 세션은 이 파일 + 루트 `PLAN.md` 두 개만 읽으면 전체 맥락이 복원된다.** (`PLAN.md` = `.omc/plans/market-morning-brief-plan.md`의 루트 사본, 내용 동일. 계획을 고치면 두 파일 모두 갱신할 것 — ralph/omc 스킬은 `.omc/plans/` 경로를 읽는다.)

---

## 1. 지금 어디까지 왔나 (현재 단계)

```
[완료] 1. 딥 인터뷰 (8라운드, 모호도 15.4% ≤ 20% 임계값 통과)
[완료] 2. 스펙 작성 → .omc/specs/deep-interview-market-morning-brief.md
[완료] 3. 사용자가 "omc-plan 합의 정제" 선택
[완료] 4. 계획 전 사전 검증: 더벨 robots.txt 문제 → 사용자가 Google News RSS 대안(1번) 선택 → 스펙 반영
[완료] 5a. 소스 접근 테스트 14개 (결과는 계획서 §3 표) + Planner 계획 v1 작성 → .omc/plans/market-morning-brief-plan.md
[완료] 5b. Architect 검토 v1 → APPROVE_WITH_IMPROVEMENTS, 개선 11건 → .omc/plans/reviews/architect-review-v1.md
[완료] 5c. Critic 검토 v1 → REVISE (Critical 2: stage1 토큰 절단, 월요일 24h 필터로 금요일 US 마감 뉴스 유실 / Major 8 / Minor 12) → .omc/plans/reviews/critic-review-v1.md
[완료] 5d. Planner 계획 v2 → Architect v2 APPROVE_WITH_IMPROVEMENTS + Critic v2 조건부 APPROVE → 필수 패치를 인라인 반영해 **계획 v2.1 확정** (Changelog v2.1 참조). ralplan-state active=false
[완료] 6. 사용자가 실행 경로 **ralph** 승인 (2026-09-15). 단, 시작 전 HANDOFF.md + PLAN.md 두 파일로 맥락 복원 가능하게 정리 요청 → 완료
[대기]   7. **ralph 실행** (Skill `oh-my-claudecode:ralph`, 계획 = PLAN.md/.omc/plans/market-morning-brief-plan.md) → Step 0(사용자 GitHub 작업) → Step 0.5 러너 프로브 → Step 1~7 → 검증 → GitHub Actions + Pages 배포
```

**다음 세션에서 바로 할 일**: 실행 승인은 이미 받았다(ralph). 되묻지 말고 **`Skill oh-my-claudecode:ralph`를 PLAN.md 기준으로 호출**해 구현을 시작/재개하라. 재개 시엔 프로젝트 루트에 어떤 파일이 생성돼 있는지(`brief/`, `tests/`, `.github/workflows/`, `docs/`) 먼저 확인해 어느 Step까지 됐는지 판단하고 이어서 진행. Step 순서: Step 0(사용자 작업: GitHub 공개 리포 생성 + `ANTHROPIC_API_KEY` 시크릿 + Pages Source=main //docs — 이건 사용자에게 요청) → Step 0.5 러너 프로브(더벨 Google News가 러너에서 FAIL이면 Step 1 차단하고 사용자와 상의) → Step 1~7. 각 Step의 "완료 기준"을 통과해야 다음 Step.

**구현 진행 상황 (ralph가 Step 완료 시마다 여기 갱신)**:
- Step 0: **완료** (2026-09-15, commit ebc7f6e — git init, pyproject, .venv, requirements.lock, README 런북, docs/.nojekyll). 남은 사용자 작업: GitHub 공개 리포 생성·push, `ANTHROPIC_API_KEY` 시크릿, Pages Source=main //docs (README §1 절차)
- Step 0.5: **파일 완료, 러너 실행 대기** — `brief/sources.yaml`, `scripts/probe.py`, `.github/workflows/probe.yml`. 로컬 프로브 17/20 OK(RSS 9·Yahoo 8 전부 OK, **news.google.com 3건 ConnectTimeout = 로컬 네트워크 문제**, 전일엔 성공). 사용자가 리포 만든 뒤 Actions → Source Probe 실행 → 더벨 행 OK/FAIL을 PLAN §3 표 "러너 결과" 열에 기록. FAIL이면 사용자와 상의.
- Step 1~2: **완료** (commit d8f28ef) — collect 어댑터, window(직전 영업일 06:50), dedupe
- Step 3: **완료** (commit 6d86154) — schemas(전달용/검증용 분리), LLMClient(호출 캡·데드라인), stage1/select/stage2
- Step 4: **완료** (commit 4bbbbc3) — 템플릿 3종 + 배너/stale JS, 수치 대조, fallback + `python -m brief.fallback`
- Step 5~6: **완료** (commit 다음 항목 참조) — run.py(최상위 BaseException → 배너 + exit 0, --skip-if-done, --dry-run), brief.yml(이중 cron, 킬 폴백, publish). 테스트 85개 통과, `python -m brief.run --dry-run` E2E OK
- Step 7: **대기** — (1) 사용자 GitHub 리포 생성·push·시크릿·Pages, (2) Source Probe 실행 → 더벨 OK/FAIL 기록, (3) 로컬 실제 실행 1회(API 키) → cost·ai_sector 확인, (4) workflow_dispatch 1회 → Pages URL 확인, (5) 첫 주 체크리스트
- ralph PRD: `.omc/prd.json`(US-000~008), 진행 로그 `.omc/progress.txt` (둘 다 gitignore)

**계획 v2.1 핵심 (구현 시 반드시 지킬 것)**:
- 이중 cron `50 21`(06:50 KST 주) + `35 22`(07:35 KST 백업), 두 schedule 모두 `--skip-if-done`(success|degraded면 비용 0 종료), `workflow_dispatch`만 강제 실행. `docs/.nojekyll`.
- LLM: stage1·stage2 모두 `claude-sonnet-5`(stage1은 `claude-haiku-4-5`로 다운그레이드 가능), max_tokens 16k, stage2 스트리밍 + `effort: medium` + adaptive thinking, stage1 thinking disabled. `Anthropic(max_retries=0)` + 자체 호출 캡(stage1 ≤2, stage2 ≤3) + 잔여시간 데드라인.
- **구조화 출력은 SDK `output_format=<pydantic 모델>` 헬퍼만** — 원시 `model_json_schema()`를 `output_config.format`에 넣으면 400. 전달용(`Stage1Result{items[]}`, `ReportOut`, `extra="forbid"`, 제약 없음) / 검증용(`Report` validator: ai_sector ≥300자, leaders 1..5 등) 모델 분리.
- 수집 윈도 = 직전 영업일 06:50 KST 이후(월요일 72h). KST 전면(`--date` 기본 Asia/Seoul, 커밋 메시지 `TZ=Asia/Seoul date +%F`).
- 실패 처리: run.py 최상위 `except BaseException` + 14분 내부 데드라인 → 어떤 경로든 error 배너 + exit 0; 워크플로 publish 전 `if: always()` 킬 폴백(`brief.fallback --reason runner_killed`); 클라이언트 stale 배너(`data-generated` + 인라인 JS); stage1 실패 시 degrade.
- 수치 환각 가드: 시장 시그널 표는 Quote에서 템플릿 직접 렌더, 프롬프트 "입력에 없는 수치 금지", 렌더 후 % 대조. Jinja autoescape.
- 비용 산정: happy ≈ $0.27/회, 캡 도달 ≈ $0.68, 월 ≈ $5.9~7.1 (₩8~10k).


## 1-1. 합의 루프에서 나온 핵심 판단 (리뷰 파일 안 읽어도 되게 요약)

- **Critic v1 (REVISE)**: stage1 `max_tokens 4k`로 200건 태깅 JSON 불가(결정론적 절단) / 24h 필터가 월요일에 금요일 미국장 마감 뉴스를 전부 버림 / thinking 토큰·재시도 중첩으로 5분·$0.50 초과 / stage2 외 실패 시 전일 페이지가 마커 없이 노출 / UTC 러너에서 KST 날짜 오류 / Pages 빌드+CDN 캐시 빠져 "60분 여유"는 실제 ~30분 / stage2 입력 카테고리 편중·AI 기사 미도달 / 수치 환각 가드 없음 / Jinja autoescape 미명시.
- **Architect v1 (APPROVE_WITH_IMPROVEMENTS)**: 위와 같은 지적 + 이중 cron(06:50/07:35) 제안, 클라이언트 stale 배너, 러너 프로브 선행, stage1도 Sonnet 5, 구조화 출력, `story_key`, Google News 제목 접미사 제거.
- **v2**: 전부 반영. 산정표 신설(happy ≈ $0.27/회, 캡 ≈ $0.68, 월 ₩8~10k; cron→URL worst ≈ 34분 → 주 cron 지연 허용 66분, 백업 21분).
- **Architect v2 / Critic v2 (APPROVE)**: 구조화 출력에 원시 `model_json_schema()`를 넣으면 `minLength/minItems/additionalProperties` 제약으로 400 → 매일 폴백. **SDK `output_format=<pydantic 모델>` 헬퍼만 사용**, 전달용/검증용 모델 분리. 주 cron에도 `--skip-if-done`. 러너 SIGKILL 시 배너 누락 → publish 전 `if: always()` 폴백 step. → 모두 v2.1에 반영, 합의 종료.
- **열린 관찰(비차단)**: 더벨 Google News가 Azure 러너에서 429 날지는 Step 0.5에서만 확인 가능 / stage1 `thinking: disabled` vs `adaptive+effort low`는 첫 주 품질로 판단 / 월요일엔 피드 보유량 때문에 금요일 기사가 이미 밀려났을 수 있음(Google News 100건이 완충, 첫 월요일 확인).

## 2. 프로젝트 한 줄 요약

평일마다 사용자 PC가 꺼져 있어도 클라우드(GitHub Actions)에서 07:30 KST에 자동 실행 → 미국·한국 증시 뉴스 + 거시경제 이슈를 무료 공개 소스에서 수집 → Claude API로 **섹터 중심** 매매 참고 해석 생성 → **핵심 요약 / 상세 분석 / 참고 자료** 3부 구성 한국어 HTML 보고서를 **08:30 KST 이전**에 공개 웹 페이지(GitHub Pages)로 게시.

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

**Claude가 제시하고 사용자가 이의 없이 수용한 가정**: 보고서 언어 = 한국어 / LLM = Claude API(비용상 Sonnet급 기본) / 시간대 = KST.

---

## 4. 확정 요구사항 요약

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
| `PLAN.md` (루트) | **확정 계획 v2.1 사본** — 새 세션은 HANDOFF.md + 이 파일만 읽으면 됨 |
| `.omc/specs/deep-interview-market-morning-brief.md` | 딥 인터뷰 최종 스펙 (목표/제약/비목표/AC 19개/가정/기술 컨텍스트/온톨로지/전체 Q&A) |
| `.omc/state/deep-interview-state.json` | 인터뷰 상태(점수·토폴로지·온톨로지 스냅샷) |
| `.omc/plans/market-morning-brief-plan.md` | **계획 v2.1 (합의 확정본)** — RALPLAN-DR, 아키텍처, LLM 호출 설계, 토큰·비용·시간 산정표, 소스 표, AC 1~19, Step 0~7, 리스크, ADR, 리뷰 반영 매트릭스, Changelog |
| `.omc/plans/reviews/architect-review-v1.md` / `-v2.md` | Architect 검토 v1(APPROVE_WITH_IMPROVEMENTS) / v2(APPROVE_WITH_IMPROVEMENTS → 필수 3건 v2.1 반영) |
| `.omc/plans/reviews/critic-review-v1.md` / `-v2.md` | Critic 검토 v1(REVISE) / v2(조건부 APPROVE → 필수 2건 v2.1 반영) |
| `.omc/plans/reviews/plan-v1-snapshot.md` | 계획 v1 원본 스냅샷 |
| `.omc/state/ralplan-state.json` | 합의 루프 상태 — **active=false, consensus_reached** |

프로젝트 루트: `C:\Users\jack8\OneDrive\Desktop\project\Research` (git 미초기화, 소스 코드 없음 — greenfield)

---

## 6. 다음 세션의 Claude에게

- 사용자는 한국어로 대화한다. 질문은 한 번에 하나씩.
- **실행 승인은 완료됨(ralph).** 계획은 PLAN.md. 세션 시작 시 되묻지 말고 구현 상태 확인 → ralph 재개. 단, Step 0의 GitHub 작업은 사용자만 할 수 있으니 필요한 시점에 한 번 요청.
- 계획과 다르게 가야 할 상황(예: 러너에서 더벨 FAIL, 소스 URL 변경)이 생기면 코드로 우회하지 말고 사용자에게 한 번 묻고 3절 표에 결정을 기록.
- 더벨 관련 결정(직접 크롤링 금지, Google News RSS 헤드라인만)은 사용자가 명시적으로 내린 것이니 되묻지 마라.
- 사용자는 토큰/컨텍스트 용량을 신경 쓰고 있다. 장황한 재확인 대신 이 문서를 근거로 바로 진행하라.
- 이 문서에 없는 새 결정이 생기면 3절 표에 행을 추가하고 1절 단계를 갱신하라.
