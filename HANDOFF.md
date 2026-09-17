# HANDOFF — 리서치 대시보드 (Market Research Dashboard)

> **목적**: 새 Claude Code 세션이 이 파일 + `PLAN.md`만 읽으면 지금까지의 맥락·결정·현재 상태를 그대로 이어갈 수 있게 하는 인수인계 문서.
> 사용자 불러오기 명령: **`HANDOFF.md 읽고 이어서 진행해줘`**
> 마지막 갱신: 2026-09-17 (수) — **v4.0 구현 완료·push됨. 사용 단계.** 상세 이력(v1~v3 계획, 리뷰, 결정 표 원문)은 `.omc/plans/archive/`와 git 로그에만 있음(평소엔 읽지 않아도 됨).

---

## 1. 지금 상태 한 줄

사용자가 폴더의 `시장브리핑 대시보드.lnk`로 로컬 **리서치 대시보드**를 열고, 기준일=오늘에 [업데이트](하루 1회, 약 16분)를 누르면 → 미국·유럽·한국·중국 지수/환율/금리/원자재/섹터 ETF + **S&P500·나스닥100·다우30·코스피·코스닥 전 구성종목**(등락·거래대금·시총) + 무료 뉴스 30소스를 수집 → **Claude Code CLI(Max 구독, 결제 0, opus)** 로 **거시경제 / 섹터별 주요뉴스 / 미국 / 유럽 / 한국 / 중국** 보고서 6종 생성 → 좌측 탭 SPA(`docs/`)에서 열람. 과거 기준일은 그날 저장한 스냅샷만 볼 수 있음. (선택) [GitHub에 게시]로 GitHub Pages 공개.

**진행 상황**
```
[완료] v1~v3 (09-14~15): 딥 인터뷰 → 계획 합의 → 단일 3부 보고서 + 로컬 대시보드(Claude CLI) 구현·사용
[완료] v4.0 (09-17): "리서치 대시보드" 재구성 구현(Step A~F), 실제 1회 실행 관측, push (커밋 a643c0c, 78250de)
[대기] 사용자가 대시보드에서 v4 확인 · 결정 19(robots API 기준) 확인 · (선택) GitHub Pages 설정
```

**코드 상태**: `main` push됨(`https://github.com/holymolygwakamoly/market-morning-brief`). 테스트 84개(`.venv/Scripts/python -m pytest -q`). 오늘(09-17) 스냅샷 `docs/data/2026-09-17/` 생성됨(CLI로 실행 — 하루 1회 규칙 때문에 오늘은 [업데이트] 비활성, 탭별 [재생성]만 가능).

---

## 2. 확정 규칙 (되묻지 말 것)

**비용·모델**: API 결제 없음. LLM = `claude -p`(구독). **모든 단계 opus**(`BRIEF_MODEL`). 비용 경고 없음(청구 없음).
**소스**: 무료 공개 소스만. **더벨은 Google News RSS 헤드라인만**(thebell.co.kr 직접 호출 0건, 테스트로 강제). robots 기준: 콘텐츠 페이지는 robots 준수, JSON API(Yahoo `query1/query2` 등)는 낮은 요청량·식별 UA·캐시로 사용(README §4 — **결정 19, 사용자 확인 대기**).
**대시보드**: 좌측 탭 7개(홈/거시경제/섹터별 주요뉴스/미국시장/유럽시장/한국시장/중국시장). 상단 기준일 + [업데이트]. **업데이트는 오늘(KST)만, 하루 1회**(완료된 날 409, 실패한 날은 재시도 가능). 탭별 [이 탭 재생성]은 당일 횟수 제한 없음. 과거 기준일은 저장분만(없으면 "내역 없음").
**구성종목 순위**: **미국(S&P500·나스닥100·다우30)·한국(코스피·코스닥)만**, 등락폭/거래대금/시가총액 내림차순. 유럽·중국·나스닥 종합·러셀·스톡스600은 카드만.
**홈**: 지역별 지수 카드(직전 완료 세션 기준, 5일 추이) + 환율·금리·원자재 + 섹터 ETF + **오늘의 핵심 5**(6개 보고서 headlines에서 토픽당 1개 우선 → 중요도순, 추가 호출 없음).
**보고서**: 한국어, 분량 상한 없음, 섹터 > 종목, **AI 섹터 매일 필수(≥300자, 섹터 탭)**, 입력에 없는 수치·종목 금지, 투자 조언 아님 고지. 6개 보고서는 한 번에 생성(3개 병렬), 서로 독립 실패.
**실패 처리**: 소스별 격리 + 누락 배너, 토픽별 독립 실패 + 탭 뱃지·재생성, 어떤 예외든 status.json에 기록.
**cron·Actions 없음**(PC 켜고 버튼). 보고서는 참고 자료(매매 추천 아님).

**되돌아온 이력(왜 지금 모습인지)**: 원안(v2.1)은 GitHub Actions cron 06:50 KST + Claude API로 08:30 전 자동 게시였으나 사용자가 API 결제를 원치 않아(v3) 로컬 대시보드 + CLI로 전환, 그다음 단일 보고서가 불편해서(v4) 탭·지수·구성종목 구조로 재구성.

---

## 3. 어떻게 돌아가나 (코드 안 읽어도 되게)

- **`brief/pipeline.py`** `update(date)`: market(Yahoo 45심볼 + 구성종목 5표, ≈30초) ∥ collect(뉴스 30소스, 3분 예산) → window(직전 영업일 06:50 KST~, 월요일 72h) → dedupe(소스당 40, 총 400) → **stage1** 태깅(opus, 200건 배치 ×2 병렬, 배치당 캡 2회, 480s; 지역 US/EU/KR/CN/JP/MACRO/GLOBAL·섹터·AI·story_key) → `select_for_topics`(토픽별 지역 쿼터 + AI 보강, 더벨은 kr·sectors만) → **stage2** `run_topics`(6토픽, 동시 3, 토픽당 캡 2회, 480s) → JSON 스냅샷. 내부 데드라인 45분. `regenerate(date, topics)`는 저장된 `inputs.json` + 시장 데이터로 해당 토픽 stage2만.
- **`brief/market/`**: Yahoo 차트 일봉(`range=1mo`)에서 **현지 날짜 < 기준일인 마지막 봉** = 직전 완료 세션(장중 값 혼입 방지). 구성종목 목록 = Wikipedia(S&P500, 나스닥100 목록 페이지) / stockanalysis.com(다우30, 섹터는 S&P500 목록으로 보충) / KRX KIND 상장법인 목록(코스피·코스닥, 6자리 숫자 코드만) → `.cache/`에 7일 캐시(실패 시 오래된 캐시). 시세 = Yahoo v7 배치 quote(crumb, 250심볼/요청, 시총·거래량·등락률). **한국 표는 장중에 업데이트하면 장중 값**(표에 노트). 거래대금 = 거래량×종가 추정. **KRX 수급(외국인·기관·개인) 데이터 없음**(로그인 필요) → 보고서는 기사 언급분만.
- **`brief/analyze/`**: `client.py` = `claude -p --model <m> --no-session-persistence --tools "" --output-format json --json-schema <schema> --system-prompt <sys>`(user=stdin), `--bare` 금지, 자식 env에서 `CLAUDE*` 제거, 스레드 안전. `schemas.py` = `TopicReportOut{title, overview, sections[{heading, body, bullets}], leaders, events_today, headlines[{text, importance}], data_caveats, disclaimer}`(거시·미국·유럽·한국·중국) / `SectorReportOut{…, sectors[{name, direction, markets, reason, news, leaders, etf_note}], ai_sector, cross_events}`. 검증: overview ≥150자, 섹션 ≥4개·각 ≥200자, headlines 1~3, disclaimer 고정 — **분량 규칙만 미달이면 마지막 시도에서 완화 게시**(warnings `length_short`). 프롬프트·섹션 구성은 `topics.py` `_TOPIC_SYSTEM`.
- **`brief/serve.py`** (127.0.0.1:8765, `BRIEF_PORT`): `GET /`(SPA) · `POST /api/update {date}` · `POST /api/regenerate {date, topic}` · `GET /api/status` · `POST /api/publish`(git add docs → commit → pull --rebase → push ×3) · 정적 `docs/` 루트. 포트 사용 중이면 브라우저만 열고 종료.
- **SPA** `brief/render/templates/research.html` → 서버 기동·업데이트 때마다 `docs/index.html`로 복사. 해시 라우팅 `#d=날짜&v=home|index|report&k=키`. `data/index.json`(기준일 목록) → `data/<날짜>/home.json` → 탭 클릭 시 `reports/<topic>.json`, 카드 클릭 시 `indices/<key>.json`. `/api/status`가 응답하면 로컬 모드(버튼 표시), 아니면 Pages 모드(읽기 전용).
- **출력** `docs/data/<날짜>/`: `home.json`(카드·핵심5·토픽 상태·소스 요약) · `status.json`(소스별 ok/error, 토픽별 ok/attempts/warnings, llm 토큰, market 커버리지) · `inputs.json`(재생성용 기사+태그) · `indices/*.json` · `reports/*.json`(보고서 + 근거 기사). 하루 ≈1.3MB.
- **런처**: `시장브리핑 대시보드.bat`(ASCII만 — 한글 넣으면 cmd 멈춤) + `.lnk`(`scripts/make_shortcut.ps1`).

**첫 v4 실행 관측(09-17 14:00~14:16)**: 16분, 6/6 성공, LLM 8회, 출력 117k 토큰, 구성종목 5표 전부(코스피 842·코스닥 1,784). Google News 11개 소스가 **이 PC 네트워크에서 타임아웃**(더벨 포함; 09-15에도 같은 증상 후 회복) → 유럽·중국 기사 얇음, result=degraded.

---

## 4. 열린 사항·follow-up

- **결정 19(robots API 기준)** 사용자 확인 대기. 반대 시 Yahoo 대체 소스 필요(무료 대안 사실상 없음).
- **KRX 수급**: 로그인 자격 증명은 채팅·리포에 넣지 않음. 원하면 KRX **Open API 키**(openapi.krx.co.kr, 무료 발급)를 사용자가 `.env`(gitignore)에 넣는 방식으로 어댑터 추가 가능 — 이용약관 확인 후.
- 나스닥100·다우 구성종목은 현재 Wikipedia/stockanalysis + Yahoo로 정상(102/30 전부 수집). 네이버 금융은 HTML 크롤링·약관 문제로 쓰지 않음.
- follow-up(낮음): 한국 구성종목 전일 종가 기준화(현재 장중 업데이트 시 장중 값), 유럽·중국 뉴스 소스 보강(Google News 의존), 스냅샷 정리 스크립트, 카드 클릭 시 미국·한국 외 지수 5일 차트 뷰, RSS 인코딩·pubDate None 처리, BOK 피드 오래됨.

---

## 5. 파일 위치

| 파일 | 용도 |
|------|------|
| `HANDOFF.md` (이 파일) | 인수인계. 새 결정은 2절에, 진행은 1절에 반영 |
| `PLAN.md` | **v4 설계 문서**(화면·소스·파이프라인·저장·규칙·편차·리스크). `.omc/plans/market-morning-brief-plan.md`와 동일 내용(둘 다 갱신) |
| `README.md` | 사용자용 설치·사용법·런북·소스 정책(robots 기준 포함) |
| `.omc/plans/archive/` | v2.1~v3 계획 원문, 리뷰(`reviews/`), 이전 HANDOFF(결정 0~19 표 원문) — 필요할 때만 |
| `.omc/specs/deep-interview-market-morning-brief.md` | 최초 딥 인터뷰 스펙(AC-1~19) |
| `brief/` | pipeline.py · serve.py · market/ · collect/ · analyze/ · render/templates/research.html · sources.yaml · window.py · dedupe.py · config.py |
| `tests/` (84) · `scripts/`(probe.py, make_shortcut.ps1) · `docs/`(SPA + data/; reports/·status/는 v3 보관) · `.cache/`(목록 캐시·실행 로그, gitignore) | |

프로젝트 루트: `C:\Users\jack8\OneDrive\Desktop\project\Research`.

---

## 6. 다음 세션의 Claude에게

- 사용자는 한국어로 대화. 질문은 한 번에 하나씩. 토큰 소모를 신경 쓰므로 이 문서 근거로 바로 진행.
- 시작하면 (1) `git status`/`git log -3`, (2) `docs/data/index.json`과 최신 `docs/data/<날짜>/status.json`으로 사용자가 업데이트를 해봤는지·결과(result, topics[].ok, failed_sources, market.errors)를 확인, 문제가 있으면 그것부터.
- 코드 변경 후: `.venv/Scripts/python -m pytest -q` → commit → `git push origin main`. 설정 바꾸면 대시보드 서버 재시작(포트 8765).
- Claude Code 세션 안에서 `claude -p`를 직접 테스트할 땐 `CLAUDE*` 환경변수를 빼야 한다(코드는 이미 그렇게 함).
- 실제 업데이트를 CLI로 돌릴 땐 `python -m brief.pipeline`(오늘 스냅샷을 만들어 그날 [업데이트]가 잠김을 사용자에게 알릴 것). 재생성은 `--regenerate us,kr`.
