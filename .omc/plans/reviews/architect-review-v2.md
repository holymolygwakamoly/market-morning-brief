# Architect Review v2 (2026-09-15)

## 판정: APPROVE_WITH_IMPROVEMENTS

v1 지적 11건 + 갭 10건은 모두 계획 본문에 실제 설계로 녹아 있음. 다만 stage2의 구조화 출력 호출 형태(`output_config.format.schema=Report.model_json_schema()`)는 현재 API 제약과 충돌해 **매 실행 400 → 3회 캡 소진 → 매일 폴백**이 될 수 있는 실장 결함이므로 Step 3 착수 전 수정 필요. 산정표는 반올림 누적 오차 외 방향은 맞음.

## v1 지적 반영 확인표

| # | 항목 | 판정 | 근거 |
|---|------|------|------|
| 1 | stage1 max_tokens 16k | 반영됨 | L88 + 압축 필드(L91) |
| 2 | stage2 16k+스트리밍+effort, 모델 ID | 반영됨(신규 문제 1 참조) | L86~88 |
| 3 | 이중 cron 멱등 | 반영됨 | L60~61, L222 `--skip-if-done`, L230~232 |
| 4 | 클라이언트 stale 배너 | 반영됨 | L72, L214 |
| 5 | 러너 프로브 선행 | 반영됨 | Step 0.5, 더벨 FAIL 시 Step 1 차단 |
| 6 | KST 날짜 처리 | 반영됨 | L78~79, L226, L235 |
| 7 | rebase/identity/.nojekyll | 반영됨 | L174, L232~237 |
| 8 | stage1 Sonnet 5 + 구조화 출력 | 반영됨 | L52, L85~86 |
| 9 | SDK 재시도 중첩 제거 | 반영됨 | L89~90 |
| 10 | 접미사/dedupe/story_key | 반영됨 | L188, L197, L206 |
| 11 | deploy-pages 분리 | 반영됨(선택) | L239, L275 |
| 갭 | tz / 수치 금지 / AC-9 / lock+cache / asof / AC-6 | 반영됨 | AC-9 자동 감축은 삭제 — Principle 4와 일관, 수용 |
| Steelman | Workers Cron 트리거 | 반영됨 | L45 평가·기각 사유 |

## 산정표 검증 (단가 Sonnet 5 $2/$10, Haiku 4.5 $1/$5 — 공식 단가 일치)

| 항목 | 계획값 | 재계산 | 판정 |
|------|--------|--------|------|
| stage1 Sonnet | $0.12 | $0.124 | OK |
| stage1 Haiku | $0.06 | $0.062 | OK |
| stage2 | $0.14 | $0.144 | OK |
| happy 합계 | $0.26 / ₩360 | **$0.268 / ₩375** | 정정 |
| 캡 도달 | $0.66 | **$0.68** | 정정 |
| 절대 상한 | $1.03 | $1.03 | OK |
| 월간 22회 | $5.7/₩8,000 | **$5.9/₩8,260**; 캡 3일 포함 **$7.1/₩9,980** | 정정(예산 내) |
| worst 시간 합계 | 31분 | **32.5분**; 내부 데드라인 14분 꽉 채우면 **34분** | 정정 |
| cron 지연 허용치 | 69/24분 | **66분 / 21분** | 정정(여전히 안전) |

추가: stage1 입력 150tok/건은 영문 기준. 한국어 250자 ≈ 200~250tok → KR 절반이면 입력 ~40~45k, +$0.02/회. 표에 주석 권고.

## 새로 발견된 문제

1. **[높음] stage2 raw 스키마 전달 → API 400 가능성** (L86, L207). 구조화 출력은 `minLength`/`maxLength`/`minItems`/`maxItems` 미지원, 모든 object에 `additionalProperties:false` 요구. `Report.model_json_schema()`는 AC-6 `min_length=300`, leaders 1..5를 그대로 내보냄. SDK가 제약을 자동 제거·클라이언트 검증하는 경로는 **`output_format=Model` 헬퍼**뿐. 결과: 매 호출 400 → 캡 소진 → 매일 폴백. **정정**: `client.messages.stream(model=..., max_tokens=16000, thinking={"type":"adaptive"}, output_config={"effort":"medium"}, output_format=Report)` → `get_final_message().parsed_output`. `output_format`에는 min_length 없는 `ReportOut` 사용, 300자 검사는 앱 코드에서.
2. **[중] stage1 출력 스키마가 top-level 배열** (L91). JSON 출력은 object 루트 필요 → `Stage1Result{tags: list[Tag]}`. 두 모델 모두 `model_config = ConfigDict(extra="forbid")`.
3. **[중] 러너 timeout kill 시 배너 부재** (L223, L231). `timeout-minutes: 20` 발동 시 SIGKILL로 `except BaseException` 미실행. 마진 3분(pip 캐시 미스 시 초과 가능). 정정: publish 스텝(`if: always()`) 앞에 `[ -f docs/status/$DATE.json ] || python -m brief.fallback --reason runner_killed`.
4. **[낮]** publish 스크립트 `exit 0`이 서브셸 안(L235) → `if git diff --cached --quiet; then …; else git commit …; fi`.
5. **[낮]** concurrency pending 슬롯 1개 — 주 실행 중 백업 대기 시 수동 dispatch가 백업을 취소. 결과 동일, Step 7 체크리스트에 주석.
6. **[낮]** stage1 degrade 시 `market` 출처 미명시 → `Article.category`로 대체 한 줄.
7. **[낮]** stage2 max_tokens 24k로 상향 가능(절대 상한 $1.25, 예산 내). 선택.
8. **[정보]** Sonnet 5 `thinking: disabled` 허용, `effort`는 `output_config` 내부, Haiku 4.5는 effort 400·thinking은 budget_tokens 필요 → L87 규칙 정확.

## 승인 조건
- 문제 1·2를 §3 LLM 호출 설계표와 Step 3에 반영(`output_format=Model`, object 루트, `extra="forbid"`, 300자 앱 측 검사).
- 문제 3의 킬 경로 폴백 한 줄을 Step 6 publish 스텝에 추가.
- 산정표 수치 정정($0.268/$0.68/$5.9·$7.1, worst 32.5~34분, 허용치 66/21분).
- 4~7은 선택. 위 세 가지 반영 시 APPROVE.
