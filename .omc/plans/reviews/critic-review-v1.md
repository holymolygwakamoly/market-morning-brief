# Critic Review v1

- 대상: `.omc/plans/market-morning-brief-plan.md` v1
- 입력: 계획 v1 + 스펙만 (Architect 결과 미제공, 독립 검토)
- 일자: 2026-09-15

## Verdict: REVISE

**총평**: 아키텍처 선택(Actions cron + Python 2단계 LLM + Pages)은 타당하고 소스 검증·테스트 매핑도 평균 이상이다. 그러나 (1) stage1 `max_tokens 4k`는 200건 태깅 출력에 물리적으로 부족해 **매 실행 결정론적으로 실패**하고, (2) "최근 24h 필터"는 **매주 월요일 미국장 마감 뉴스를 전부 버리며**, (3) 타임박스·비용 계산에 Sonnet 5 기본 adaptive thinking 토큰과 재시도 상한이 빠져 있다. 세 가지 모두 실행 전에 계획 수준에서 고쳐야 하는 수치 오류이므로 REVISE.

---

## 심각도별 이슈

### Critical (계획 승인 차단)

**C-1. stage1 출력 토큰 사이징 오류 — 매 실행 실패**
- 위치: §5 Step 3 `stage1_tag.py … max_tokens 4k`, §2 L1 `출력 ≤ 8k 토큰`
- 문제: 출력 스키마 `[{id, importance 1-5, sectors[], market, is_ai}]`는 한국어 섹터명 포함 시 기사당 35~45 토큰. 200건 × 40 = **약 8k 토큰 > 4k**. 120건으로 감축해도 ~4.8k > 4k. `max_tokens` 도달 → JSON 잘림 → pydantic 실패 → 1회 재생성 → 같은 이유로 또 잘림. 결정론적 실패이며 재시도로 해결되지 않는다.
- 수정안: (a) stage1 `max_tokens` ≥ 16k로 상향, (b) 출력 포맷 압축(한 줄 per 기사 `id|imp|mkt|ai|sec1,sec2` 또는 섹터 코드표 정수) — 200건 기준 ~3k 토큰, (c) 구조화 출력(`output_config.format` + `client.messages.parse()` pydantic 연동)으로 JSON 깨짐 자체를 제거. 계획에 **단계별 예상 입력/출력 토큰 표**를 명시하고 AC-9 검증 항목에 포함.

**C-2. "최근 24h 필터"가 월요일 미국장 마감 뉴스를 전부 폐기**
- 위치: §3 `dedupe.py … 최근 24h 필터`, §5 Step 1 `rss.py … 24h 필터`
- 문제: 금요일 미국장 마감(ET 16:00~20:00) = 토 05:00~09:00 KST. 월 07:30 KST 실행의 24h 윈도 하한은 일 07:30 KST → 금요일 마감 뉴스·주간 마감 분석이 **전부 제외**. 스펙 Goal 첫 항목 "미국장 마감 뉴스"가 주 5회 중 1회 구조적으로 누락.
- 수정안: 윈도 하한을 "직전 영업일 07:30 KST"로 정의 — 월요일은 72h, 화~금은 24h. `window_start = last_business_day(run_date_kst) @ 07:30 KST`. 테스트: 월요일 `--date`로 금요일 22:00 KST 기사 포함 assert. Yahoo 시세도 월요일엔 "금요일 종가" 표기.

### Major (v2에서 반드시 수정)

**M-1. 타임박스·비용 계산에서 thinking 토큰과 호출 횟수 상한 누락**
- 위치: §3 `stage2 ≤ 5분`, §5 Step 3 `client.py … 재시도 3회`, `schemas.py … 1회 재생성`
- 문제: (a) Sonnet 5는 `thinking` 미지정 시 adaptive thinking 기본 ON, thinking 토큰은 출력 과금 + `max_tokens`에 포함 → 8k 중 수천을 thinking이 소비하면 본문 JSON 잘림. (b) stage2 1회 ≈ 100~160초. 재시도 3회 + 재생성 1회 + SDK `max_retries=2` 중첩 시 최악 4~9회 호출 → 5분·$0.50 모두 초과. (c) `총 ≤ 3분`은 백오프 합(65s)만 계산.
- 근거: 현행 단가(Sonnet 5 $2/$10, Haiku 4.5 $1/$5)로 재계산: happy path ≈ $0.26, 재생성 1회 ≈ $0.43, 3회 재시도 시 $0.6+.
- 수정안: stage2에 `output_config.effort: "low"|"medium"` 명시, thinking 정책 기록. stage별 총 호출 상한(stage1 ≤ 2, stage2 ≤ 3, 모든 재시도 합산), SDK `max_retries` 명시. 단계별 deadline은 잔여 시간 기반(부족하면 호출 스킵 → 폴백). max_tokens 16k + 스트리밍 `.get_final_message()`.

**M-2. stage2 이외 실패(크래시·타임아웃·pip 실패) 시 전일 페이지가 마커 없이 방치**
- 위치: §5 Step 5, Step 6 `timeout-minutes: 20`
- 문제: collect/dedupe/stage1 미처리 예외, 워크플로 타임아웃, 의존성 설치 실패 시 비정상 종료 → 커밋 미실행 → 전일 보고서가 표시 없이 노출. 사용자 결정("전일 유지 + 실패 마커") 위반. 알림 채널이 없으므로 이 마커가 유일한 실패 신호.
- 수정안: `run.py` 최상위 `try/except BaseException` + 내부 데드라인(14분)으로 어떤 경로든 `.banner-error` 삽입 후 exit 0. 워크플로 커밋 단계 `if: always()`. stage1 실패 시 degrade(태깅 없이 최신순+소스 쿼터로 stage2 입력). 첫 실행에 전일 `index.html` 없을 때 폴백(빈 템플릿 + 에러 배너).

**M-3. KST/UTC 날짜 처리 미명세**
- 위치: §5 Step 6 `date -u +%F`, Step 5 `--date`
- 문제: 러너 22:30 UTC에서 `date.today()`는 KST 전날. `--date` 기본값 규칙 없음, 커밋 메시지는 UTC 날짜. RSS `published` tz 정책 미명세.
- 수정안: 모든 날짜 `ZoneInfo("Asia/Seoul")`, `--date` 기본 = `now(KST).date()`, 커밋 `TZ=Asia/Seoul date +%F`. 기사 시각 UTC-aware 정규화, naive는 소스 설정 `tz`로 보정. 테스트: 22:30 UTC 고정 시각에서 report date = KST 당일.

**M-4. 러너 환경 소스 프로브가 Step 7로 밀려 있음**
- 문제: 접근 테스트는 국내 주거용 IP. Azure 미국 IP에서 Google News(429/consent), Yahoo(429), 국내 언론(해외 IP 차단) 결과가 다를 수 있음. 더벨=required인데 러너에서 안 되면 아키텍처 재검토 필요.
- 수정안: Step 0.5 프로브 워크플로(`workflow_dispatch` 전용, curl 14개 URL http_code + 건수) → §3 표에 "러너 결과" 열 → 이후 Step 1.

**M-5. 타이밍 예산에 Pages 빌드·CDN 캐시 누락 — "60분 여유" 과대**
- 문제: 워크플로 완료 ≠ URL 반영. `pages-build-deployment`(1~5분, Jekyll) + CDN `max-age=600`. 고정 소요 ≈ 17~31분 → cron 지연 허용치 약 30~43분. 계획이 인용한 "피크 시 30~60분 지연"과 자기모순. 고부하 시 예약 실행 **드롭** 시나리오 부재.
- 수정안: (a) 07:00 KST(22:00 UTC) + 정각 회피 분(`7 22 * * 0-4`). (b) 안전망 cron(22:45 UTC) — 당일 정상 생성돼 있으면 LLM 호출 없이 종료. (c) `docs/.nojekyll`. (d) AC-15 검증을 "Pages URL curl → 당일 날짜 문자열 < 23:15 UTC"로 교체. (e) 선택: `upload-pages-artifact` + `deploy-pages`.

**M-6. stage2 입력 60건의 카테고리 편중 및 AI 기사 도달 미보장**
- 문제: US 소스 120건 vs KR 적음 → 상위 60건 쏠림. `is_ai` 기사가 importance <3이면 stage2가 AI 재료를 못 받음. AC-6 2회 실패 후 동작 미정의.
- 수정안: 쿼터 기반 입력(US 25 / KR 25 / MACRO 10 + `is_ai` 전부 + 더벨 30). `ai_sector` 최소 길이(≥300자) + "조용한 날엔 최근 흐름 서술" 지시. 2회 실패 시 최종 동작(배너 경고 + 게시) 명시.

**M-7. 수치 환각 가드 부재**
- 문제: 지수 등락률·종목 수치가 LLM 출력 경로에 있으면 환각 검출 불가. 시세 없을 때 LLM이 수치를 지어내지 않도록 하는 지시·검증 없음.
- 수정안: 시장 시그널 표는 `Quote`에서 템플릿 직접 렌더(LLM은 코멘트만). "제공된 시세 외 수치 금지, 종목은 입력 기사 등장분만" 지시. 렌더 후 `%` 수치 vs Quote 대조(불일치 시 status 경고). 시세 `asof` 표기.

**M-8. Jinja autoescape 미명시 — 저장형 XSS**
- 수정안: `Environment(autoescape=select_autoescape(["html"]))` + 테스트(`<script>` 제목 escape 확인). LLM 출력은 이스케이프 후 줄바꿈만 `<br>`/`<p>` 변환.

### Minor (권고)

1. 모델 ID `claude-haiku-4-5-20251001` → `claude-haiku-4-5`. `claude-sonnet-5`는 올바름.
2. dedupe: 공백 토큰 자카드 ≥0.8은 한국어 조사 때문에 사실상 완전 동일 제목만 병합. ` - 언론사명` 접미사 제거 후 문자 bigram 유사도(또는 정규화 후 자카드 0.6). KO/EN 동일 기사는 병합하지 말고 "언어 간 중복은 stage1이 처리"로 명시.
3. git: `user.name/email` 설정, push 전 `pull --rebase origin main`, `fetch-depth: 0` 또는 명시적 fetch.
4. AC-9 자동 감축(200→120)은 과설계. 비용 지배 요인은 출력/thinking/재시도 → 호출 상한(M-1)으로 대체, 감축은 수동 설정값.
5. AC-4: robots.txt → 어댑터 화이트리스트 대체는 합리적이나 스펙 편차로 명시, README에 크롤링 타입 추가 시 robots 체크 의무 기록.
6. AC-5 `leaders: 1..5` 타입 미정 → `{name, ticker, comment}` 객체로 확정.
7. rss.py: feedparser에 URL을 넘기면 자체 fetch해 httpx 모킹 우회 → 항상 `feedparser.parse(text)`.
8. Free 플랜은 공개 리포에서만 Pages 가능 → Step 0에 "공개 리포 필수" 명시.
9. Option B "30초 CPU 제한(무료)"는 부정확(무료 10ms). 결론 유지, 수치 정정.
10. ADR Consequences에 "예약 실행 드롭 가능", "실패 알림 없음 → 사용자 08:40 직접 확인" 수용 리스크 기록.
11. AC-12: 배너는 `<main>` 밖 `<header>`에 두고 `#summary`가 `<main>` 첫 자식 관계 명시.
12. README 런북(수동 dispatch, 로그 보는 법) 1페이지.

---

## AC 커버리지 표

| AC | 계획 단계 | 검증 방법 | 상태 |
|----|-----------|-----------|------|
| AC-1 | Step 1, status.py | 소스별 ok/count, coverage_ok | OK (coverage_ok=false 시 배너 연동 명시 필요) |
| AC-2 | Step 1 google_news.py | httpx 모킹 호스트 assert | OK (Minor-7 조건) |
| AC-3 | Step 1 | 절반 TimeoutError 테스트 | OK |
| AC-4 | Step 1 sources.yaml | 어댑터 화이트리스트 | OK (스펙 편차 명시, Minor-5) |
| AC-5 | Step 3 schemas.py | pydantic | OK (leaders 타입, Minor-6) |
| AC-6 | Step 3 | ai_sector non-empty + 재호출 | **GAP** (M-6) |
| AC-7 | Step 4 | 문자 수 비교 | OK |
| AC-8 | Step 3 | us_view/kr_impact 필수 | OK |
| AC-9 | Step 3 client.py | cost_usd ≤ 0.50 | **GAP** (C-1, M-1) |
| AC-10 | Step 4 | 3섹션 테스트 | OK |
| AC-11 | Step 4 | 수동 확인 | OK |
| AC-12 | Step 4 | `#summary` 첫 자식 | OK (Minor-11) |
| AC-13 | Step 4 | reports/ + archive | OK |
| AC-14 | Step 6 | cron + 로그 | OK |
| AC-15 | Step 6/7 | 완료 < 23:30 UTC | **GAP** (M-5) |
| AC-16 | Step 6 | cron `0-4` | OK |
| AC-17 | Step 4 | `.banner-warn` 테스트 | OK (소스명 포함 assert 권장) |
| AC-18 | Step 5 | stage2 실패 폴백 | **GAP** (M-2) |
| AC-19 | Step 6 | dispatch + `--date` | OK |

추가 GAP: 월요일 US 마감 뉴스(C-2), KST 날짜(M-3), 더벨 러너 검증(M-4).

---

## 반드시 v2에 반영할 항목 (우선순위 순)

1. **C-1** 토큰 산정표, stage1 `max_tokens` ≥16k 또는 압축 포맷, 구조화 출력 채택 여부.
2. **C-2** 수집 윈도 "직전 영업일 07:30 KST 이후"(월요일 72h) + 테스트.
3. **M-1** effort/thinking 정책, stage별 총 호출 상한, 잔여 시간 deadline, SDK `max_retries`.
4. **M-2** 최상위 예외 처리 → 모든 실패 경로 `.banner-error`, `if: always()`, stage1 degrade, 첫 실행 폴백.
5. **M-3** KST 날짜 규칙 + tz-aware 파싱 + 22:30 UTC 테스트.
6. **M-4** Step 0.5 러너 프로브.
7. **M-5** 07:00 KST + 정각 회피, 안전망 cron, `.nojekyll`, AC-15 URL 기준.
8. **M-6** stage2 쿼터 입력, ai_sector 최소 길이, 2회 실패 후 동작.
9. **M-7** 시그널 표 직접 렌더, 수치 환각 금지 + 사후 검사.
10. **M-8** autoescape + XSS 테스트.
11. Minor 최소: 1, 3, 6, 7, 8.

## 잘된 점

- 소스 14개 실제 접근 테스트·stooq 탈락, 더벨 `thebell.co.kr` 호스트 부재 테스트는 구체적이고 검증 가능.
- AC마다 테스트 방법이 붙어 있고 Principle 2(부분 실패 = 정상 경로)가 일관됨.
- Option C invalidate, L2/L3 기각 근거 타당.
