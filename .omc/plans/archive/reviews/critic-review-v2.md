# Critic Review v2 (2026-09-15)

- 대상: 계획 v2 (iteration 2). 입력: 계획 v2 + Critic v1 + 스펙. API 사양은 `claude-api` 스킬 문서로 대조.

## Verdict: APPROVE (조건부 — "결론"의 필수 패치 2건을 실행 전 계획 본문에 반영)

**총평**: v1의 Critical 2·Major 8·Minor 12건이 모두 검증 가능한 형태로 반영됐고 산정표는 현행 단가와 일치. 새 Major 1건(stage2가 pydantic 원시 스키마를 `output_config.format`에 직접 전달 → API 제약 위반)은 한 줄 수정으로 해소되며 Verification 4에서 즉시 드러나므로 재검토 루프 재가동 사안 아님. 조건부 승인.

## v1 이슈 해소 확인표

| # | 상태 | 근거 |
|---|------|------|
| C-1 | 해소 | §3 LLM 호출 설계(16k, 압축 필드, `messages.parse`) |
| C-2 | 해소 | §3 수집 윈도 "직전 영업일 06:50 KST", Step 2 월요일 72h 테스트, `asof` 배지 |
| M-1 | 해소 | `max_retries=0`, 캡 2/3, `effort: medium`, 잔여시간 timeout |
| M-2 | 해소 | Step 5 `try/except BaseException` + 14분, `if: always()`, degrade, `empty.html.j2` |
| M-3 | 해소 | KST 규칙, tz 보정, 21:50Z 테스트, `TZ=Asia/Seoul date` |
| M-4 | 해소 | Step 0.5 게이트, "러너 결과" 열 |
| M-5 | 해소 | 시간 예산표, 이중 cron, `.nojekyll`, AC-15 URL curl, 드롭 행 |
| M-6 | 해소(주의) | `select.py` 쿼터, AC-6 ≥300 + 캡 후 동작. `is_ai 전부` 상한 없음(새 Minor 3) |
| M-7 | 해소 | 시그널 표 직접 렌더 + `%` 대조, 금지 문구, leaders 스키마 |
| M-8 | 해소 | `select_autoescape` + XSS 테스트 |
| Minor 1~12 | 전부 해소 | — |

## AC 커버리지: AC-1~19 전부 OK, 잔여 GAP 없음 (AC-6은 Major 1 수정 전제).

## 산정표 검증
- 단가 일치. stage1 $0.124 / Haiku $0.062 / stage2 $0.144 / happy $0.264 / 캡 $0.66 / 절대 $1.03 / 월 $5.8(캡 3일 $7.0) ✓.
- 한국어 입력은 200~250tok/건 → stage1 입력 ~45~55k, +$0.03~0.05/회. 결론 불변.
- worst 시간 합산 32.5분(계획 31), 데드라인 기준 34분 → 여유 66/21분. 결론 불변.
- 구조화 출력 스키마 컴파일 캐시 24h → 1일 1회 실행은 매번 컴파일 지연(수초~수십초) 가능. 여유 내.

## 새 이슈

**Major**
1. **stage2가 `Report.model_json_schema()` 원시 스키마를 `output_config.format.schema`에 직접 전달.** pydantic은 `minLength`·`minItems/maxItems` 방출, `additionalProperties:false` 기본 미방출. 구조화 출력 API는 이를 미지원/요구 → 매 호출 400 → 캡 소진 → **매일 AC-18 폴백**. pytest(모킹)로는 안 잡히고 Verification 4에서 처음 드러남.
   - Fix: `client.messages.stream(..., output_format=Report)` → `get_final_message().parsed_output`. 검증(≥300자, leaders 1..5, US 필드)은 pydantic validator로 클라이언트 측 강제. stage1도 `output_format=Stage1Result`.

**Minor**
1. 주 cron에 `--skip-if-done` 없음 → 주 cron이 백업 뒤에 큐잉되면 2회 과금. 두 `schedule` 트리거 모두 `--skip-if-done`, `workflow_dispatch`만 강제.
2. Step 5 skip 판정 문구 불일치(`success` vs `success|degraded`) → `result in ("success","degraded")`로 통일.
3. `is_ai 전부` 상한 → importance 내림차순 ≤15.
4. 폴백 배너 "아래는 <전일 날짜> 보고서" — 연속 실패 시 로드한 `index.html`의 `data-generated` 값 사용. 연속 2회 실패 테스트에 날짜 assert.
5. publish 루프 시작에 `git rebase --abort 2>/dev/null || true`.
6. stage1 출력 최상위 배열 → `Stage1Result{items: list[...]}` 객체 래핑.
7. 스키마 컴파일 지연 산정표 주석.
8. stage1 입력 토큰 45~55k로 갱신.
9. 월요일: 피드 보유량(CNBC 30, MW 10)이 금요일 기사를 이미 밀어냈을 수 있음. Google News 100건이 완충. Step 7 체크리스트에 "첫 월요일 US 기사 중 금요일 발행분 수 확인" 추가.

**동시성·멱등 검토(문제 없음)**: `concurrency: brief, cancel-in-progress: false`로 직렬화, 백업 checkout은 주 push 이후 → `status/{date}.json` 판독 정확. stale JS `data-failed-on` 조건은 서버 배너와 중첩 없음.

## 결론 (APPROVE 조건 — planner 인라인 패치, 별도 합의 라운드 불필요)
1. **필수** — Major 1: `output_format=<pydantic 모델>` 방식 고정, "원시 `model_json_schema()` 전달 금지" 사유 기록, 테스트에 `output_format` 인자 사용 assert.
2. **필수** — Minor 1·2: 두 `schedule` 모두 `--skip-if-done`, skip 판정 `success|degraded` 통일.
3. 권고 — Minor 3~9는 실행 중 반영 가능.

Open Questions(비채점): (a) 더벨 Google News 러너 429 여부는 Step 0.5 결과에 달림 — 게이트 있음. (b) stage1 `thinking: disabled` vs `adaptive + effort: low` — 첫 주 출력 품질로 판단.
