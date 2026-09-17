"""보고서(토픽)별 stage2 입력 선별 — 지역 쿼터 + AI 보강 + 더벨 헤드라인 분리 + story_key 그룹핑 (PLAN §11.5).

토픽: macro / sectors / us / eu / kr / cn. 각 토픽은 태그 `m`(지역) 기준 쿼터로 기사를 고르고, 태그가 없는
기사(stage1 실패·부분 실패)는 `Article.category`(US/KR/MACRO/EU/CN/GLOBAL)를 지역으로 간주한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from brief.analyze.schemas import Stage1Item, Stage1Tags
from brief.collect.base import Article
from brief.config import AI_KEYWORDS
from brief.dedupe import normalize_title

TOPICS = ("macro", "sectors", "us", "eu", "kr", "cn")
TOPIC_NAMES = {"macro": "거시경제", "sectors": "섹터별 주요뉴스", "us": "미국시장", "eu": "유럽시장", "kr": "한국시장", "cn": "중국시장"}
THEBELL_PREFIX = "더벨"

# 토픽별 지역 쿼터(중요도 내림차순으로 채움). "ANY"는 지역 무관 상위.
TOPIC_QUOTAS: dict[str, dict[str, int]] = {
    "macro": {"MACRO": 40, "GLOBAL": 10, "ANY": 8},
    "sectors": {"ANY": 45, "GLOBAL": 15},
    "us": {"US": 40, "MACRO": 8, "GLOBAL": 8},
    "eu": {"EU": 30, "MACRO": 8, "GLOBAL": 8},
    "kr": {"KR": 40, "US": 8, "MACRO": 6},
    "cn": {"CN": 30, "JP": 6, "GLOBAL": 8},
}
AI_EXTRA = {"sectors": 15, "us": 8, "kr": 6}


@dataclass
class Selected:
    topic: str
    articles: list[Article]
    tags: dict[int, Stage1Item] | None
    thebell: list[Article]
    groups: dict[str, list[Article]] = field(default_factory=dict)
    degraded: bool = False


def is_ai_keyword(title: str) -> bool:
    t = title.lower()
    return any(k.lower() in t for k in AI_KEYWORDS)


def _region(idx: int, a: Article, tags: dict[int, Stage1Item]) -> str:
    return tags[idx].m if idx in tags else a.category


def _pick(ranked: list[tuple[int, Article]], tags: dict[int, Stage1Item], quotas: dict[str, int]) -> list[tuple[int, Article]]:
    counts = {m: 0 for m in quotas}
    picked: list[tuple[int, Article]] = []
    chosen: set[int] = set()
    for region, cap in quotas.items():
        if region == "ANY":
            continue
        for idx, a in ranked:
            if idx in chosen or counts[region] >= cap:
                continue
            if _region(idx, a, tags) == region:
                counts[region] += 1
                picked.append((idx, a))
                chosen.add(idx)
    if "ANY" in quotas:
        for idx, a in ranked:
            if counts["ANY"] >= quotas["ANY"]:
                break
            if idx not in chosen:
                counts["ANY"] += 1
                picked.append((idx, a))
                chosen.add(idx)
    return picked


def _groups(picked: list[tuple[int, Article]], tags: dict[int, Stage1Item]) -> dict[str, list[Article]]:
    groups: dict[str, list[Article]] = {}
    for i, a in picked:
        key = (tags[i].k.strip() if i in tags and tags[i].k.strip() else normalize_title(a.title)) or a.url
        groups.setdefault(key, []).append(a)
    return groups


def select_for_topics(
    articles: list[Article],
    stage1: Stage1Tags | None,
    *,
    topics: tuple[str, ...] = TOPICS,
    thebell_max: int = 30,
) -> dict[str, Selected]:
    """토픽별 Selected. stage1 None → degrade(최신순, 카테고리 쿼터, AI 키워드 휴리스틱)."""
    thebell = sorted(
        (a for a in articles if a.source.startswith(THEBELL_PREFIX)),
        key=lambda a: a.published_at,
        reverse=True,
    )[:thebell_max]
    pool = [(i, a) for i, a in enumerate(articles) if not a.source.startswith(THEBELL_PREFIX)]
    tags: dict[int, Stage1Item] = {}
    if stage1 is not None:
        tags = {it.i: it for it in stage1.items if 0 <= it.i < len(articles)}
    degraded = stage1 is None
    if tags:
        ranked = sorted(pool, key=lambda t: (tags[t[0]].p if t[0] in tags else 0, t[1].published_at), reverse=True)
    else:
        ranked = sorted(pool, key=lambda t: t[1].published_at, reverse=True)

    out: dict[str, Selected] = {}
    for topic in topics:
        picked = _pick(ranked, tags, TOPIC_QUOTAS[topic])
        chosen = {i for i, _ in picked}
        ai_cap = AI_EXTRA.get(topic, 0)
        if ai_cap:
            if tags:
                extra = [(i, a) for i, a in ranked if i not in chosen and i in tags and tags[i].a]
            else:
                extra = [(i, a) for i, a in ranked if i not in chosen and is_ai_keyword(a.title)]
            picked += extra[:ai_cap]
        out[topic] = Selected(
            topic=topic,
            articles=[a for _, a in picked],
            tags={i: tags[i] for i, _ in picked if i in tags} if tags else None,
            thebell=thebell if topic in ("kr", "sectors") else [],
            groups=_groups(picked, tags),
            degraded=degraded,
        )
    return out
