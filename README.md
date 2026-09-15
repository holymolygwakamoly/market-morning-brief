# Market Morning Brief — 평일 아침 시장 브리핑 자동 생성기

평일(KST 월~금) 아침, PC가 꺼져 있어도 GitHub Actions에서 자동 실행되어 미국·한국 증시 뉴스와 거시 이슈를 무료 공개 소스에서 수집하고, Claude API로 **섹터 중심** 한국어 해석(AI 섹터 매일 필수)을 만들어 **핵심 요약 / 상세 분석 / 참고 자료** 3부 HTML 보고서를 **08:30 KST 이전**에 GitHub Pages로 게시합니다.

- 계획·설계: `PLAN.md` (확정 v2.1) · 진행 맥락: `HANDOFF.md`
- 실행: 주 cron 06:50 KST + 백업 cron 07:35 KST(당일 완료 시 비용 0으로 종료) + 수동 `workflow_dispatch`
- 비용: LLM API만 (회당 ≈ $0.27, 월 ≈ ₩8~10k)
- **투자 조언이 아닙니다.** 매매 판단의 참고 자료일 뿐입니다.

---

## 1. 설정 절차 (최초 1회, 사용자 작업)

1. **GitHub 공개 리포 생성** — Free 플랜은 **공개 리포에서만** GitHub Pages를 쓸 수 있고 Actions 분량도 무제한입니다. 이 폴더를 `main` 브랜치로 push합니다.
2. **시크릿 등록** — 리포 `Settings → Secrets and variables → Actions → New repository secret`
   - `ANTHROPIC_API_KEY` : Claude API 키 (유일한 시크릿)
3. **Pages 설정** — `Settings → Pages → Build and deployment → Source: Deploy from a branch` → Branch `main`, Folder `/docs` → Save.
   - `docs/.nojekyll`이 있으므로 Jekyll 빌드 없이 정적 복사됩니다.
4. **러너 소스 프로브(필수, 파이프라인 첫 실행 전)** — `Actions → Source Probe → Run workflow`. 로그의 표에서 더벨(Google News) 행이 `OK`인지 확인합니다. FAIL이면 이슈로 남기고 `PLAN.md` §3 표를 갱신하세요.
5. **첫 실행** — `Actions → Market Morning Brief → Run workflow`. 완료 후 `https://<계정>.github.io/<리포>/` 에서 `data-generated` 날짜가 오늘(KST)인지 확인합니다.

### 로컬 실행

```bash
python -m venv .venv && .venv/Scripts/activate   # Windows
pip install -r requirements.lock
set ANTHROPIC_API_KEY=...                        # 또는 .env (gitignore됨)
python -m brief.run --dry-run                    # LLM 미호출, 픽스처로 HTML 생성
python -m brief.run --date 2026-09-16            # 실제 실행(KST 날짜 지정)
python -m brief.run --skip-if-done               # 당일 status가 success|degraded면 즉시 종료
pytest
```

---

## 2. 런북 (실패 시 사용자가 할 일)

| 증상 | 확인 | 조치 |
|------|------|------|
| 08:30에 페이지가 어제 날짜 + 노란 stale 배너 | `Actions` 탭에 오늘 실행이 없음(cron 드롭) | `Market Morning Brief → Run workflow` 수동 실행 |
| 빨간 "오늘 생성 실패 HH:MM" 배너 | `Actions` 로그의 `python -m brief.run` 스텝 / `docs/status/YYYY-MM-DD.json`의 `error` | 일시 오류면 수동 재실행. LLM 오류(401/429/529)면 키·크레딧 확인 |
| 상단 "누락 소스: …" 경고 배너 | `status.json`의 `sources[].error` | 개별 소스 장애는 무시 가능. 더벨이 며칠 연속 누락이면 `Source Probe` 워크플로 실행 |
| 비용 경고(`cost_over_soft_cap`) | `status.json`의 `cost_usd`, `calls` | `brief/config.py`의 `MAX_ARTICLES` 축소 또는 stage1 모델을 `claude-haiku-4-5`로 |
| push 실패(rebase 충돌) | publish 스텝 로그 | 수동으로 `docs/`만 다시 실행하거나 재실행 |

로그 보는 법: 리포 `Actions` 탭 → 워크플로 실행 클릭 → `run` 스텝 펼치기. `docs/status/<날짜>.json`에 소스별 성공/실패, 토큰·비용, 경고가 남습니다.

### 첫 주 체크리스트 (5영업일)

| 날짜 | 주 cron 실행/완료 시각 | 백업 cron `--skip-if-done` 종료? | `curl -s <URL> \| grep -c 'data-generated="<당일>"'` 시각 (< 08:15) | `cost_usd` | 더벨 성공 | `warnings` | 비고 |
|------|------------------------|----------------------------------|------------------------------------------------------------------|-----------|-----------|------------|------|
| 월 | | | | | | | **US 기사 중 금요일(KST 토) 발행분 건수 확인** |
| 화 | | | | | | | |
| 수 | | | | | | | |
| 목 | | | | | | | |
| 금 | | | | | | | |

완료 기준: 5/5 08:15 KST 이전 게시, 평균 비용 ≤ $0.50, 더벨 러너 성공률 ≥ 80%.

참고: `concurrency` 대기 슬롯은 1개입니다. 주 실행 중 백업이 대기하는 동안 수동 실행을 누르면 백업이 취소되고 수동 실행이 대신 돕니다(결과 동일).

---

## 3. 소스 정책

- **무료 공개 소스만** 사용합니다: RSS / 공식 보도자료 피드 / Google News RSS / Yahoo 차트 JSON. 유료 API·유료 벽 우회 없음.
- **더벨(thebell.co.kr)은 직접 접근하지 않습니다.** `robots.txt`가 검색봇 외 전면 차단이므로 Google News RSS(`site:thebell.co.kr`)로 제목·링크·발행시각만 수집합니다. 코드가 `thebell.co.kr` 호스트를 호출하지 않는지 테스트로 강제합니다.
- 어댑터 타입은 `rss` / `google_news` / `yahoo_chart` 화이트리스트로 제한합니다(페이지 크롤링 없음). **새로 페이지 크롤링 타입을 추가할 때는 대상 사이트의 `robots.txt`와 이용약관을 반드시 확인하고 README의 이 절에 근거를 남겨야 합니다.**
- 시세는 Yahoo 비공식 차트 API를 씁니다. 실패해도 보고서는 게시되며 시그널 표에 "데이터 없음"과 `asof`가 표시됩니다.

## 4. 구조

```
.github/workflows/brief.yml   주/백업 cron + workflow_dispatch → python -m brief.run → docs/ 커밋
.github/workflows/probe.yml   러너에서 소스 접근 프로브(수동)
brief/collect/                rss / google_news / yahoo_chart 어댑터, collect_all()
brief/window.py, dedupe.py    수집 윈도(직전 영업일 06:50 KST) + 중복 제거
brief/analyze/                stage1 태깅 → select 쿼터 → stage2 본문 (Claude 구조화 출력)
brief/render/                 Jinja 템플릿 → docs/reports/YYYY-MM-DD.html, index.html, archive.html, status/
brief/run.py                  오케스트레이션·데드라인·폴백
docs/                         GitHub Pages 루트 (.nojekyll)
```
