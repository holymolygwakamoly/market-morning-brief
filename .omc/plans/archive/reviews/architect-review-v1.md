# Architect Review — market-morning-brief-plan.md v1 (2026-09-14)

판정: **APPROVE_WITH_IMPROVEMENTS**

## 핵심 지적
1. **마감 여유 재계산**: 실제 산식 = cron 지연 + 워크플로(≤20분) + Pages 빌드(1~3분) + CDN 캐시(최대 10분) → 07:30 시작 시 cron 지연 허용치는 약 27분뿐. Actions cron은 지연뿐 아니라 **스킵**도 발생.
2. **러너 IP 차단**: 로컬 Windows 200 응답이 Azure 러너 성공을 보장 못 함(특히 Yahoo v8 chart 429/crumb, Google News 429/consent). 파이프라인 작성 전 러너 소스 프로브 워크플로부터 돌릴 것.
3. **git 게시**: `pull --rebase` + 재시도, bot commit identity, `docs/.nojekyll` 누락.
4. **LLM 설계 버그**: stage1 `max_tokens 4k`로 200건 태깅 JSON 불가(5~8k 필요) → 절단 확정. Sonnet 5는 thinking 토큰이 max_tokens에 포함 → stage2 8k로는 한국어 장문 JSON 잘림 → 16k + 스트리밍 + effort 설정. 모델 ID `claude-haiku-4-5-20251001`은 잘못됨 → `claude-haiku-4-5`. 자체 재시도 3회 × SDK 기본 max_retries=2 중첩 → 최대 9회 호출.
5. **비용**: 실제 회당 $0.15~0.30. stage1을 Sonnet 5로 올려도 +$0.05 → stage1도 Sonnet 5 기본.
6. **폴백 구멍**: 연속 실패 시 배너 중첩; 첫 실행엔 전일 index 없음; **Python이 시작조차 못 하면(pip 실패·러너 장애·cron 스킵) 배너 없이 전일 페이지가 그대로 보임** → 사용자가 오늘 것으로 오인.

## Steelman 반론
스케줄러와 실행 환경을 분리하면 Cloudflare Workers Cron(정시성 우수, 무료)을 **트리거로만** 써서 GitHub `workflow_dispatch` REST를 호출할 수 있음(JS 20줄). 계획은 둘을 묶어 평가해 이 조합을 놓침. LLM: 가장 판단이 필요한 stage1 선별에 가장 약한 모델(Haiku)을 배치한 것은 $0.05 절약이 아님.

## 트레이드오프
시작 시각 vs 신선도 vs 마감 안전. 원칙 1(마감 최우선)에 따라 신선도 양보. 미국장 마감은 EDT 기준 05:00 KST(계획의 06:00은 EST만) → 06:50 시작도 손실 없음.

## 종합 경로(권고)
1. 주 cron `50 21 * * 0-4`(06:50 KST) + 백업 cron `35 22 * * 0-4`(07:35 KST). 백업은 당일 KST status.json 성공이면 즉시 종료(멱등, 비용 0).
2. 게시를 `actions/upload-pages-artifact` + `deploy-pages`로 분리(선택).
3. HTML에 `data-generated="YYYY-MM-DD"` + 인라인 JS로 "오늘(KST) ≠ 생성일"이면 클라이언트 stale 배너.
4. stage1·stage2 모두 `claude-sonnet-5` 기본, 구조화 출력(`messages.parse()` + pydantic)로 스키마 강제.

## 구체적 갭
- Step 6 커밋 메시지 `date -u` → UTC 날짜 = KST 전날. `--date` 기본값도 `zoneinfo("Asia/Seoul")`.
- Step 1 24h 필터: tz 없는 pubDate → KST 가정, 파싱 불가 → 유지 규칙 명시.
- google_news.py: 제목 끝 ` - 더벨` 접미사 제거, `<source>` 요소로 발행사 추출.
- dedupe: Google News 리다이렉트 URL vs 원문 URL 불일치 → 제목 접미사 제거 후 자카드. 한↔영 동일 이슈는 stage1 출력에 `story_key` 추가로 묶기.
- stage2: "입력에 없는 수치 주장 금지" 지시 필수(티커·등락률 환각 방지).
- client.py: SDK `max_retries=0` + 자체 재시도, 또는 SDK 재시도만.
- AC-9 자동 감축: 전일 status.json 읽는 코드 Step 5에 명시.
- 의존성: `requirements.lock` + setup-python cache.
- Yahoo 시세: 미국 휴장일 전전일 종가 → `asof` 배지.
- AC-6 문구 자기모순 → 허용 여부 한 줄로 확정.

## 개선 목록(중요도순)
1. stage1 max_tokens 4k → 16k
2. stage2 max_tokens 16k + 스트리밍 + effort, 모델 ID 수정
3. 이중 cron(06:50 주 + 07:35 백업, 멱등)
4. 클라이언트 측 stale 배너
5. 러너 소스 프로브 워크플로를 Step 1 이전에
6. KST 날짜 처리(커밋 메시지·--date·24h 필터)
7. push 전 pull --rebase + 재시도, bot identity, .nojekyll
8. stage1 기본 Sonnet 5, 구조화 출력
9. SDK 재시도 중첩 제거
10. Google News 접미사·dedupe·story_key
11. deploy-pages 분리(선택)
