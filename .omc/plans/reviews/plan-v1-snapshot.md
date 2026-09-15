# Plan: Market Morning Brief — 평일 아침 시장 브리핑 자동 생성기

- Status: **pending approval** (consensus in progress)
- Source spec: `.omc/specs/deep-interview-market-morning-brief.md` (ambiguity 15.4%)
- Mode: RALPLAN-DR short
- Iteration: 1
- Date: 2026-09-14

---

## 1. Requirements Summary

평일 07:30 KST에 GitHub Actions에서 자동 실행되어, 무료 공개 소스(RSS/공식 사이트/Google News RSS/Yahoo 차트 JSON)에서 미국·한국 증시 뉴스와 거시 이슈를 수집하고, Claude API로 섹터 중심(이슈/상승/하락 섹터 + 이유 + 대표주, AI 섹터 필수) 한국어 해석을 만들어, 핵심 요약/상세 분석/참고 자료 3부 HTML 보고서를 08:30 KST 이전에 GitHub Pages 공개 URL로 게시한다. 소스 일부 실패 시 부분 발행 + 누락 배너, LLM 실패 시 전일 유지 + 실패 표시. 더벨은 Google News RSS 헤드라인만(직접 크롤링 금지). 비용은 LLM만, 월 1~2만 원.

## 2. RALPLAN-DR Summary

### Principles
1. **08:30 마감이 최우선** — 품질보다 정시 게시. 어떤 단계도 마감을 넘기며 재시도하지 않는다(단계별 타임박스).
2. **부분 실패는 정상 경로** — 소스는 죽는다. 소스별 격리, 결과는 항상 게시, 누락은 배너로 정직하게.
3. **소스 정책 준수** — robots.txt/이용약관 위반 없음. 더벨은 Google News 헤드라인만.
4. **비용 상한 내 결정론적 파이프라인** — LLM 호출 횟수·입력 토큰 상한을 코드로 고정. 예산 초과 시 기사 수를 줄이지 호출을 늘리지 않는다.
5. **사용자 개입 0** — PC·계정 로그인 없이 돌아가야 한다. 시크릿 1개(ANTHROPIC_API_KEY)만.

### Decision Drivers (top 3)
1. PC 꺼져 있어도 08:30 전 완료 → 무료 클라우드 스케줄러 필수
2. 유료 API 금지 → RSS/공식 피드/무료 JSON만, 크롤링 최소화
3. 월 1~2만 원 LLM 예산 → 1회 실행 ≤ 약 700원 (≈ $0.50)

### Viable Options

**Option A (선택): GitHub Actions cron + Python 파이프라인 + GitHub Pages 정적 HTML**
- Pros: 무료(공개 리포 무제한 / 비공개 월 2,000분, 1회 ≈ 5분 → 월 ~110분), 시크릿 관리 내장, `workflow_dispatch`로 수동 실행(AC-19), Pages는 리포 커밋만으로 배포, 아카이브가 git 히스토리로 자연 보존
- Cons: cron 지연 수 분~수십 분(피크 시) → 07:30 예약해 60분 여유 확보; 리포가 공개면 코드·보고서 모두 공개(사용자 OK); Actions 러너 IP가 일부 사이트에서 차단될 가능성(RSS는 대개 무관)

**Option B: Cloudflare Workers Cron + KV/R2 + Workers 정적 서빙**
- Pros: cron 정시성 우수, 엣지 실행, 무료 티어 넉넉
- Cons: Python 불가(JS/TS) → feedparser 등 생태계 손실, 30초 CPU 제한(무료)으로 LLM 장문 생성·수십 개 피드 파싱에 부적합, 아카이브 저장소 별도 설계 필요

**Option C: 사용자 PC + Windows 작업 스케줄러 (로그온 시 트리거)**
- Pros: 가장 단순, 외부 계정 불필요
- Cons: 사용자 1순위 요구(PC 꺼져 있어도 08:30 전 완료) 불충족 — 08:40 켜면 08:45 이후 완성. **주 경로로 invalidated**; Option A 장애 시 백업 경로로만 유지(범위 밖, follow-up)

**LLM 호출 구조 옵션**
- L1 (선택): 2단계 — (1) 기사 메타데이터(제목+요약 200자) 일괄 → 중요도·섹터 태깅 JSON(Haiku 4.5 또는 Sonnet 5, 1회) → (2) 상위 N개 + 시세 + 태그 → 보고서 본문 생성(Sonnet 5, 1회). 호출 2회, 입력 ≤ 40k 토큰, 출력 ≤ 8k 토큰 → 회당 약 $0.25~0.45.
- L2: 1회 호출로 전부 — 입력 토큰 폭증(원문 100~300건), 품질 불안정. 기각.
- L3: 기사별 개별 요약 호출 — 호출 수백 회, 비용·시간 초과. 기각.

## 3. Architecture

```
.github/workflows/brief.yml   cron 30 22 * * 0-4 (UTC) = KST 월~금 07:30, + workflow_dispatch
    └─ python -m brief.run
         ├─ collect/     소스 어댑터 (rss, google_news, yahoo_chart) — 각각 timeout 15s, 재시도 2회, 예외 격리
         ├─ dedupe.py    URL 정규화 + 제목 유사도(간단 토큰 자카드)로 중복 제거, 최근 24h 필터
         ├─ analyze/     stage1_tag.py (중요도/섹터/시장 태깅 JSON) → stage2_write.py (보고서 본문 JSON)
         ├─ render/      jinja2 → docs/reports/YYYY-MM-DD.html, docs/index.html (=최신), docs/archive.html
         ├─ status.py    run_status.json (소스별 ok/fail/count, 토큰 사용량, 비용 추정, 실행 시각)
         └─ fallback     LLM 실패 시 전일 index.html 유지 + 실패 배너 삽입
    └─ git commit docs/ && push  → GitHub Pages (Source: /docs on main)
```

- 언어: Python 3.12. 의존성: `feedparser`, `httpx`, `anthropic`, `jinja2`, `pydantic`, `python-dateutil`. (`beautifulsoup4`는 RSS description HTML 정리용만; 페이지 크롤링 없음)
- 소스 설정: `brief/sources.yaml` — name, type, url, category(US/KR/MACRO), required(bool), lang.
- 타임박스: 수집 ≤ 4분(전체), stage1 ≤ 3분, stage2 ≤ 5분, 렌더+커밋 ≤ 1분. 워크플로 `timeout-minutes: 20`.

### 검증된 소스 목록 (2026-09-14 접근 테스트 결과)
| 소스 | URL | 카테고리 | 결과 |
|------|-----|---------|------|
| CNBC Top News | `https://www.cnbc.com/id/100003114/device/rss/rss.html` | US | OK 30건 |
| CNBC Finance | `https://www.cnbc.com/id/10000664/device/rss/rss.html` | US | OK 30건 |
| MarketWatch Top | `https://feeds.content.dowjones.io/public/rss/mw_topstories` | US | OK 10건 |
| Yahoo Finance | `https://finance.yahoo.com/news/rssindex` | US | OK 50건 |
| Fed 보도자료 | `https://www.federalreserve.gov/feeds/press_all.xml` | MACRO | OK 20건 |
| 연합뉴스 경제 | `https://www.yna.co.kr/rss/economy.xml` | KR | OK 120건 |
| 한국경제 증권 | `https://www.hankyung.com/feed/finance` | KR | OK 50건 |
| 매일경제 증권 | `https://www.mk.co.kr/rss/50200011/` | KR | OK 50건 |
| 한국은행 보도자료 | `https://www.bok.or.kr/portal/bbs/B0000245/news.rss?menuNo=200761` | MACRO | OK 100건 |
| **더벨 (Google News)** | `https://news.google.com/rss/search?q=site:thebell.co.kr&hl=ko&gl=KR&ceid=KR:ko` | KR, **required** | OK 100+건 (간헐 타임아웃 → 재시도 3회) |
| Google News 「AI 주식」 | `.../rss/search?q=AI+stocks&hl=en-US&gl=US&ceid=US:en` | US/AI | 보강용 |
| Google News 「코스피」 | `.../rss/search?q=코스피&hl=ko&gl=KR&ceid=KR:ko` | KR | OK 91건 |
| Yahoo 차트 JSON | `https://query1.finance.yahoo.com/v8/finance/chart/{^GSPC,^IXIC,^DJI,^KS11,^KQ11,KRW=X,^TNX,CL=F}` | 시세 | OK |
| stooq CSV | — | — | 404, 제외 |

## 4. Acceptance Criteria (스펙 AC-1~19 상속 + 계획 수준 구체화)

- [ ] AC-1: `run_status.json`에 소스별 `{ok, count, error}` 기록, US/KR/MACRO 각 ≥1 성공 시 `coverage_ok=true`
- [ ] AC-2: 더벨 어댑터는 `news.google.com`만 호출. 테스트: httpx 모킹으로 요청 호스트 목록에 `thebell.co.kr` 부재 assert
- [ ] AC-3: 단위 테스트 — 소스 절반이 `TimeoutError`를 던져도 `collect_all()`이 나머지 결과 반환
- [ ] AC-4: `sources.yaml`에 크롤링 타입 없음(rss/google_news/yahoo_chart만). robots.txt 체크는 페이지 크롤링이 없으므로 미적용 — 대신 어댑터 타입 화이트리스트 테스트
- [ ] AC-5: stage2 출력 JSON 스키마(pydantic): `sectors: list[{name, direction: issue|up|down, market, reason, leaders: 1..5}]` 검증 실패 시 1회 재생성
- [ ] AC-6: 스키마에 `ai_sector` 필드 필수(non-empty). 없으면 stage2 재호출 1회, 그래도 없으면 "AI 섹터: 오늘 특이 뉴스 없음 — 최근 흐름: …" 템플릿 폴백 금지 → 프롬프트에 AI 섹터 필수 명시 + 검증
- [ ] AC-7: 렌더 후 2부에서 섹터 블록 문자 수 > 대표주 블록 문자 수 — 렌더 테스트로 계산, 위반 시 status에 경고
- [ ] AC-8: 섹터 스키마 내 `us_view`, `kr_impact` 두 필드 필수(US 시장 섹터에 한해)
- [ ] AC-9: `run_status.json.cost_usd` ≤ 0.50; 초과 시 다음 실행부터 stage1 입력 기사 수 자동 감축(200→120)
- [ ] AC-10: 템플릿에 `#summary`, `#analysis`, `#references` 3개 섹션 고정. 렌더 테스트로 존재 확인
- [ ] AC-11: `lang="ko"`, viewport meta, 모바일 CSS. 수동 확인 1회
- [ ] AC-12: `#summary`가 `<main>` 첫 자식
- [ ] AC-13: `docs/reports/YYYY-MM-DD.html` 누적 + `docs/archive.html` 목록 자동 생성
- [ ] AC-14/16: cron `30 22 * * 0-4` UTC. 검증: 워크플로 파일 + 첫 주 실행 로그 5회 확인. (KST 공휴일은 범위 밖 — 실행돼도 무해)
- [ ] AC-15: Actions 로그의 완료 시각 < 23:30 UTC. 첫 주 5회 모두 충족
- [ ] AC-17: `status.failed_sources` 비어있지 않으면 템플릿 상단 `.banner-warn` 렌더 — 테스트
- [ ] AC-18: stage2가 최종 실패하면 전일 `index.html` 읽어 `.banner-error`("오늘 생성 실패 HH:MM KST") 삽입 후 저장 — 테스트
- [ ] AC-19: `workflow_dispatch` 존재 + 로컬 `python -m brief.run --date 2026-09-15` 동작

## 5. Implementation Steps

### Step 0 — 리포 부트스트랩 (사용자 작업 1회 포함)
- `git init`, `.gitignore`, `pyproject.toml`(deps 고정), `README.md`(설정 절차)
- **사용자**: GitHub 리포 생성(공개 권장 — Actions 무제한), `ANTHROPIC_API_KEY` 시크릿 등록, Pages Source = `main` / `/docs`
- 파일: `pyproject.toml`, `README.md`, `.github/workflows/brief.yml`

### Step 1 — 수집 계층 `brief/collect/`
- `base.py`: `Article(title, url, published_at, source, category, summary)` pydantic 모델, `SourceResult(ok, count, error, articles)`
- `rss.py`: feedparser + httpx(timeout 15s, retry 2, backoff 2s) — description HTML strip, 24h 필터
- `google_news.py`: rss.py 상속, retry 3, `source_name` 강제(더벨), Google 리다이렉트 링크 그대로 보관(원문 URL 해석 위해 thebell 호출 금지)
- `yahoo_chart.py`: 8개 심볼 JSON → `Quote(symbol, name, close, change_pct, asof)`
- `__init__.py::collect_all(sources)`: `ThreadPoolExecutor(8)` + 전체 4분 데드라인, 예외 격리
- `sources.yaml`: 위 검증 표 그대로
- 테스트: `tests/test_collect.py` (모킹: 절반 타임아웃, 더벨 호스트 assert, 24h 필터)

### Step 2 — 중복 제거 `brief/dedupe.py`
- URL 정규화(utm 제거), 제목 토큰 자카드 ≥ 0.8 병합, 소스 다양성 유지(소스당 최대 40건, 전체 상한 200건)
- 테스트: 동일 기사 3소스 → 1건

### Step 3 — 분석 계층 `brief/analyze/`
- `stage1_tag.py`: 입력 = 기사 목록(제목+요약 ≤200자, 번호) → 출력 JSON `[{id, importance 1-5, sectors[], market, is_ai}]`. 모델 `claude-haiku-4-5-20251001`(저비용) — 품질 미달 시 Sonnet 5로 승격 가능하게 설정화. max_tokens 4k
- `stage2_write.py`: 입력 = importance ≥3 상위 60건 + 시세 + 더벨 헤드라인 전체(제목만, 최대 30건) → 출력 JSON(스키마: `headline5[]`, `signals[]`, `sectors[]`(각 direction/reason/leaders/us_view/kr_impact), `ai_sector`, `events_today[]`, `macro_notes`). 모델 `claude-sonnet-5`, max_tokens 8k, 한국어 지시, 섹터 > 종목 비중 지시, "투자 조언 아님" 고지 포함
- `client.py`: anthropic SDK, 재시도 3회(지수 백오프 5/15/45s, 총 ≤ 3분), usage 집계 → cost_usd 계산
- `schemas.py`: pydantic 스키마 + 검증 실패 시 1회 재생성
- 테스트: 스키마 검증, 모킹 응답으로 AI 섹터 누락 시 재호출

### Step 4 — 렌더 `brief/render/`
- `templates/report.html.j2`: 3섹션, 배너 슬롯, 모바일 CSS 인라인(외부 CDN 없음), 3부에 소스 상태 표 + 기사 링크 전체
- `templates/archive.html.j2`
- `render.py`: `docs/reports/{date}.html`, `docs/index.html`(복사), `docs/archive.html`, `docs/status/{date}.json`
- 테스트: 섹션 존재, 배너 조건, AC-7 문자 수 비교

### Step 5 — 오케스트레이션 `brief/run.py` + 폴백
- 순서: collect → dedupe → stage1 → stage2 → render → status. 각 단계 타임박스.
- stage2 최종 실패: 전일 `index.html` 로드 → `.banner-error` 삽입 → 저장, exit 0(커밋은 진행)
- CLI: `--date`, `--dry-run`(LLM 미호출, 픽스처 사용), `--max-articles`
- 테스트: `--dry-run` E2E가 HTML 3파일 생성

### Step 6 — 워크플로 `.github/workflows/brief.yml`
- `on: schedule: - cron: '30 22 * * 0-4'`, `workflow_dispatch`
- steps: checkout → setup-python 3.12 → pip install → `python -m brief.run` → `git add docs && git commit -m "brief: $(date -u +%F)" && git push`(변경 없으면 skip)
- `timeout-minutes: 20`, `permissions: contents: write`, `concurrency: brief`

### Step 7 — 첫 실행 및 튜닝
- `workflow_dispatch`로 수동 1회 → Pages URL 확인 → 프롬프트 톤/길이 조정 1~2회
- 첫 주 5회 자동 실행 모니터링(완료 시각, 비용, 누락 소스)

## 6. Risks & Mitigations
| 위험 | 영향 | 완화 |
|------|------|------|
| GitHub Actions cron 지연(피크 시 최대 30~60분 보고됨) | 08:30 초과 | 07:30 예약(60분 여유). 첫 주 관측 후 필요 시 07:00로 앞당김(미국장 마감 06:00 이후라 데이터 손실 없음) |
| Google News RSS 간헐 타임아웃(로컬 테스트에서 재현) | 더벨 누락 | retry 3 + 20s timeout; 실패 시 배너에 "더벨 누락" 명시(AC-17). Actions 러너에서는 별도 확인(Step 7) |
| Actions 러너 IP가 Yahoo/CNBC에 차단 | 소스 누락 | User-Agent 지정, 다중 소스 중복(US 4개), 누락 배너 |
| LLM 출력 JSON 깨짐 | 렌더 실패 | pydantic 검증 + 1회 재생성 + 최종 폴백(전일 유지) |
| 비용 초과 | 예산 위반 | 입력 상한(200→120건 자동 감축), 모델 선택 설정화, status에 누적 비용 |
| Yahoo 차트 비공식 API 변경 | 시장 시그널 누락 | 시세 실패는 비치명(시그널 섹션 "데이터 없음"), 대체 후보 문서화 |
| 한국 공휴일 실행 | 무의미한 보고서 | 범위 밖(비목표). 미국장 뉴스는 여전히 유효하므로 무해 |
| 공개 리포에 API 키 노출 | 보안 | GitHub Secrets만 사용, `.env` gitignore, 코드에 키 하드코딩 금지 테스트(grep) |

## 7. Verification Steps
1. `pytest` 전부 통과(수집 격리, 더벨 호스트, 스키마, 렌더 섹션, 배너, dry-run E2E)
2. 로컬 `python -m brief.run --dry-run` → `docs/index.html` 브라우저 확인(데스크톱+모바일 폭)
3. 로컬 실제 실행 1회(API 키) → `status.cost_usd ≤ 0.50`, AI 섹터 존재, 섹터>종목 문자 수 확인
4. `workflow_dispatch` 1회 → Actions 로그 완료 시각, Pages URL에서 당일 보고서 열림
5. 첫 5영업일 자동 실행 로그: 완료 시각 < 23:30 UTC 5/5, 더벨 수집 성공률 기록

## 8. Out of Scope (follow-ups)
- 로컬 백업 실행(Windows 작업 스케줄러 로그온 트리거)
- 한국 공휴일 스킵
- 실패 알림 채널(이메일/텔레그램)
- 저녁(미국장 개장 전) 2차 브리핑

## 9. ADR
- **Decision**: GitHub Actions cron(07:30 KST) + Python 2단계 LLM 파이프라인 + GitHub Pages(/docs) 정적 HTML.
- **Drivers**: PC 무관 08:30 전 완료 / 무료 소스·무료 인프라 / LLM 월 1~2만 원.
- **Alternatives considered**: Cloudflare Workers(JS 제약·CPU 제한), 사용자 PC 스케줄러(1순위 요구 불충족), 1회/기사별 LLM 호출(비용·품질).
- **Why chosen**: 유일하게 세 드라이버를 동시에 만족하고 Python 생태계(feedparser, anthropic)를 그대로 쓰며 아카이브가 git으로 공짜.
- **Consequences**: cron 지연 리스크를 60분 여유로 흡수; 리포 공개 시 코드 공개; Yahoo 비공식 API 의존(비치명).
- **Follow-ups**: 8절 항목 + 첫 주 관측 후 cron 시각 조정.

## Changelog
- v1 (2026-09-14): 초안. 소스 14개 접근 테스트 반영, 더벨=Google News RSS 결정 반영.
