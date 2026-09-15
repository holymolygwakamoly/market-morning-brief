# Plan: Market Morning Brief — 평일 아침 시장 브리핑 자동 생성기

- Status: **pending approval** — 합의 완료(Architect v2 APPROVE_WITH_IMPROVEMENTS → 필수 패치 반영, Critic v2 APPROVE). 사용자 실행 승인 대기
- Source spec: `.omc/specs/deep-interview-market-morning-brief.md` (ambiguity 15.4%)
- Mode: RALPLAN-DR short
- Iteration: **2.1** (v2 + Architect v2 APPROVE_WITH_IMPROVEMENTS · Critic v2 조건부 APPROVE의 필수 패치 반영)
- Date: 2026-09-15
- 리뷰 원본: `.omc/plans/reviews/{architect,critic}-review-v1.md`, `{architect,critic}-review-v2.md`

---

## 1. Requirements Summary

평일(KST 월~금) 아침 GitHub Actions에서 자동 실행되어(주 cron 06:50 KST + 백업 cron 07:35 KST), 무료 공개 소스(RSS/공식 사이트/Google News RSS/Yahoo 차트 JSON)에서 미국·한국 증시 뉴스와 거시 이슈를 수집하고, Claude API 2단계 호출로 섹터 중심(이슈/상승/하락 섹터 + 이유 + 대표주, AI 섹터 필수) 한국어 해석을 만들어, 핵심 요약/상세 분석/참고 자료 3부 HTML 보고서를 **08:30 KST 이전**에 GitHub Pages 공개 URL로 게시한다. 소스 일부 실패 시 부분 발행 + 누락 배너, LLM 실패(또는 파이프라인 어느 단계의 실패든) 시 전일 유지 + 실패 표시. 더벨은 Google News RSS 헤드라인만(thebell.co.kr 직접 호출 0건). 비용은 LLM만, 월 1~2만 원(1회 ≤ 약 $0.50 목표).

**고정 결정(재논의 없음)**: 무료 소스만 / LLM 비용만 / 더벨=Google News RSS 헤드라인 매 실행 필수 / 공개 Pages URL 무인증 / 부분 발행+누락 배너 / LLM 실패→전일 유지+실패 마커 / 섹터 중심 + AI 섹터 매일 / 3부 한국어 HTML / 평일만 / 08:30 KST 이전 / GitHub Actions + Pages(Option A).

**스펙 대비 편차(명시)**
- AC-14 "07:30 시작" → 주 cron **06:50 KST** + 백업 **07:35 KST**. 이유: Pages 빌드·CDN 캐시를 포함한 실제 마감 산식(§3 산정표)에서 07:30 시작은 cron 지연 허용치가 ~27분에 불과. 미국장 마감은 EDT 기준 05:00 KST이므로 06:50 시작도 데이터 손실 없음. 사용자 의도(08:30 이전 게시)는 유지.
- AC-4 "robots.txt 확인" → 페이지 크롤링이 없으므로 **어댑터 타입 화이트리스트**(rss/google_news/yahoo_chart)로 대체. README에 "크롤링 타입 추가 시 robots.txt 체크 의무" 규칙 기록.

## 2. RALPLAN-DR Summary

### Principles
1. **08:30 마감이 최우선** — 품질보다 정시 게시. 모든 단계는 잔여 시간 기반 데드라인을 갖고, 시간이 없으면 호출을 건너뛰고 폴백한다.
2. **부분 실패는 정상 경로** — 소스는 죽는다. 소스별 격리, 결과는 항상 게시, 누락은 배너로 정직하게. **어떤 실패 경로도 배너 없이 끝나지 않는다**(전일 페이지가 오늘 것으로 오인되는 상태 금지).
3. **소스 정책 준수** — robots.txt/이용약관 위반 없음. 더벨은 Google News 헤드라인만.
4. **비용 상한 내 결정론적 파이프라인** — LLM 호출 횟수(stage1 ≤2, stage2 ≤3)와 입력 기사 수(`max_articles`)를 코드로 고정. 실행 간 자동 피드백 없음(단순·예측 가능).
5. **사용자 개입 0** — PC·계정 로그인 없이 돌아가야 한다. 시크릿 1개(ANTHROPIC_API_KEY)만. 알림 채널이 없으므로 페이지 배너가 유일한 실패 신호.

### Decision Drivers (top 3)
1. PC 꺼져 있어도 08:30 전 완료 → 무료 클라우드 스케줄러 필수
2. 유료 API 금지 → RSS/공식 피드/무료 JSON만, 크롤링 최소화
3. 월 1~2만 원 LLM 예산 → 1회 실행 happy path ≈ $0.26, 상한 캡 도달 시에도 ≈ $0.66(§3 산정표)

### Viable Options

**Option A (선택): GitHub Actions cron(이중) + Python 파이프라인 + GitHub Pages 정적 HTML(/docs 커밋)**
- Pros: 무료(공개 리포 Actions 무제한), 시크릿 관리 내장, `workflow_dispatch`로 수동 실행(AC-19), Pages는 리포 커밋만으로 배포, 아카이브가 git 히스토리로 보존
- Cons: cron 지연(피크 시 수십 분) 및 **예약 실행 드롭** 가능 → 06:50 주 + 07:35 백업(멱등)으로 흡수; 리포 공개 필수(Free 플랜 Pages) → 코드·보고서 공개(사용자 OK); Actions 러너(Azure, 미국 IP)에서 일부 소스가 차단될 수 있음 → Step 0.5 러너 프로브로 선검증

**Option B: Cloudflare Workers Cron + KV/R2 + Workers 정적 서빙**
- Pros: cron 정시성 우수, 엣지 실행, 무료 티어 넉넉
- Cons: Python 불가(JS/TS) → feedparser 등 생태계 손실; 무료 티어 CPU 시간 **10ms/요청**(v1의 "30초"는 오기, 정정)으로 수십 개 피드 파싱·LLM 장문 대기에 부적합; 아카이브 저장소 별도 설계 필요
- Architect steelman: Workers Cron을 **트리거로만** 써서 GitHub `workflow_dispatch` REST를 호출하는 조합(JS 20줄)은 유효. 그러나 계정·시크릿(GitHub PAT)이 하나 더 늘어 Principle 5에 어긋나므로 이번 범위에서는 이중 cron으로 대신하고, 첫 달 관측에서 cron 드롭/지연이 반복되면 follow-up으로 채택(§8).

**Option C: 사용자 PC + Windows 작업 스케줄러 (로그온 시 트리거)**
- Pros: 가장 단순, 외부 계정 불필요
- Cons: 사용자 1순위 요구(PC 꺼져 있어도 08:30 전 완료) 불충족 — 08:40 켜면 08:45 이후 완성. **주 경로로 invalidated**; 백업 경로로만 유지(범위 밖, follow-up)

**LLM 호출 구조 옵션**
- L1 (선택): 2단계 — (1) 기사 메타데이터(제목+요약 ≤200자) 일괄 → 중요도·섹터·`story_key` 태깅(구조화 출력, 1회) → (2) 쿼터 기반 선별 기사 + 시세 + 태그 + 더벨 헤드라인 → 보고서 본문 JSON(구조화 출력, 스트리밍, 1회). 양 단계 기본 `claude-sonnet-5`(stage1은 `claude-haiku-4-5`로 설정 다운그레이드 가능 — Architect 지적대로 stage1 Sonnet 승격 비용은 +$0.06/회에 불과하므로 기본은 Sonnet).
- L2: 1회 호출로 전부 — 입력 토큰 폭증(원문 100~300건), 품질 불안정. 기각.
- L3: 기사별 개별 요약 호출 — 호출 수백 회, 비용·시간 초과. 기각.

## 3. Architecture

```
.github/workflows/probe.yml   workflow_dispatch 전용: 러너에서 전 소스 URL curl (Step 0.5)
.github/workflows/brief.yml   cron '50 21 * * 0-4'(주, 06:50 KST) + '35 22 * * 0-4'(백업, 07:35 KST) + workflow_dispatch
    └─ python -m brief.run [--skip-if-done]      ← schedule 실행(주·백업) 시 당일 KST status가 success|degraded면 즉시 exit 0 (비용 0); 수동 dispatch는 강제 실행
         ├─ (최상위 try/except BaseException + 내부 데드라인 14분: 어떤 실패도 .banner-error 후 exit 0)
         ├─ collect/     소스 어댑터 (rss, google_news, yahoo_chart) — timeout 15s, 재시도 2~3회, 예외 격리, 전체 ≤3분
         ├─ dedupe.py    수집 윈도(직전 영업일 06:50 KST 이후) 필터, URL 정규화, 제목 정규화 + 문자 bigram 유사도 병합
         ├─ analyze/     stage1_tag.py (구조화 출력: 중요도/섹터/시장/is_ai/story_key) → select.py (쿼터 선별) → stage2_write.py (구조화 출력, 스트리밍)
         │               stage1 실패 시 degrade: 태깅 없이 최신순 + 소스 쿼터 + AI 키워드 휴리스틱으로 stage2 입력 구성
         ├─ render/      jinja2(autoescape) → docs/reports/YYYY-MM-DD.html, docs/index.html, docs/archive.html
         │               시장 시그널 표는 Quote에서 템플릿 직접 렌더(LLM은 코멘트만); 렌더 후 % 수치 대조
         ├─ status.py    docs/status/YYYY-MM-DD.json (소스별 ok/count/error, 단계별 호출 수·토큰·비용, 경고, run_kind)
         └─ fallback     stage2 최종 실패/기타 실패: 전일 index.html 로드 → <header id="status-banner"> 통째 교체 → 저장
    └─ (if: always()) git commit docs/ → pull --rebase 재시도 → push  → GitHub Pages (Source: main /docs, .nojekyll)
브라우저: index.html의 data-generated + 인라인 JS → 오늘(KST) ≠ 생성일이면 클라이언트 stale 배너(러너가 아예 안 돈 경우 대비)
```

- 언어: Python 3.12. 의존성: `feedparser`, `httpx`, `anthropic`, `jinja2`, `pydantic`, `python-dateutil`, `pyyaml`. (`beautifulsoup4`는 RSS description HTML 정리용만; 페이지 크롤링 없음). `requirements.txt` + `requirements.lock`(pip freeze) 고정, `setup-python` `cache: pip`.
- 소스 설정: `brief/sources.yaml` — `name, type(rss|google_news|yahoo_chart), url, category(US|KR|MACRO|QUOTE), required(bool), lang(ko|en), tz(Asia/Seoul|UTC|America/New_York; naive pubDate 보정용), retries, max_items(기본 40)`.
- 전역 설정 `brief/config.py`: `MAX_ARTICLES=200`(수동 조정, 자동 감축 없음), `STAGE1_MODEL="claude-sonnet-5"`(대안 `claude-haiku-4-5`), `STAGE2_MODEL="claude-sonnet-5"`, `STAGE1_MAX_CALLS=2`, `STAGE2_MAX_CALLS=3`, `RUN_DEADLINE_SEC=840`, `COST_SOFT_CAP_USD=0.50`.
- **모든 날짜·시각은 `ZoneInfo("Asia/Seoul")` 기준**. `--date` 기본값 = `datetime.now(KST).date()`. 기사 시각은 tz-aware UTC로 정규화 후 비교(naive → 소스 `tz`로 보정, 파싱 불가 → 유지·`published_at=None`·윈도 필터 통과).
- 수집 윈도: `window_start = prev_business_day(run_date_kst) 06:50 KST` (화~금 = 24h, 월 = 약 72h). `prev_business_day`는 KST 요일 기준(월→금). 공휴일은 범위 밖.
- 타임박스(잔여 시간 기반): 수집 ≤3분, stage1 ≤3분(호출 ≤2), stage2 ≤6분(호출 ≤3), 렌더+status ≤1분, 내부 데드라인 14분(`RUN_DEADLINE_SEC`), 워크플로 `timeout-minutes: 20`. 각 LLM 호출의 timeout = `min(단계 예산, 잔여 − 60s(렌더·커밋 예약))`; 잔여가 최소 실행 시간(stage1 40s, stage2 90s) 미만이면 호출하지 않고 폴백.

### LLM 호출 설계 (stage별)
| 항목 | stage1 (태깅) | stage2 (본문) |
|------|---------------|---------------|
| 모델 | `claude-sonnet-5` (설정으로 `claude-haiku-4-5`) | `claude-sonnet-5` |
| 호출 방식 | `client.messages.parse(output_format=Stage1Result)` → `.parsed_output` (비스트리밍) | `client.messages.stream(output_format=ReportOut, output_config={"effort":"medium"})` → `get_final_message().parsed_output` → 앱 측 `Report` validator 통과 |
| 구조화 출력 규칙 | **원시 `model_json_schema()`를 `output_config.format`에 직접 넣지 말 것** — 구조화 출력 API는 `minLength/maxLength/minItems/maxItems`를 지원하지 않고 모든 object에 `additionalProperties:false`를 요구한다. SDK `output_format=<pydantic 모델>` 헬퍼만 이 제약을 자동 제거·클라이언트 검증한다. 전달용 모델(`Stage1Result`, `ReportOut`)은 `model_config = ConfigDict(extra="forbid")` + 길이·개수 제약 없음, 루트는 반드시 object. 길이·개수·필수 필드 검증은 앱 측 `Report` validator에서 수행 | 동일 |
| thinking | `{"type":"disabled"}` (분류 작업, Sonnet 5에서 허용). Haiku 4.5는 `thinking`·`effort` 미지정 | `{"type":"adaptive"}` + `effort: "medium"` — thinking 토큰이 `max_tokens`·출력 과금에 포함됨을 산정표에 반영 |
| max_tokens | 16,000 (압축 포맷 실사용 ~6k) | 16,000 (본문 ~8k + thinking ~2~4k) |
| SDK 재시도 | `Anthropic(max_retries=0, timeout=호출별)` | 동일 |
| 자체 재시도 | 총 호출 ≤2 (API 오류·스키마 실패 합산). 백오프 5s(잔여 시간 내) | 총 호출 ≤3 (API 오류·스키마 실패·ai_sector 검증 실패 합산). 백오프 5s/15s(잔여 시간 내) |
| 출력 스키마 | `Stage1Result{items: [{i:int, p:1..5, m:"US"|"KR"|"MACRO", a:bool, s:list[str](섹터 코드), k:str(story_key, ascii slug)}]}` — object 루트, 필드명 1글자 압축 | `Report{headline5[], signal_comments[], sectors[], ai_sector, events_today[], macro_notes, disclaimer}` (§4 AC-5/6/8) |
| 프롬프트 핵심 | 섹터 코드표(약 30개) 제공, 한↔영 동일 이슈는 같은 `story_key` | 한국어, 섹터 > 종목, AI 섹터 필수(≥300자, 조용한 날엔 최근 흐름 서술), **"입력에 없는 수치 주장 금지·종목은 입력 기사 등장분만"**, "투자 조언 아님" 고지 |

### 토큰·비용·시간 산정표
단가(리뷰 기준): Sonnet 5 입력 $2 / 출력 $10 per MTok, Haiku 4.5 $1 / $5. 환율 가정 ₩1,400/$.

**토큰·비용 (1회 실행)**
| 단계 | 입력 토큰 | 출력 토큰(thinking 포함) | Sonnet 5 비용 | Haiku 4.5 비용(stage1만) |
|------|-----------|--------------------------|---------------|--------------------------|
| stage1 | 시스템+섹터표 ~1.5k + 기사 200건 × ~150(영문)·~200~250(한국어) = **~32k(영문 기준) / ~45~55k(KR 절반 시, +$0.03~0.05)** | 200건 × ~30 = **~6k** (cap 16k) | $0.064 + $0.060 = **$0.12** | $0.032 + $0.030 = **$0.06** |
| stage2 | 시스템·지시 ~2.5k + 기사 ~80건 × ~150 = 12k + 더벨 헤드라인 30 × ~30 = 0.9k + 시세 0.4k + 태그 1k = **~17k** | 본문 JSON ~8k + thinking(medium) ~2~4k = **~11k** (cap 16k) | $0.034 + $0.110 = **$0.14** | — |
| **happy path 합계** | ~49k | ~17k | **≈ $0.27 (≈ ₩375)** | stage1 Haiku 시 ≈ $0.21 |
| **캡 도달 시(stage1 2회 + stage2 3회, 통상 출력)** | | | 2 × 0.124 + 3 × 0.144 = **≈ $0.68** | |
| **절대 상한(캡 도달 + 매 호출 출력 16k)** | | | 2 × 0.22 + 3 × 0.19 = **≈ $1.03** | |
| 백업 cron(주 성공 시) | 0 | 0 | **$0** (status 확인 후 종료) | |

- 월간: 영업일 22회 × $0.27 ≈ $5.9 ≈ **₩8,300**. 캡 도달일이 월 3회 있어도 ≈ $7.1 ≈ ₩10,000. 주 실행 실패 → 백업이 전체 재실행하는 날은 2회분 과금. 모두 월 1~2만 원 범위 내.
- `COST_SOFT_CAP_USD=0.50`은 **경고 기준**(status `warnings: ["cost_over_soft_cap"]`)이며 실행을 막지 않는다. 비용 지배 요인은 출력·thinking·재시도이므로 통제 수단은 호출 상한이고, 입력 축소는 `MAX_ARTICLES` 수동 조정.

**시간 예산 (cron 트리거 → Pages URL 반영)**
| 구간 | happy | worst(캡·데드라인 도달) |
|------|-------|--------------------------|
| cron 지연 | 0~5분 | 수십 분 또는 **드롭**(→ 백업 cron) |
| 러너 큐·부팅 | 0.5분 | 2분 |
| checkout(fetch-depth 0) + setup-python(pip cache) + pip install | 0.75분 | 2분 |
| collect (ThreadPool 8) | 1분 | 3분(데드라인) |
| dedupe·선별 | 5초 | 10초 |
| stage1 | 45초 | 3분(2회) |
| stage2 (스트리밍) | 2분 | 6분(3회) |
| 렌더·status·수치 대조 | 5초 | 20초 |
| (참고) 구조화 출력 스키마 컴파일 | 0 (24h 캐시) | 수초~수십초 — 1일 1회 실행이라 매 실행 캐시 만료 가능, stage2 worst에 포함 |
| commit + pull --rebase + push(재시도 3회) | 20초 | 1분 |
| Pages build/deploy(`.nojekyll`, 정적 복사) | 1~2분 | 5분 |
| CDN 캐시(`max-age=600`) | 0~10분 | 10분 |
| **고정 소요 합계(cron 지연 제외)** | **≈ 7~17분** | **≈ 32.5분 (내부 데드라인 14분 꽉 채우면 ≈ 34분)** |

- 주 cron 06:50 + worst 34분 = **07:24** → 08:30까지 cron 지연 허용치 **66분**. 백업 cron 07:35 + 34분 = **08:09** → 허용치 **21분**. `run.py` 내부 데드라인 14분 + 워크플로 20분이 worst 열의 상한을 보장.
- AC-15 판정은 워크플로 완료 시각이 아니라 **Pages URL curl에 당일 날짜 문자열이 포함되는 시각 < 23:15 UTC(08:15 KST)**.

### 검증된 소스 목록 (로컬 2026-09-14 접근 테스트 + 러너 결과는 Step 0.5 후 기입)
| 소스 | URL | 카테고리 | tz | 로컬 결과 | 러너 결과 |
|------|-----|---------|----|-----------|-----------|
| CNBC Top News | `https://www.cnbc.com/id/100003114/device/rss/rss.html` | US | UTC | OK 30건 | TBD |
| CNBC Finance | `https://www.cnbc.com/id/10000664/device/rss/rss.html` | US | UTC | OK 30건 | TBD |
| MarketWatch Top | `https://feeds.content.dowjones.io/public/rss/mw_topstories` | US | UTC | OK 10건 | TBD |
| Yahoo Finance | `https://finance.yahoo.com/news/rssindex` | US | UTC | OK 50건 | TBD |
| Fed 보도자료 | `https://www.federalreserve.gov/feeds/press_all.xml` | MACRO | America/New_York | OK 20건 | TBD |
| 연합뉴스 경제 | `https://www.yna.co.kr/rss/economy.xml` | KR | Asia/Seoul | OK 120건 | TBD |
| 한국경제 증권 | `https://www.hankyung.com/feed/finance` | KR | Asia/Seoul | OK 50건 | TBD |
| 매일경제 증권 | `https://www.mk.co.kr/rss/50200011/` | KR | Asia/Seoul | OK 50건 | TBD |
| 한국은행 보도자료 | `https://www.bok.or.kr/portal/bbs/B0000245/news.rss?menuNo=200761` | MACRO | Asia/Seoul | OK 100건 | TBD |
| **더벨 (Google News)** | `https://news.google.com/rss/search?q=site:thebell.co.kr&hl=ko&gl=KR&ceid=KR:ko` | KR, **required** | UTC | OK 100+건 (간헐 타임아웃 → 재시도 3회) | TBD (429/consent 여부 확인) |
| Google News 「AI stocks」 | `https://news.google.com/rss/search?q=AI+stocks&hl=en-US&gl=US&ceid=US:en` | US/AI | UTC | 보강용 | TBD |
| Google News 「코스피」 | `https://news.google.com/rss/search?q=코스피&hl=ko&gl=KR&ceid=KR:ko` | KR | UTC | OK 91건 | TBD |
| Yahoo 차트 JSON | `https://query1.finance.yahoo.com/v8/finance/chart/{^GSPC,^IXIC,^DJI,^KS11,^KQ11,KRW=X,^TNX,CL=F}` | QUOTE | — | OK | TBD (429/crumb 여부 확인) |
| stooq CSV | — | — | — | 404, 제외 | — |

- 러너 결과가 FAIL인 소스는 `sources.yaml`에서 `enabled: false` 처리하거나 User-Agent/대체 URL을 시도. **더벨(required)이 러너에서 실패하면** Step 1 착수 전에 대안(다른 hl/gl 파라미터, `news.google.com/rss/search` 대신 `…/rss/headlines/section/…` 등)을 프로브에 추가해 재검증한다.

## 4. Acceptance Criteria (스펙 AC-1~19 상속 + 계획 수준 구체화)

- [ ] AC-1: `status.json`에 소스별 `{ok, count, error}` 기록, US/KR/MACRO 각 ≥1 성공 시 `coverage_ok=true`. `coverage_ok=false`이면 `.banner-warn`에 "카테고리 누락: …" 문구 포함(테스트)
- [ ] AC-2: 더벨 어댑터는 `news.google.com`만 호출. 테스트: httpx 모킹으로 요청 호스트 목록에 `thebell.co.kr` 부재 assert. 3부 소스 표에 더벨 행(성공/실패, 건수) 표시
- [ ] AC-3: 단위 테스트 — 소스 절반이 `TimeoutError`를 던져도 `collect_all()`이 나머지 결과 반환
- [ ] AC-4: `sources.yaml`의 `type`은 `rss|google_news|yahoo_chart`만 허용(로더에서 화이트리스트 검증 + 테스트). **스펙 편차**: robots.txt 체크는 페이지 크롤링이 없으므로 미적용. README에 "크롤링 타입 추가 시 robots.txt 체크 의무" 규칙 기록
- [ ] AC-5: stage2 출력 스키마(pydantic, 구조화 출력): `sectors: list[Sector{name, direction: issue|up|down, market: US|KR, reason, leaders: list[Leader{name, ticker: str|None, comment}] (1..5), us_view, kr_impact}]`. 스키마 위반은 API 수준에서 차단되며 pydantic 재검증 실패 시 stage2 호출 캡(3) 내 재생성
- [ ] AC-6: `ai_sector: AiSector{headline, status(min_length=300), drivers[], leaders[], kr_impact}` 필수. 프롬프트에 "AI 섹터는 매일 필수, 조용한 날에는 최근 흐름·밸류에이션·수급을 서술" 명시. 검증 실패 → 강화 지시로 재호출(캡 내). **최종 동작(확정)**: 캡 소진 후에도 `ai_sector`가 짧으면 나머지 JSON이 유효한 경우 **게시하되** `.banner-warn`에 "AI 섹터 분량 미달" 표시 + status `warnings: ["ai_sector_short"]`. JSON 자체가 무효면 AC-18 폴백
- [ ] AC-7: 렌더 후 2부에서 섹터 서술(`reason`+`us_view`+`kr_impact`+`ai_sector.status`) 문자 수 > 대표주 서술(`leaders[].comment`) 문자 수 — 렌더 테스트로 계산, 위반 시 status 경고
- [ ] AC-8: US 시장 섹터(`market == "US"`)는 `us_view`, `kr_impact` 두 필드 non-empty(pydantic validator)
- [ ] AC-9: `status.json`에 stage별 `{model, calls, input_tokens, output_tokens, cost_usd}` + 합계 `cost_usd` 기록. 호출 캡(stage1 ≤2, stage2 ≤3)을 테스트로 고정. `cost_usd > 0.50`이면 `warnings: ["cost_over_soft_cap"]`(실행 차단 없음). **자동 감축 없음** — 입력 축소는 `MAX_ARTICLES`/`--max-articles` 수동 설정
- [ ] AC-10: 템플릿에 `#summary`, `#analysis`, `#references` 3개 섹션 고정. 렌더 테스트로 존재 확인
- [ ] AC-11: `lang="ko"`, viewport meta, 모바일 CSS 인라인. 수동 확인 1회
- [ ] AC-12: 배너는 `<main>` 밖 `<header id="status-banner">`에 위치, `#summary`가 `<main>`의 첫 자식 — 렌더 테스트(HTML 파서로 assert)
- [ ] AC-13: `docs/reports/YYYY-MM-DD.html` 누적 + `docs/archive.html` 목록 자동 생성(파일 목록 기반, status의 result 표시)
- [ ] AC-14/16: 주 cron `50 21 * * 0-4` + 백업 `35 22 * * 0-4` (UTC 일~목 = KST 월~금). 백업은 `--skip-if-done`으로 멱등. 검증: 워크플로 파일 + 첫 주 실행 로그(주/백업 각각). (KST 공휴일은 범위 밖 — 실행돼도 무해). **스펙 편차**: 07:30 → 06:50/07:35
- [ ] AC-15: **Pages URL curl**의 응답에 `data-generated="<KST 당일>"` 문자열이 포함되는 시각 < 23:15 UTC. 첫 주 5회 모두 충족(Step 7 체크리스트)
- [ ] AC-17: `status.failed_sources` 비어있지 않으면 `.banner-warn` 렌더, 배너 텍스트에 누락 소스명 전부 포함 — 테스트(assert 소스명)
- [ ] AC-18: stage2 최종 실패 **및 그 외 모든 실패 경로**(수집/stage1 미처리 예외, 데드라인 초과, 첫 실행에 전일 파일 없음)에서 전일 `index.html`을 읽어 `<header id="status-banner">`를 `.banner-error`("YYYY-MM-DD 생성 실패 HH:MM KST — 아래는 <전일 날짜> 보고서")로 **통째 교체**(중첩 없음) 후 저장, exit 0. 전일 파일이 없으면 `empty.html.j2` + 에러 배너. 테스트: 연속 2회 실패에도 `.banner-error` 1개
- [ ] AC-19: `workflow_dispatch` 존재 + 로컬 `python -m brief.run --date 2026-09-15` 동작. README 런북에 수동 실행·로그 보는 법 기록

## 5. Implementation Steps

### Step 0 — 리포 부트스트랩 (사용자 작업 1회 포함)
- `git init`, `.gitignore`(`.env`, `__pycache__`, `.venv`), `pyproject.toml`, `requirements.txt` + `requirements.lock`, `README.md`(설정 절차 + 1페이지 런북: 수동 dispatch 방법, Actions 로그 보는 법, `MAX_ARTICLES`/모델 변경 위치, 크롤링 타입 추가 시 robots.txt 체크 의무)
- `docs/.nojekyll` 생성(Jekyll 빌드 생략 → Pages 배포 단축·`_`로 시작하는 파일 문제 회피)
- **사용자**: GitHub **공개 리포** 생성(Free 플랜은 공개 리포에서만 Pages 가능), `ANTHROPIC_API_KEY` 시크릿 등록, Settings → Pages Source = `main` / `/docs`
- 파일: `pyproject.toml`, `requirements.txt`, `requirements.lock`, `README.md`, `docs/.nojekyll`
- 완료 기준: 리포 생성·시크릿 등록·Pages 활성 확인(빈 index.html이 URL에서 열림)

### Step 0.5 — 러너 소스 프로브 (Step 1 이전 필수)
- `.github/workflows/probe.yml`: `workflow_dispatch` 전용. `scripts/probe_sources.sh` — `sources.yaml`의 모든 URL(더벨 Google News, Yahoo 차트 8심볼 포함)에 대해 `curl -sS -A "<UA>" -o /tmp/body -w "%{http_code} %{time_total}"` 후 RSS는 `<item>` 개수, Yahoo는 `jq '.chart.result[0].meta.regularMarketPrice'`를 출력. 3회 반복(간헐 429 감지)
- 결과를 §3 소스 표 "러너 결과" 열에 기입. FAIL 소스는 `enabled: false` 또는 대체 URL. **더벨 FAIL 시 대안 재검증 후에만 Step 1 진행**
- 파일: `.github/workflows/probe.yml`, `scripts/probe_sources.sh`, `brief/sources.yaml`(초안)
- 완료 기준: 프로브 로그 확보, 소스 표 러너 열 채움, required 소스(더벨) 러너 OK

### Step 1 — 수집 계층 `brief/collect/`
- `base.py`: `Article(title, url, published_at: datetime|None(UTC aware), source, publisher, category, lang, summary)` pydantic 모델, `SourceResult(name, ok, count, error, articles)`, `Quote(symbol, name, close, change_pct, asof: date, asof_label)`
- `rss.py`: httpx(timeout 15s, retry 2, backoff 2s, User-Agent 지정)로 텍스트를 받아 **항상 `feedparser.parse(text)`**(URL을 넘기지 않음 → 모킹 우회 방지). description HTML strip(bs4). pubDate 파싱: tz-aware → UTC 변환; naive → 소스 `tz` 적용; 파싱 불가 → `None`(윈도 필터 통과, status에 카운트). `max_items` 적용
- `google_news.py`: rss.py 상속, retry 3, timeout 20s. 제목 끝 ` - 출처` 접미사 제거(원 제목은 `raw_title` 보존), `<source>` 요소 → `publisher`. 더벨 피드는 `source="더벨"` 강제, Google 리다이렉트 링크 그대로 보관(원문 URL 해석을 위한 thebell 호출 금지)
- `yahoo_chart.py`: 8개 심볼 JSON → `Quote`. `asof`는 응답의 마지막 거래일(현지) — 미국 휴장일엔 전전일 종가가 되므로 템플릿에 `asof` 배지 표시. 실패는 비치명(시그널 표 "데이터 없음")
- `__init__.py::collect_all(sources, deadline)`: `ThreadPoolExecutor(8)` + 전체 3분 데드라인(`future.result(timeout=잔여)`), 예외 격리, `SourceResult` 목록 반환
- `sources.yaml`: §3 표 그대로(`tz`, `required`, `enabled` 포함)
- 테스트 `tests/test_collect.py`: 절반 타임아웃 격리(AC-3), 더벨 호스트 부재(AC-2), naive pubDate KST 보정, 접미사 제거, feedparser에 URL 미전달, 어댑터 타입 화이트리스트(AC-4)
- 완료 기준: 테스트 통과, `python -m brief.collect --dump`로 소스별 건수 출력

### Step 2 — 수집 윈도·중복 제거 `brief/dedupe.py`
- `window_start(run_date_kst)`: 직전 영업일(KST 월~금) 06:50 KST → UTC. 월요일은 금요일 06:50 KST(~72h)
- URL 정규화(utm/fbclid 제거, 스킴·호스트 소문자), 제목 정규화(접미사 제거·공백/구두점 제거·소문자) 후 **문자 bigram Dice 유사도 ≥ 0.75**면 병합(같은 `lang` 내에서만). 한↔영 동일 이슈는 병합하지 않고 stage1 `story_key`로 묶음
- 소스 다양성: 소스당 최대 `max_items`(40), 전체 상한 `MAX_ARTICLES`(200) — 초과 시 소스별 라운드로빈 최신순
- 테스트: 월요일 `--date`에서 금요일 22:00 KST 기사 포함 assert; 화요일에는 일요일 기사 제외; 동일 기사 3소스(접미사 다름) → 1건; 조사만 다른 한국어 제목 병합; KO/EN 동일 이슈 비병합
- 완료 기준: 테스트 통과

### Step 3 — 분석 계층 `brief/analyze/`
- `client.py`: `Anthropic(max_retries=0)`; `call(stage, fn, max_calls, deadline)` — 호출마다 `client.with_options(timeout=min(단계 예산, 잔여−60s))`, 총 호출 수를 stage별 캡으로 제한, 잔여 시간이 최소 실행 시간 미만이면 `SkippedForDeadline` 반환, usage 집계 → `cost_usd`(단가표 상수) 및 status 기록
- `schemas.py`: **전달용**(`output_format`에 넘김, `extra="forbid"`, 길이·개수 제약 없음, object 루트) `Stage1Result{items[]}`, `ReportOut`(`headline5[]`, `signal_comments[]`(코멘트만, 수치 없음), `sectors[]`, `ai_sector`, `events_today[]`, `macro_notes`, `disclaimer`) / **검증용** `Report`(`ReportOut`과 같은 필드 + validator: AC-6 `ai_sector` ≥300자, AC-8 US 섹터 `us_view`/`kr_impact`, leaders 1..5, `headline5` 정확히 5). `Report.model_validate(out.model_dump())`로 앱 측 검증 → 실패 사유를 재호출 프롬프트에 첨부
- `stage1_tag.py`: 입력 = 번호 매긴 기사(제목+요약 ≤200자) + 섹터 코드표 → `client.messages.parse(model=STAGE1_MODEL, max_tokens=16000, thinking={"type":"disabled"}, output_format=Stage1Result)`. Haiku 4.5 선택 시 `thinking` 미지정. 실패(캡 소진) → `Stage1Degraded` 예외
- `select.py`: 쿼터 선별 — importance 내림차순으로 US 25 / KR 25 / MACRO 10 + `is_ai` 상위 ≤15 + 더벨 헤드라인 ≤30(제목만) → `story_key`로 그룹핑(같은 이야기의 KO/EN 링크를 한 항목에 병기). **degrade 경로**(stage1 실패): 태깅 없이 최신순 + 동일 쿼터(`market`은 `Article.category`로 대체) + AI 키워드 휴리스틱(AI, 인공지능, 반도체, HBM, GPU, 엔비디아/NVIDIA, 데이터센터, 오픈AI)로 ≤10건 추가, status `stage1_degraded=true` → `.banner-warn`
- `stage2_write.py`: 입력 = 선별 기사 + `Quote` 목록(값 그대로) + 태그 요약 → `client.messages.stream(model=STAGE2_MODEL, max_tokens=16000, thinking={"type":"adaptive"}, output_config={"effort":"medium"}, output_format=ReportOut)` → `get_final_message().parsed_output` → `Report` validator(앱 측). **원시 `model_json_schema()` 전달 금지**(§3 구조화 출력 규칙). 프롬프트: 한국어, 섹터 > 종목, AI 섹터 필수·조용한 날 지시, **"입력에 제공된 시세·기사에 없는 수치를 쓰지 말 것, 대표주는 입력 기사에 등장한 종목만"**, "투자 조언 아님". 검증 실패 시 실패 사유를 덧붙여 재호출(캡 3 내). 마지막 호출에서도 `ai_sector`만 미달이면 `Report`를 반환하고 `warnings` 추가(AC-6 최종 동작)
- 테스트: 스키마 validator, 전달용 모델 스키마에 `minLength/minItems/maxItems` 부재 + `additionalProperties:false` assert, 모킹된 `messages.parse/stream` 호출 kwargs에 `output_format` 인자 사용(그리고 `output_config`에 `format` 키 부재) assert, 모킹 응답으로 (a) 스키마 실패→재호출→성공, (b) 캡 도달 시 호출 수 정확히 2/3, (c) 데드라인 부족 시 호출 0회 + `SkippedForDeadline`, (d) stage1 실패 → degrade 입력 구성, (e) usage → cost_usd 계산
- 완료 기준: 테스트 통과, `--dry-run` 픽스처로 stage 입출력 파일 생성

### Step 4 — 렌더 `brief/render/`
- `Environment(autoescape=select_autoescape(["html", "j2"]))`. LLM 텍스트는 이스케이프 후 줄바꿈만 `<p>`/`<br>` 변환(커스텀 필터, `Markup` 최소 사용)
- `templates/report.html.j2`: `<header id="status-banner">`(배너 슬롯: error/warn 각 최대 1개, `data-failed-on` 속성) → `<main>`: `#summary`(핵심 5 + **시장 시그널 표: Quote에서 직접 렌더**, `asof` 배지, LLM `signal_comments`는 표 아래 코멘트로만) → `#analysis`(이슈/상승/하락 섹터, AI 섹터 블록) → `#references`(지표 표, 소스 상태 표(더벨 행 포함), 기사 링크 전체, story_key 그룹별 KO/EN 링크). `<html lang="ko" data-generated="YYYY-MM-DD">`, viewport meta, 인라인 CSS(외부 CDN 없음), 인라인 stale JS(아래)
- 인라인 JS(stale 배너): `Intl.DateTimeFormat("en-CA",{timeZone:"Asia/Seoul"})`로 오늘(KST)·현재 시각 계산 → 평일이고 08:30 KST 이후이며 `data-generated` ≠ 오늘이고 `header`에 `[data-failed-on=오늘]`이 없으면 `#stale-banner`("이 페이지는 YYYY-MM-DD 보고서입니다. 오늘 자동 생성이 실행되지 않았을 수 있습니다") 표시. 서버 측 배너와 중첩되지 않음
- `templates/archive.html.j2`, `templates/empty.html.j2`(첫 실행 폴백용)
- `render.py`: `docs/reports/{date}.html`, `docs/index.html`(복사), `docs/archive.html`, `docs/status/{date}.json`. 렌더 후 **수치 대조**: LLM 텍스트 필드에서 `[-+]?\d+(\.\d+)?\s?%` 추출 → `Quote.change_pct`(소수 1자리 반올림) 또는 입력 기사 텍스트에 존재하지 않으면 `status.unverified_numbers[]`에 기록 + `warnings: ["unverified_numbers"]`(게시는 진행)
- `fallback.py`: 기존 `index.html` 로드 → `<header id="status-banner">…</header>` 정규식으로 통째 교체(중첩 방지) → `data-generated`는 유지, 배너 문구의 "아래는 YYYY-MM-DD 보고서" 날짜는 로드한 파일의 `data-generated` 값 사용(연속 실패 시 전일이 아닐 수 있음) → 저장. `--reason runner_killed`로도 호출 가능(Step 6). 전일 파일 없으면 `empty.html.j2`
- 테스트: 3섹션 존재(AC-10), `#summary` 첫 자식·배너는 header(AC-12), 배너 조건·소스명 포함(AC-1/17), AC-7 문자 수, XSS(`<script>` 제목이 escape됨), 시그널 표 값이 Quote와 일치, 수치 대조 경고, 폴백 2회 연속에도 `.banner-error` 1개 + 배너 날짜 = 로드한 `data-generated`, 첫 실행 폴백
- 완료 기준: 테스트 통과, 픽스처 렌더 결과 브라우저 확인

### Step 5 — 오케스트레이션 `brief/run.py` + 폴백
- 순서: 시작 시각(KST) 기록 → `--skip-if-done` 검사(당일 `docs/status/{date}.json`의 `result in ("success","degraded")`면 로그 후 exit 0, 네트워크 0회) → collect → dedupe → stage1(실패 시 degrade) → select → stage2 → render → status
- **최상위 `try/except BaseException`** + 내부 데드라인 14분(각 단계 진입 시 잔여 확인): 어떤 예외·타임아웃이든 `fallback.write_error_banner(reason, now_kst)` → status `result: "failed"` 기록 → **exit 0**(커밋은 항상 진행). `KeyboardInterrupt`/`SystemExit`도 동일 처리
- 결과 등급: `success`(LLM 정상) / `degraded`(stage1 degrade·소스 누락·ai_sector_short 등 경고 있음, 게시됨) / `failed`(전일 유지 + error 배너). 백업 cron은 `success`와 `degraded` 모두 "완료"로 간주해 재실행하지 않음
- CLI: `--date`(기본 `now(KST).date()`), `--dry-run`(LLM 미호출, 픽스처), `--max-articles`, `--skip-if-done`, `--run-kind primary|backup|manual`(status 기록용)
- 테스트: `--dry-run` E2E가 HTML 3파일 + status 생성; **고정 시각 2026-09-15T21:50:00Z(=KST 09-16 06:50)에서 report date = 2026-09-16** assert; 고정 데드라인 초과 시 error 배너 + exit 0; `--skip-if-done`이 success status 존재 시 collect를 호출하지 않음(모킹 assert)
- 완료 기준: 테스트 통과, 로컬 실제 실행 1회 성공

### Step 6 — 워크플로 `.github/workflows/brief.yml`
- `on: schedule: [{cron: '50 21 * * 0-4'}, {cron: '35 22 * * 0-4'}]`, `workflow_dispatch`
- `permissions: contents: write`, `concurrency: {group: brief, cancel-in-progress: false}`, `timeout-minutes: 20`, `env: {TZ: Asia/Seoul}`
- steps: `actions/checkout@v4 (fetch-depth: 0)` → `setup-python 3.12 (cache: pip, cache-dependency-path: requirements.lock)` → `pip install -r requirements.lock` → run: `python -m brief.run --run-kind ${{ github.event.schedule == '35 22 * * 0-4' && 'backup' || (github.event_name == 'workflow_dispatch' && 'manual' || 'primary') }} ${{ github.event_name == 'schedule' && '--skip-if-done' || '' }}` (**두 schedule 트리거 모두 `--skip-if-done`** — 주 cron이 백업 뒤에 큐잉돼도 2회 과금 방지; `workflow_dispatch`만 강제 실행) → **`if: always()`** 킬 폴백 step: `[ -f "docs/status/$(TZ=Asia/Seoul date +%F).json" ] || python -m brief.fallback --reason runner_killed` (workflow `timeout-minutes`로 Python이 SIGKILL돼 `except BaseException`이 못 돈 경우 커버, 네트워크 0·1초) → **`if: always()`** publish step:
  ```
  git config user.name "brief-bot" && git config user.email "brief-bot@users.noreply.github.com"
  git add docs
  if git diff --cached --quiet; then echo "no changes"; exit 0; fi
  git commit -m "brief: $(TZ=Asia/Seoul date +%F) [$RUN_KIND]"
  for i in 1 2 3; do
    git rebase --abort 2>/dev/null || true
    git pull --rebase origin main && git push origin HEAD:main && exit 0
    sleep 5
  done; exit 1
  ```
- 완료 기준: `workflow_dispatch` 1회 성공, 커밋 메시지 날짜 = KST 당일, 두 cron 항목이 각각 실행 로그에 나타남
- 참고: `concurrency` pending 슬롯은 1개 — 주 실행 중 백업이 대기 중일 때 수동 dispatch가 들어오면 백업이 취소되고 수동 실행이 대신 돈다(결과 동일). Step 7 체크리스트에 주석
- (선택 follow-up) `actions/upload-pages-artifact` + `deploy-pages`로 게시 분리 — Pages 빌드 대기 단축

### Step 7 — 첫 실행 및 튜닝
- `workflow_dispatch`로 수동 1회 → Pages URL에서 `data-generated` 확인 → 프롬프트 톤/길이 조정 1~2회
- 첫 주 5영업일 체크리스트(README 런북에 표로): 주 cron 실행 여부·완료 시각, 백업 cron `--skip-if-done` 종료 여부, **`curl -s <Pages URL> | grep -c 'data-generated="<당일>"'` 시각 < 08:15 KST**, `status.cost_usd`, 더벨 성공률, `warnings` 목록, **첫 월요일: US 기사 중 금요일(KST 토) 발행분 건수**(피드 보유량이 금요일 마감 기사를 밀어냈는지 확인 — 부족하면 Google News 쿼리 보강)
- 완료 기준: 5/5 AC-15 충족, 평균 비용 ≤ $0.50, 더벨 러너 성공률 ≥ 80%(미달 시 대안 URL 검토)

## 6. Risks & Mitigations
| 위험 | 영향 | 완화 |
|------|------|------|
| GitHub Actions cron 지연/**드롭**(피크 시) | 08:30 초과 또는 미실행 | 06:50 주 + 07:35 백업(멱등) 이중 cron; 정각 회피 분 사용; 미실행 시 클라이언트 stale 배너로 사용자에게 명시. 반복 시 Workers Cron 트리거(§8) |
| Google News RSS 러너에서 429/consent | 더벨(required) 누락 | Step 0.5 프로브로 사전 확인, retry 3 + UA 지정, 실패 시 배너 "더벨 누락"(AC-17), 대안 URL 검토 |
| Yahoo v8 chart 429/crumb 변경 | 시장 시그널 누락 | 비치명(표 "데이터 없음"), `asof` 배지, 프로브로 확인, 대체 후보 문서화 |
| Actions 러너 IP가 국내 언론사에 차단 | KR 소스 누락 | US/KR 각 4개 이상 중복 소스, 누락 배너, 프로브로 사전 확인 |
| stage1 출력 절단 | 태깅 실패 | 압축 포맷(~6k) + max_tokens 16k + 구조화 출력; 실패 시 degrade 경로로 stage2 진행 |
| stage2 thinking·본문이 16k 초과 | JSON 절단 | effort medium, 본문 분량 지시(섹터 6~10개), `stop_reason == "max_tokens"` 시 재호출(캡 내) |
| 재시도 중첩·시간 초과 | 데드라인·비용 초과 | SDK `max_retries=0`, stage별 총 호출 캡, 잔여 시간 기반 timeout, 내부 데드라인 14분 |
| 파이프라인 크래시·pip 실패·러너 장애 | 전일 페이지가 오늘 것으로 오인 | 최상위 예외 처리 → error 배너 + exit 0; 커밋 `if: always()`; 러너가 아예 안 돌면 클라이언트 stale 배너 |
| 수치 환각(등락률·티커) | 잘못된 판단 근거 | 시그널 표 템플릿 직접 렌더, 수치 금지 지시, 렌더 후 % 대조 → status 경고 |
| 저장형 XSS(기사 제목·LLM 출력) | 공개 페이지 스크립트 실행 | Jinja autoescape + 테스트 |
| 동시 푸시 충돌(백업 cron·수동 실행) | push 실패 | concurrency 그룹 직렬화 + `pull --rebase` 재시도 3회 |
| 비용 초과 | 예산 위반 | 호출 캡, `MAX_ARTICLES` 수동 조정, status에 비용·soft cap 경고, 월 누적은 archive에서 합산 확인 |
| 한국 공휴일 실행 | 무의미한 보고서 | 범위 밖. 미국장 뉴스는 유효하므로 무해 |
| 공개 리포에 API 키 노출 | 보안 | GitHub Secrets만 사용, `.env` gitignore, 코드에 키 하드코딩 금지 테스트(grep) |
| 실패 알림 없음 | 실패를 늦게 인지 | 수용 리스크: 사용자가 08:40 페이지 배너로 확인(ADR). 알림 채널은 follow-up |

## 7. Verification Steps
1. `pytest` 전부 통과(수집 격리, 더벨 호스트, 윈도(월요일 72h), dedupe, 스키마·호출 캡·데드라인, 렌더 섹션·배너·XSS·수치 대조·폴백 중첩 방지, KST 날짜, `--skip-if-done`, dry-run E2E)
2. Step 0.5 프로브 로그로 소스 표 러너 열 채움, 더벨 러너 OK 확인
3. 로컬 `python -m brief.run --dry-run` → `docs/index.html` 브라우저 확인(데스크톱+모바일 폭), `#summary`가 첫 화면
4. 로컬 실제 실행 1회(API 키) → `status.cost_usd ≤ 0.50`, 호출 수 stage1 ≤2/stage2 ≤3, `ai_sector.status ≥ 300자`, 섹터>종목 문자 수, `unverified_numbers` 비어 있음(또는 검토)
5. `workflow_dispatch` 1회 → 커밋 메시지 날짜 KST, Pages URL에서 `data-generated` = 당일
6. 첫 5영업일: Pages URL curl 당일 문자열 < 23:15 UTC 5/5, 백업 cron이 `skip` 로그로 종료, 더벨 수집 성공률, 비용 평균 기록
7. 의도적 실패 리허설 1회(잘못된 API 키로 dispatch) → 전일 페이지 + `.banner-error` 1개, exit 0, 커밋됨

## 8. Out of Scope (follow-ups)
- `upload-pages-artifact` + `deploy-pages` 게시 분리(Pages 빌드 대기 단축)
- Cloudflare Workers Cron → `workflow_dispatch` REST 트리거(cron 드롭이 반복될 때)
- 로컬 백업 실행(Windows 작업 스케줄러 로그온 트리거)
- 한국 공휴일 스킵
- 실패 알림 채널(이메일/텔레그램)
- 08:20 KST 자동 검증 워크플로(Pages URL curl 실패 시 이슈 생성)
- 프롬프트 캐싱(시스템 프롬프트·섹터표 고정 프리픽스) — 비용 여유가 있어 이번엔 미적용
- 저녁(미국장 개장 전) 2차 브리핑

## 9. ADR
- **Decision**: GitHub Actions 이중 cron(06:50 KST 주 + 07:35 KST 백업, 멱등) + Python 2단계 LLM 파이프라인(양 단계 `claude-sonnet-5`, 구조화 출력, 호출 캡) + GitHub Pages(/docs 커밋, `.nojekyll`) 정적 HTML + 클라이언트 stale 배너.
- **Drivers**: PC 무관 08:30 전 완료 / 무료 소스·무료 인프라 / LLM 월 1~2만 원.
- **Alternatives considered**: Cloudflare Workers 전체 실행(JS 제약·무료 CPU 10ms), Workers Cron 트리거만 사용(계정·시크릿 추가 → follow-up), 사용자 PC 스케줄러(1순위 요구 불충족), 1회/기사별 LLM 호출(비용·품질), stage1 Haiku 기본(절약 $0.06/회 대비 선별 품질 손실 → 설정 옵션으로만 유지).
- **Why chosen**: 유일하게 세 드라이버를 동시에 만족하고 Python 생태계(feedparser, anthropic)를 그대로 쓰며 아카이브가 git으로 공짜. 이중 cron과 stale 배너로 Actions 스케줄러의 지연·드롭 리스크를 추가 계정 없이 흡수.
- **Consequences**: cron 지연 허용치 69분(주)/24분(백업)으로 마감 흡수; **예약 실행이 드롭될 수 있음**(백업 cron + stale 배너로 완화, 완전 제거는 아님); **실패 알림이 없어 사용자가 08:40 페이지 배너로 직접 확인**; 리포 공개 필수(코드·보고서 공개); Yahoo 비공식 API 의존(비치명); 러너 IP 차단 가능성은 Step 0.5에서 선검증하되 이후 변동 가능; 자동 감축을 없애 비용은 호출 캡으로만 통제(캡 도달일 ≈ $0.66).
- **Follow-ups**: 8절 항목 + 첫 주 관측 후 cron 시각·`MAX_ARTICLES`·effort 조정.

---

## 리뷰 반영 매트릭스

### Architect (개선 목록 1~11 + 구체적 갭)
| # | 지적 | 반영 위치 |
|---|------|-----------|
| 1 | stage1 max_tokens 4k → 16k | §3 LLM 호출 설계, §5 Step 3 (16k + 압축 포맷 + 구조화 출력) |
| 2 | stage2 16k + 스트리밍 + effort, 모델 ID 수정 | §3 LLM 호출 설계, §5 Step 3 (`stream` + `effort: medium` + adaptive thinking), 모델 `claude-haiku-4-5` |
| 3 | 이중 cron(06:50 주 + 07:35 백업, 멱등) | §3 아키텍처, §4 AC-14, §5 Step 5(`--skip-if-done`)·Step 6 |
| 4 | 클라이언트 측 stale 배너 | §3 아키텍처, §5 Step 4(인라인 JS, `data-generated`) |
| 5 | 러너 소스 프로브를 Step 1 이전에 | §5 Step 0.5, §3 소스 표 "러너 결과" 열 |
| 6 | KST 날짜 처리(커밋 메시지·--date·윈도 필터) | §3 KST 규칙, §5 Step 1(tz 보정)·Step 5(`--date` 기본, 21:50Z 테스트)·Step 6(`TZ=Asia/Seoul date +%F`) |
| 7 | pull --rebase + 재시도, bot identity, .nojekyll | §5 Step 0(.nojekyll), Step 6(publish step, `fetch-depth: 0`) |
| 8 | stage1 기본 Sonnet 5, 구조화 출력 | §2 L1, §3 LLM 호출 설계(`messages.parse`), §5 Step 3 |
| 9 | SDK 재시도 중첩 제거 | §3 LLM 호출 설계(`max_retries=0` + 캡), §5 Step 3 client.py |
| 10 | Google News 접미사·dedupe·story_key | §5 Step 1(접미사·`<source>`), Step 2(bigram, 언어 내 병합), Step 3(`story_key` 그룹핑) |
| 11 | deploy-pages 분리(선택) | §5 Step 6 선택 follow-up, §8 |
| 갭 | 24h 필터 tz 규칙 / 수치 주장 금지 / AC-9 자동 감축 코드 / requirements.lock + cache / Yahoo asof 배지 / AC-6 자기모순 | Step 1 tz 규칙 / Step 3 프롬프트 + Step 4 수치 대조 / AC-9 자동 감축 삭제(수동 설정) / Step 0·Step 6 / Step 1·Step 4 asof 배지 / AC-6 최종 동작 확정 |
| Steelman | Workers Cron 트리거 조합 | §2 Option B에 평가 기록, §8 follow-up |
| 트레이드오프 | 미국장 마감 EDT 05:00 KST | §1 스펙 편차(06:50 시작 무손실) |

### Critic (C-1, C-2, M-1~M-8, Minor 1~12)
| # | 지적 | 반영 위치 |
|---|------|-----------|
| C-1 | stage1 출력 토큰 사이징 | §3 토큰·비용·시간 산정표, LLM 호출 설계(16k + 압축 필드 + `messages.parse`), AC-9 |
| C-2 | 24h 필터가 월요일 US 마감 뉴스 폐기 | §3 수집 윈도(직전 영업일 06:50 KST), §5 Step 2 + 월요일 테스트, Yahoo `asof` 배지 |
| M-1 | thinking 토큰·호출 캡·잔여 시간 데드라인·SDK max_retries | §3 LLM 호출 설계·산정표(thinking 포함), §5 Step 3 client.py, AC-9 |
| M-2 | stage2 이외 실패 시 마커 없음 | §5 Step 5(최상위 BaseException + 14분 데드라인 + exit 0), Step 6(`if: always()`), Step 3 degrade, Step 4 첫 실행 폴백, AC-18 |
| M-3 | KST/UTC 날짜 미명세 | §3 KST 규칙, Step 1 tz-aware, Step 5 21:50Z 테스트, Step 6 커밋 메시지 |
| M-4 | 러너 프로브가 Step 7 | §5 Step 0.5, 소스 표 러너 열 |
| M-5 | Pages 빌드·CDN 누락, cron 드롭 | §3 시간 예산표, 06:50/07:35 정각 회피 이중 cron, `.nojekyll`, AC-15 URL curl 기준, §6 드롭 리스크 |
| M-6 | stage2 입력 편중·AI 미도달·AC-6 2회 실패 | §5 Step 3 select.py 쿼터(US 25/KR 25/MACRO 10 + is_ai 전부 + 더벨 30), AC-6 min_length 300 + 최종 동작 |
| M-7 | 수치 환각 가드 | §5 Step 4 시그널 표 직접 렌더 + 수치 대조, Step 3 프롬프트 지시, AC-5 leaders 스키마 |
| M-8 | Jinja autoescape | §5 Step 4(`select_autoescape`) + XSS 테스트 |
| Minor 1 | 모델 ID | §3 `claude-haiku-4-5` |
| Minor 2 | dedupe 유사도·KO/EN | §5 Step 2(bigram Dice 0.75, 언어 내 병합, story_key) |
| Minor 3 | git identity·rebase·fetch-depth | §5 Step 6 |
| Minor 4 | AC-9 자동 감축 과설계 | AC-9(호출 캡 + `MAX_ARTICLES` 수동), §3 config |
| Minor 5 | AC-4 스펙 편차·README 규칙 | §1 스펙 편차, AC-4, Step 0 README |
| Minor 6 | leaders 타입 | AC-5 `Leader{name, ticker?, comment}` |
| Minor 7 | feedparser에 URL 전달 금지 | §5 Step 1 rss.py + 테스트 |
| Minor 8 | 공개 리포 필수 | §5 Step 0 |
| Minor 9 | Option B CPU 수치 | §2 Option B(무료 10ms) |
| Minor 10 | ADR 수용 리스크 | §9 Consequences(드롭 가능, 알림 없음 → 08:40 확인) |
| Minor 11 | 배너 위치·#summary 첫 자식 | AC-12, §5 Step 4 템플릿 구조 |
| Minor 12 | README 런북 | §5 Step 0·Step 7, AC-19 |
| 커버리지 표 | AC-1 coverage_ok 배너 연동, AC-17 소스명 assert | AC-1, AC-17 |

## Changelog
- v1 (2026-09-14): 초안. 소스 14개 접근 테스트 반영, 더벨=Google News RSS 결정 반영.
- v2 (2026-09-15): Architect·Critic v1 리뷰 전면 반영 — (1) 이중 cron 06:50/07:35 KST(멱등 백업, 정각 회피) + `.nojekyll` + 클라이언트 stale 배너; (2) stage1·stage2 모두 `claude-sonnet-5` 기본(Haiku 4.5 설정 다운그레이드), 구조화 출력(`messages.parse`/`output_config.format`), max_tokens 16k, stage2 스트리밍 + `effort: medium` + adaptive thinking, stage1 thinking 비활성; (3) SDK `max_retries=0` + stage별 총 호출 캡(2/3) + 잔여 시간 데드라인 + 내부 데드라인 14분; (4) 수집 윈도 "직전 영업일 06:50 KST"(월요일 72h), tz-aware 파싱·소스별 `tz`, Yahoo `asof` 배지; (5) 최상위 예외 처리·`if: always()` 커밋·stage1 degrade·첫 실행 폴백·배너 중첩 방지; (6) Step 0.5 러너 프로브 + 소스 표 러너 결과 열; (7) KST 전면 적용(`--date`, 커밋 메시지, 21:50Z 테스트); (8) git bot identity·`pull --rebase` 재시도·`fetch-depth: 0`·requirements.lock·pip cache; (9) stage2 쿼터 입력 + `story_key` + Google News 접미사 제거 + bigram dedupe; (10) 수치 환각 가드(시그널 표 직접 렌더, 프롬프트 금지, 렌더 후 대조, leaders 스키마); (11) Jinja autoescape + XSS 테스트, feedparser 텍스트 파싱; (12) AC-6 최종 동작 확정(≥300자, 캡 후 경고 배너 게시), AC-9 자동 감축 삭제, AC-15 Pages URL curl 기준, AC-4 스펙 편차 명시; (13) 신설 "토큰·비용·시간 산정표", "리뷰 반영 매트릭스"; Option B CPU 수치 정정, ADR consequences 보강, README 런북, 공개 리포 필수 명시.
- v2.1 (2026-09-15): Architect v2(APPROVE_WITH_IMPROVEMENTS)·Critic v2(조건부 APPROVE) 필수 패치 — (1) 구조화 출력은 SDK `output_format=<pydantic 모델>` 헬퍼만 사용, 원시 `model_json_schema()` 전달 금지(minLength/minItems/additionalProperties 제약), 전달용(`Stage1Result{items[]}`, `ReportOut`, `extra="forbid"`, object 루트) / 검증용(`Report` validator) 모델 분리, 테스트 assert 추가; (2) 두 schedule 트리거 모두 `--skip-if-done`, skip 판정 `success|degraded`; (3) 러너 SIGKILL 경로용 `brief.fallback --reason runner_killed` step 추가, publish 스크립트 서브셸 `exit` 제거 + `rebase --abort`; (4) 산정표 정정(happy $0.27, 캡 $0.68, 월 $5.9~7.1, worst 32.5~34분, cron 허용치 66/21분, KR 입력 토큰 주석, 스키마 컴파일 캐시 행); (5) `is_ai` ≤15, degrade 시 `market`=`Article.category`, 폴백 배너 날짜 = 로드한 `data-generated`, concurrency pending 주석, 첫 월요일 금요일 기사 건수 체크.
