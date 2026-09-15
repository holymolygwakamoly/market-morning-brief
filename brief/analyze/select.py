"""stage2 입력 선별 — 시장별 쿼터 + AI 보강 + 더벨 헤드라인 분리 + story_key 그룹핑."""
from __future__ import annotations

from dataclasses import dataclass, field

from brief.analyze.schemas import Stage1Item, Stage1Result
from brief.collect.base import Article
from brief.config import AI_KEYWORDS
from brief.dedupe import normalize_title

DEFAULT_QUOTAS: dict[str, int] = {"US": 25, "KR": 25, "MACRO": 10}
THEBELL_PREFIX = "더벨"


@dataclass
class Selected:
    articles: list[Article]
    tags: dict[int, Stage1Item] | None
    thebell: list[Article]
    groups: dict[str, list[Article]] = field(default_factory=dict)
    degraded: bool = False


def is_ai_keyword(title: str) -> bool:
    t = title.lower()
    return any(k.lower() in t for k in AI_KEYWORDS)


def _fill_quotas(
    ranked: list[tuple[int, Article]],
    market_of,
    quotas: dict[str, int],
) -> list[tuple[int, Article]]:
    counts = {m: 0 for m in quotas}
    picked: list[tuple[int, Article]] = []
    for idx, a in ranked:
        m = market_of(idx, a)
        if m in counts and counts[m] < quotas[m]:
            counts[m] += 1
            picked.append((idx, a))
    return picked


def select_for_stage2(
    articles: list[Article],
    stage1: Stage1Result | None,
    *,
    quotas: dict[str, int] | None = None,
    ai_max: int = 15,
    thebell_max: int = 30,
) -> Selected:
    quotas = dict(quotas or DEFAULT_QUOTAS)

    thebell = sorted(
        (a for a in articles if a.source.startswith(THEBELL_PREFIX)),
        key=lambda a: a.published_at,
        reverse=True,
    )[:thebell_max]
    pool = [(i, a) for i, a in enumerate(articles) if not a.source.startswith(THEBELL_PREFIX)]

    if stage1 is None:
        # degrade: 최신순 + category 쿼터 + AI 키워드 휴리스틱(≤10)
        ranked = sorted(pool, key=lambda t: t[1].published_at, reverse=True)
        picked = _fill_quotas(ranked, lambda _i, a: a.category, quotas)
        chosen = {i for i, _ in picked}
        ai_extra = [(i, a) for i, a in ranked if i not in chosen and is_ai_keyword(a.title)]
        picked += ai_extra[: min(ai_max, 10)]
        groups: dict[str, list[Article]] = {}
        for _, a in picked:
            groups.setdefault(normalize_title(a.title) or a.url, []).append(a)
        return Selected(
            articles=[a for _, a in picked], tags=None, thebell=thebell, groups=groups, degraded=True
        )

    tags = {it.i: it for it in stage1.items if 0 <= it.i < len(articles)}
    ranked = sorted(
        pool,
        key=lambda t: (tags[t[0]].p if t[0] in tags else 0, t[1].published_at),
        reverse=True,
    )
    picked = _fill_quotas(
        ranked, lambda i, a: tags[i].m if i in tags else a.category, quotas
    )
    chosen = {i for i, _ in picked}
    ai_extra = [(i, a) for i, a in ranked if i not in chosen and i in tags and tags[i].a]
    picked += ai_extra[:ai_max]
    groups = {}
    for i, a in picked:
        key = (tags[i].k.strip() if i in tags and tags[i].k.strip() else normalize_title(a.title)) or a.url
        groups.setdefault(key, []).append(a)
    return Selected(
        articles=[a for _, a in picked],
        tags={i: tags[i] for i, _ in picked if i in tags},
        thebell=thebell,
        groups=groups,
        degraded=False,
    )
