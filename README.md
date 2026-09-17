# 리서치 대시보드 (Market Research Dashboard)

폴더 안의 **`시장브리핑 대시보드`** 바로가기를 누르면 브라우저에 대시보드가 열립니다. 상단 기준일을 **오늘**로 두고 **[업데이트]** 를 누르면(하루 1회):

1. 미국·유럽·한국·중국 지수, 환율·금리·원자재, 미국 섹터 ETF 시세와 **S&P500·나스닥100·다우30·코스피·코스닥 전 구성종목**(등락률·거래대금·시가총액)을 무료 공개 JSON에서 수집하고,
2. 30여 개 무료 뉴스 소스(RSS·Google News, 더벨은 헤드라인만)를 모아 Claude Code(구독)로 태깅한 뒤,
3. **거시경제 / 섹터별 주요뉴스 / 미국시장 / 유럽시장 / 한국시장 / 중국시장** 보고서 6종을 opus로 생성해 `docs/data/<기준일>/`에 저장합니다.

좌측 탭으로 보고서를 읽고, 홈의 지수 카드를 누르면 구성종목을 **등락폭 / 거래대금 / 시가총액** 기준으로 정렬해 볼 수 있습니다. 지난 기준일은 그날 업데이트한 스냅샷만 볼 수 있습니다(지나간 날은 새로 만들 수 없음). 약 12~20분 걸립니다.

- 계획·설계: `PLAN.md` (§11 v4) · 진행 맥락: `HANDOFF.md`
- 비용: **추가 결제 없음** — LLM은 이 PC에 로그인된 Claude Code CLI(`claude -p`, 모델 opus)를 사용하므로 Max/Pro 구독 사용량만 소모합니다. API 키 불필요.
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

1. `시장브리핑 대시보드.lnk` (또는 `.bat`) 더블클릭 → 콘솔 창이 뜨고 브라우저에 `http://127.0.0.1:8765/` 가 열립니다.
2. 기준일이 오늘인지 확인하고 **[업데이트]**. 진행 단계(collect → stage1 → stage2 → render)와 로그가 2초마다 갱신되고, 좌측 탭의 뱃지가 완료/실패로 바뀝니다.
3. **홈**: 지역별 지수 카드(전일 대비 등락·5일 추이), 환율·금리·원자재, **오늘의 핵심 5**(6개 보고서에서 추림, 클릭 시 해당 탭). 미국·한국 지수 카드를 누르면 구성종목 표(3가지 정렬).
4. **보고서 탭**: 개요 → 섹션별 서술 → 주도주 → 오늘 이벤트 → 데이터 한계 → 근거 기사 링크. 결과가 마음에 들지 않거나 실패한 탭은 상단 **[이 탭 재생성]**(당일만, 횟수 제한 없음).
5. (선택) **[GitHub에 게시]** → `docs/` 를 커밋·push 합니다. 리포의 *Settings → Pages → Deploy from a branch → main / /docs* 가 설정돼 있으면 `https://<계정>.github.io/<리포>/` 에서 폰으로도 같은 대시보드를 볼 수 있습니다(게시된 페이지에서는 업데이트·재생성 버튼이 숨겨집니다).
6. 끝나면 콘솔 창을 닫거나 `Ctrl+C`.

### 규칙
- **업데이트는 오늘(KST) 기준일만, 하루 1회.** 완료된 날은 [업데이트]가 비활성화되고 탭별 [재생성]만 됩니다. 실패(`failed`)한 날은 다시 누를 수 있습니다.
- **지난 기준일**은 상단 날짜를 바꾸면 저장된 스냅샷을 불러옵니다. 그날 업데이트하지 않았으면 "내역 없음"입니다.
- 지수 카드는 **직전 완료 세션** 기준입니다(미국·유럽 = 오늘 새벽 마감분, 한국·중국 = 전 영업일). 구성종목 표는 장 마감 후 업데이트하면 종가, **장중에 업데이트하면 장중 값**으로 표시되며 표 상단에 그 사실을 적습니다.
- 뉴스 수집 윈도: 직전 영업일 06:50 KST ~ 업데이트 시각(월요일은 금요일 미국장 마감분 포함).

### 명령줄
```bat
.venv\Scripts\python -m brief.serve                        :: 대시보드 (포트: 환경변수 BRIEF_PORT, 기본 8765)
.venv\Scripts\python -m brief.pipeline                     :: 대시보드 없이 오늘 업데이트
.venv\Scripts\python -m brief.pipeline --regenerate us,kr  :: 오늘 스냅샷에서 지정 탭만 재생성
.venv\Scripts\python scripts\probe.py [--constituents]     :: 소스 접근 진단
.venv\Scripts\python -m pytest -q
```
모델: 모든 단계 기본 `opus` (환경변수 `BRIEF_MODEL`, 단계별 `STAGE1_MODEL`/`STAGE2_MODEL`).

---

## 3. 문제가 생기면 (런북)

| 증상 | 확인 | 조치 |
|------|------|------|
| 대시보드가 안 열림 | `.bat` 콘솔에 `.venv 가 없습니다` | §1 2번 다시 실행 |
| 업데이트 → `Claude Code CLI를 찾을 수 없음` | 터미널 `claude --version` | Claude Code 설치, PATH 확인. 필요하면 환경변수 `CLAUDE_BIN` 에 전체 경로 |
| 로그에 `Not logged in` | `claude` 실행 후 `/login` | 로그인 후 실패한 탭 [재생성] 또는 (failed면) 다시 업데이트 |
| 로그에 `rate limit` / 사용량 한도 | 구독 사용량 창(5시간) 소진 | 시간 지나서 실패 탭 [재생성] |
| 빨간 "업데이트 실패" 배너 | `docs/data/<날짜>/status.json` 의 `error`, 대시보드 로그 | 일시 오류면 다시 업데이트. 같은 오류 반복이면 로그를 이슈로 |
| "생성 실패한 보고서: …" 배너 | 해당 탭의 오류 메시지 | 그 탭에서 [이 탭 재생성] |
| "누락 소스: …" 배너 | `status.json` 의 `sources[].error` | 개별 소스 장애는 무시 가능. Google News 계열이 전부 누락이면 `scripts\probe.py` 로 `news.google.com` 접속 확인(사내망 차단 등) |
| 지수 카드 일부 없음 / "시장 데이터 경고" | `home.json` 의 `market_errors` | Yahoo 일시 장애. 다음 업데이트 때 재시도 |
| 구성종목 표가 비었거나 일부만 | `docs/data/<날짜>/indices/<키>.json` 의 `error`, `rows/total` | 목록 소스(Wikipedia/KIND) 실패 시 7일 캐시(`.cache/`)를 쓰고, 그것도 없으면 빈 표. Yahoo 배치 실패 시 부분 표 |
| [GitHub에 게시] 실패 | 출력의 git 메시지 | 자격 증명(`git push` 가 터미널에서 되는지) 확인, 충돌이면 `git pull --rebase` 후 재시도 |

`docs/data/<날짜>/status.json` 에 소스별 성공/실패, 단계별 토큰(참고용 추정 비용, 청구 없음), 탭별 결과·경고가 남습니다.

---

## 4. 소스 정책

- **무료 공개 소스만.** 유료 API·유료 벽 우회 없음. 목록은 `brief/sources.yaml`.
  - 뉴스: RSS / 공식 보도자료 피드(Fed·ECB·BoE·한국은행) / Google News RSS 검색.
  - 시세: Yahoo Finance 차트·quote JSON(지수·ETF·환율·원자재·개별 종목 시세·시가총액).
  - 구성종목 목록: Wikipedia(S&P500·나스닥100, CC-BY-SA) / stockanalysis.com(다우30) / KRX KIND 상장법인 목록(코스피·코스닥). 7일 캐시.
- **더벨(thebell.co.kr)은 직접 접근하지 않습니다.** `robots.txt`가 검색봇 외 전면 차단이므로 Google News RSS(`site:thebell.co.kr`)로 제목·링크·발행시각만 수집합니다. 코드가 `thebell.co.kr` 호스트를 호출하지 않는지 테스트로 강제합니다.
- **robots.txt 적용 기준(2026-09-17 정리)**: 기사·본문 같은 **콘텐츠 페이지**는 robots.txt를 그대로 따릅니다(더벨 사례). 사이트의 자체 프론트엔드가 쓰는 **JSON API 엔드포인트**(Yahoo `query1/query2`, nasdaq `api.*` 등)의 `Disallow: /`는 검색엔진 색인 금지 목적이므로 접근 자체를 막는 것으로 보지 않되, 하루 수십 회 수준의 낮은 요청량, 식별 가능한 User-Agent, 결과 캐시로 예의를 지킵니다. 이 기준을 바꾸려면 여기와 `PLAN.md` §11.3 을 함께 고치세요.
- 제외한 소스와 이유: KRX 정보데이터시스템(로그인 없이는 `LOGOUT` 응답 → 투자자별 수급 데이터 없음, 보고서는 기사 언급분만 서술), nasdaq.com 스크리너(Yahoo로 대체 가능해 미사용), 네이버 금융(HTML 크롤링·접근 불안정), Invesco/iShares 보유내역(WAF·HTML 응답), BLS·IMF RSS(403), 미 재무부 RSS(타임아웃).
- 어댑터 타입은 `rss` / `google_news` 화이트리스트로 제한합니다(페이지 크롤링 없음). **새로 페이지 크롤링 타입을 추가할 때는 대상 사이트의 `robots.txt`와 이용약관을 반드시 확인하고 이 절에 근거를 남겨야 합니다.**

## 5. 구조

```
시장브리핑 대시보드.lnk/.bat   → .venv\Scripts\python -m brief.serve
brief/serve.py                로컬 HTTP 서버(127.0.0.1:8765): SPA, /api/update, /api/regenerate, /api/status, /api/publish, 정적 docs/
brief/pipeline.py             update(): market ∥ collect → window/dedupe → stage1 → select → 토픽 6종(병렬 3) → 스냅샷 JSON / regenerate()
brief/market/                 Yahoo 차트(직전 완료 세션)·배치 quote, 구성종목 목록(Wikipedia·stockanalysis·KIND, 캐시), MarketSnapshot
brief/collect/                rss / google_news 어댑터 (30여 소스, 지역 category)
brief/window.py, dedupe.py    수집 윈도(직전 영업일 06:50 KST) + 중복 제거(소스당 40, 총 400)
brief/analyze/                Claude Code CLI 엔진(client.py), stage1 태깅(배치·지역), 토픽별 select, topics.py(보고서 6종 프롬프트·스키마·병렬)
brief/render/templates/research.html   정적 SPA(좌측 탭·기준일·지수 카드·구성종목 3정렬·보고서 뷰) → docs/index.html 로 복사
docs/                         index.html(SPA) · data/index.json(기준일 목록) · data/<날짜>/{home,status,inputs}.json, indices/*.json, reports/*.json
                              reports/·status/ 는 v3(단일 보고서) 시절 결과 보관. GitHub Pages 루트(.nojekyll)
```
