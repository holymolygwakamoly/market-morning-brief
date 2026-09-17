# PLAN — 리서치 대시보드 v4 (구현 완료 2026-09-17)

> 이 문서는 **현재 구현된 v4의 설계**를 설명한다. v1~v3 계획·리뷰·산정표 원문은 `.omc/plans/archive/plan-v2.1-v3-history.md`(필요할 때만).
> 맥락·결정·진행은 `HANDOFF.md`, 사용법·런북은 `README.md`.

## 1. 목표

사용자(한국 개인투자자, 미국·한국 주식)가 아침에 PC를 켜고 버튼 하나로 **전날 세계 시장(미국·유럽·한국·중국)과 거시·섹터 이슈**를 상세 보고서와 데이터로 받아 오늘 매매 판단에 참고한다. 무료 공개 소스만, LLM은 Claude Code CLI(구독, opus), 추가 결제 0.

## 2. 화면

| 영역 | 내용 |
|------|------|
| 상단 바 | 기준일 date input(기본 오늘 KST, max=오늘) · [업데이트](오늘·미완료·로컬일 때만 활성) · [이 탭 재생성](보고서 탭·오늘·로컬) · 상태 문구 · [GitHub에 게시](원격 있을 때) |
| 좌측 탭 | 홈 · 거시경제 · 섹터별 주요뉴스 · 미국시장 · 유럽시장 · 한국시장 · 중국시장 (탭마다 완료/실패/없음/생성 중 뱃지) |
| 홈 | 오늘의 핵심 5(클릭 → 탭) → 지수 카드 그룹(미국·유럽·한국·중국·아시아·환율·금리·원자재·섹터ETF; 종가·전일 대비·세션 날짜·5일 스파크라인) → 보고서·소스·구성종목 커버리지 칩 → 저장된 기준일 목록 |
| 지수 상세 | 미국 3·한국 2 지수만. 상승/하락 종목 수, 상위 5 상승·하락, 서브탭 **등락폭 / 거래대금 / 시가총액** 내림차순 표(종목·티커·섹터·종가·등락률·거래량·거래대금(추정)·시총). 장중 값이면 노트 |
| 보고서 탭 | 제목·개요 → 섹션(제목/본문/불릿) → 주도주 카드 → 오늘 이벤트 → 데이터 한계 → 고지 → 근거 기사 링크(+ 한국·섹터는 더벨 헤드라인). 섹터 탭은 섹터별 블록(방향·시장·이유·뉴스·대표주·ETF) + AI 섹터 + 다중 섹터 이벤트 |
| 진행 표시 | 실행 중 단계(collect/stage1/stage2/render)·경과·로그 tail, 2초 폴링 |

정적 SPA 하나(`brief/render/templates/research.html` → `docs/index.html`). 로컬 서버(`/api/*` 응답)면 버튼 표시, GitHub Pages면 읽기 전용. 해시 라우팅 `#d=날짜&v=home|index|report&k=키`.

## 3. 기준일과 세션

- 기준일 D = KST 날짜. **업데이트는 D == 오늘일 때만, 하루 1회**(status가 success/degraded면 잠김, failed면 재시도 가능). 지난 D는 그날 저장된 스냅샷만 표시.
- **직전 완료 세션** = Yahoo 일봉(`range=1mo`)에서 거래소 현지 날짜 < D인 마지막 봉. 미국·유럽은 D 새벽 KST 마감분, 한국·중국은 D−1 영업일. 전일 대비는 그 앞 봉과 비교.
- 뉴스 윈도: 직전 영업일 06:50 KST ~ 업데이트 시각(월요일은 금요일 06:50부터 ≈72h).

## 4. 데이터 소스 (확정, 2026-09-17 프로브)

| 데이터 | 소스 | 비고 |
|--------|------|------|
| 지수·환율·금리·원자재·섹터 ETF (45심볼) | Yahoo `v8/finance/chart` | `^HSTECH` 404, `^KS200`·`000300.SS`는 일봉 없음 → 제외 |
| 구성종목 시세(시총·거래량·등락률) | Yahoo `v7/finance/quote` 배치(crumb, 250심볼/요청) | 미국 ≈635 + 한국 ≈2,630 심볼 ≈ 12요청 |
| S&P500 목록 | Wikipedia `List_of_S%26P_500_companies` (#constituents, GICS 섹터) | 7일 캐시 `.cache/members_*.json` |
| 나스닥100 목록 | Wikipedia `List_of_NASDAQ-100_companies` (ICB Industry) | 본문 `Nasdaq-100` 페이지엔 표 없음 |
| 다우30 목록 | stockanalysis.com `/list/dow-jones-stocks/` (robots 허용) | 섹터는 S&P500 목록에서 보충 |
| 코스피·코스닥 목록 | KRX KIND `corpList.do?method=download&marketType=stockMkt|kosdaqMkt` (EUC-KR, 회사명·코드·업종) | 영문 포함 신규 코드 제외 |
| 뉴스 30소스 | RSS(CNBC 2, MarketWatch, Yahoo, 연합, 한경, 매경, Fed, BOK, ECB, BoE) + Google News RSS 검색(미국 3, 거시 2, 한국 3(더벨 포함), 유럽 2, 중국 3(중문 1), 섹터·지정학 5) | category US/KR/MACRO/EU/CN/GLOBAL |
| 제외 | KRX 정보데이터시스템(로그인 없이 `LOGOUT`) · nasdaq.com 스크리너(robots, Yahoo로 대체) · 네이버 금융(HTML·약관) · Invesco/iShares(403/HTML) · BLS·IMF(403) · 미 재무부(타임아웃) | |

**robots 기준**: 콘텐츠 페이지는 robots 준수(더벨 → Google News 헤드라인만). JSON API 엔드포인트의 `Disallow: /`(Yahoo query1/2)는 색인 금지 목적으로 보고 낮은 요청량·식별 UA·캐시로 사용(README §4, 결정 19 확인 대기). 어댑터 타입은 `rss`/`google_news`만(페이지 크롤링 없음).

## 5. 파이프라인 (`brief/pipeline.py`)

```
update(D)   [서버: D==오늘 & 미완료 검증]
 1. market  ∥ collect   Yahoo 45심볼(병렬 8) + 구성종목 5표(목록 캐시 + v7 배치)  ‖  뉴스 30소스(ThreadPool 8, 3분)
 2. window → dedupe     소스당 40, 총 400 (URL·제목 bigram 유사도 병합, 라운드로빈)
 3. stage1 (opus)       200건 배치, 2배치 병렬, 배치당 캡 2회, 480s. 태그: p(1~5), m(지역), a(AI), s(섹터≤3), k(story_key). 일부 배치 실패 → partial, 전부 실패 → degrade(카테고리 기준 선별)
 4. select              토픽별 지역 쿼터(macro MACRO40+GLOBAL10+ANY8 / sectors ANY45+GLOBAL15 / us US40+MACRO8+GLOBAL8 / eu EU30+… / kr KR40+US8+MACRO6 / cn CN30+JP6+GLOBAL8) + AI 보강(sectors 15, us 8, kr 6). 더벨 ≤30은 kr·sectors만
 5. stage2 (opus)       6토픽, 동시 3, 토픽당 캡 2회, 480s. 입력 = 토픽별 시장 데이터 표 + 기사 그룹(+더벨). 검증 실패 사유를 붙여 재시도, 분량 규칙만 미달이면 마지막에 완화 게시
 6. 저장                docs/data/D/{home,status,inputs}.json, indices/*.json, reports/*.json → data/index.json 갱신, SPA 복사
 내부 데드라인 45분. 어떤 예외든 status failed 기록.
regenerate(D, topics)   inputs.json + home/indices에서 시장 데이터 복원 → 해당 토픽 stage2만 → reports/status/home(핵심5·토픽 상태) 갱신
```

관측(첫 실행): 16분(stage1 3분, stage2 11분), LLM 8회, 출력 117k 토큰.

## 6. 보고서 구조 (프롬프트 `brief/analyze/topics.py`)

| 탭 | 섹션 |
|----|------|
| 거시경제 | 금리·채권 / 환율 / 원자재 / 중앙은행 / 경제지표 / 지정학·정책 / 자산별 함의 |
| 미국시장 | 지수 흐름 / 시장 폭과 거래(수급 데이터 없음 명시) / 섹터 동향(ETF) / 주도주와 급등락(구성종목 표) / 실적·기업 뉴스 / 오늘 이벤트 / 한국장 영향 |
| 유럽시장 | 지수 흐름 / 섹터·주도주 / ECB·BoE·거시 / 주요 뉴스 / 미국·한국 영향 |
| 한국시장 | 지수 흐름 / 수급(기사 언급분만, KRX 데이터 없음 명시) / 업종 동향 / 주도주와 급등락 / 주요 뉴스·더벨 / 오늘 이벤트 / 오늘 장 참고 |
| 중국시장 | 지수 흐름 / 정책·유동성 / 섹터·주도주 / 주요 뉴스 / 한국 영향 |
| 섹터별 주요뉴스 | 이슈·상승·하락 섹터 5~12개(시장·이유·뉴스·대표주·ETF) / **AI 섹터(매일, ≥300자)** / 여러 섹터에 걸친 이벤트(전쟁·관세 등) |

공통: 한국어, 분량 상한 없음, 입력에 없는 수치·종목 금지, headlines 1~3(중요도 1~5) → 홈 핵심 5, events_today, data_caveats, disclaimer 고정 문구. 검증: overview ≥150자, 섹션 ≥4·각 ≥200자(섹터: 섹터 ≥3·이유 ≥100자, AI ≥300자).

## 7. 저장 구조·게시

```
docs/index.html                 SPA (템플릿 복사본)
docs/data/index.json            {dates:[{date,result,updated_at_kst,topics_ok}]}
docs/data/<D>/home.json         indices[], constituents 요약, headlines[], topics{}, sources 요약, warnings, market_errors
docs/data/<D>/status.json       result, sources[], failed_sources, topics{ok,error,warnings,attempts}, llm, market, elapsed_s
docs/data/<D>/inputs.json       재생성용 기사·태그
docs/data/<D>/indices/<key>.json   ConstituentTable(rows[], total, session_date, note, source)
docs/data/<D>/reports/<topic>.json report + articles[] + thebell[]
docs/reports/, docs/status/     v3 결과 보관(읽기만)
```
게시: [GitHub에 게시] = `git add docs && commit && pull --rebase && push`(3회 재시도). Pages(main //docs)에서 같은 SPA가 읽기 전용으로 열림. 하루 ≈1.3MB.

## 8. 계획 대비 편차·리스크

| 항목 | 상태 |
|------|------|
| 한국 수급(외국인·기관·개인) | KRX 로그인 필요 → 데이터 없음. 기사 언급분만 서술. 대안: KRX Open API 키(사용자 발급, `.env`) — 미구현 |
| 한국 구성종목 표 | 장중 업데이트 시 장중 값(노트 표시). 전일 종가 기준화는 2,600회 차트 호출 필요 → 미적용 |
| 유럽·중국 뉴스 | Google News 의존. 이 PC 네트워크에서 간헐 접속 불가(09-15·09-17) → 누락 배너, 보고서 얇아짐 |
| Max 사용량 | 1회 ≈117k 출력 토큰. 재생성 반복 시 5시간 창 한도 가능 → CLI 오류를 탭에 표시 |
| Yahoo 의존 | 비공식 JSON. 변경·429 시 카드/표 누락(비치명). robots 기준은 결정 19 |
| 스냅샷 용량 | 월 ≈40MB 증가. 정리 스크립트 follow-up |

## 9. Changelog(요약)

- v1~v2.1 (09-14~15): 딥 인터뷰 → Architect/Critic 합의 → GitHub Actions cron + Claude API 설계·구현(원문은 archive).
- v3 (09-15): API 결제 없이 → 로컬 대시보드 + Claude Code CLI, 전 단계 opus.
- v4.0 (09-17): 리서치 대시보드 — 좌측 탭 7개, 보고서 6종, 지수 카드·구성종목 3정렬, 기준일 스냅샷, 당일·하루 1회 업데이트, v3 단일 보고서 경로 제거. 테스트 84개.
