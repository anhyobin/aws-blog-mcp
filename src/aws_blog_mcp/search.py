"""Local keyword search over post metadata.

The directory API's own full-text search only matches whole whitespace-delimited words, so Korean
queries with particles (``쿠버네티스를``) or compounds miss most posts. Searching the in-memory
index instead lets us:

* match Hangul as substrings and strip a trailing particle from query words,
* match ASCII words at word starts (so ``eks`` does not match "weeks"),
* expand a small table of Korean/English synonyms (``쿠버네티스`` ↔ ``kubernetes``),
* rank by where terms match (title > categories > excerpt > slug > authors).

Only metadata is searchable (title, excerpt, categories/tags, slug, authors); post bodies are not
indexed.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

from aws_blog_mcp.errors import InvalidInputError

MatchMode = Literal["all_terms", "any_term"]

FIELD_WEIGHTS: dict[str, float] = {
    "title": 5.0,
    "categories": 3.0,
    "excerpt": 2.0,
    "slug": 1.5,
    "authors": 1.0,
}
STRIPPED_PARTICLE_FACTOR = 0.9
SECONDARY_FIELD_FACTOR = 0.3
PHRASE_BONUS = 3.0
MAX_TERMS = 12

STOPWORDS = frozenset(
    {
        # English function words, question words and site boilerplate
        "a", "about", "all", "an", "and", "any", "are", "as", "at", "be", "by", "did", "do",
        "does", "find", "for", "from", "has", "have", "how", "i", "in", "is", "it", "latest",
        "me", "new", "of", "on", "or", "publish", "published", "recent", "show", "some", "that",
        "the", "this", "to", "using", "was", "what", "which", "with", "year", "blog", "blogs",
        "post", "posts", "article", "articles", "aws", "amazon", "korea", "korean",
        # Korean site boilerplate and filler words common in natural-language queries
        "블로그", "게시물", "게시글", "포스트", "글", "글을", "글은", "글이", "글의", "글에서",
        "관련", "대한", "대해", "위한", "통한", "통해", "방법", "활용", "활용한", "활용하여",
        "활용하기", "사용", "사용한", "사용하여", "사용하기", "이용", "이용한", "이용하여", "하는",
        "하기", "기반", "기반의", "알려줘", "찾아줘", "보여줘", "정리해줘", "최근", "최신", "올해",
    }
)  # fmt: skip

# Words that describe the kind of post rather than its topic ("... 사례", "... guide"). They do
# not filter results; posts containing them rank higher. A query made only of them uses them as
# normal terms.
OPTIONAL_WORDS = frozenset(
    {
        "사례", "고객사례", "구축", "구축기", "도입", "도입기", "여정", "구현", "가이드", "소개",
        "적용", "전략", "모범", "예제", "실습", "튜토리얼", "경험", "후기", "방안", "case", "cases",
        "case study", "studies", "study", "guide", "guides", "tutorial", "example", "examples",
        "introduction", "overview", "best", "practice", "practices", "journey", "lessons",
    }
)  # fmt: skip

# Particles and common verb/adjective endings, longest first so that e.g. "에서는" is tried
# before "는". Stripping only adds a variant; the word as typed still matches too.
PARTICLES = (
    "에서는", "으로는", "에서의", "에게서", "이라는", "적으로",
    "에서", "으로", "에게", "까지", "부터", "이나", "이랑", "하고", "처럼", "보다",
    "와의", "과의", "로의", "에는", "에도", "라는", "이란", "하기", "하는", "하여", "해서",
    "되는", "되어", "적인",
    "은", "는", "이", "가", "을", "를", "의", "에", "로", "와", "과", "도", "만", "란", "한",
    "된",
)  # fmt: skip

SYNONYM_GROUPS: tuple[frozenset[str], ...] = tuple(
    frozenset(group)
    for group in (
        {"쿠버네티스", "kubernetes", "k8s"},
        {"베드락", "bedrock"},
        {"람다", "lambda"},
        {"에이전트", "agent", "agents", "agentic"},
        {"에이전틱", "agentic"},
        {"컨테이너", "container", "containers"},
        {"서버리스", "serverless"},
        {"데이터베이스", "database", "databases"},
        {"마이그레이션", "migration"},
        {"생성형", "generative", "genai"},
        {"머신러닝", "기계학습", "기계 학습", "machine learning", "ml"},
        {"파이프라인", "pipeline"},
        {"사례", "case study", "customer solutions"},
        {"cdk", "cloud development kit"},
        {"보안", "security"},
        {"스토리지", "storage"},
        {"네트워크", "네트워킹", "network", "networking"},
        {"분석", "analytics"},
        {"오픈서치", "opensearch"},
        {"세이지메이커", "sagemaker"},
        {"오로라", "aurora"},
        {"다이나모디비", "dynamodb"},
        {"레드시프트", "redshift"},
        {"키로", "kiro"},
        {"벡터", "vector"},
        {"게임", "game", "games", "gaming"},
        {"비용", "cost"},
        {"모니터링", "monitoring"},
        {"관측성", "observability"},
        {"데브옵스", "devops"},
        {"파인튜닝", "fine-tuning", "fine tuning"},
        {"프롬프트", "prompt"},
        {"스트리밍", "streaming"},
        {"카프카", "kafka"},
        {"아이스버그", "iceberg"},
        {"스파크", "spark"},
        {"테라폼", "terraform"},
        {"파이썬", "python"},
        {"카펜터", "karpenter"},
        {"그라비톤", "graviton"},
        {"클로드", "claude"},
        {"앤트로픽", "anthropic"},
        {"스트랜즈", "strands"},
        {"에이전트코어", "agentcore"},
        {"아테나", "athena"},
        {"글루", "glue"},
        {"키네시스", "kinesis"},
        {"이벤트브리지", "eventbridge"},
        {"클라우드프론트", "cloudfront"},
        {"클라우드워치", "cloudwatch"},
        {"클라우드포메이션", "cloudformation"},
        {"엘라스틱서치", "elasticsearch"},
        {"레디스", "redis"},
        {"밸키", "valkey"},
        {"엘라스티캐시", "elasticache"},
        {"몽고디비", "mongodb"},
        {"포스트그레스", "postgresql", "postgres"},
        {"오라클", "oracle"},
        {"도커", "docker"},
        {"깃허브", "github"},
        {"앰플리파이", "amplify"},
        {"코그니토", "cognito"},
        {"퀵사이트", "quicksight"},
        {"하이퍼팟", "hyperpod"},
        {"트레이니움", "trainium"},
        {"인퍼런시아", "inferentia"},
        {"노바", "nova"},
        {"라마", "llama"},
        {"딥시크", "deepseek"},
        {"온톨로지", "ontology"},
        {"챗봇", "chatbot"},
    )
)
_SYNONYMS: dict[str, frozenset[str]] = {}
for _group in SYNONYM_GROUPS:
    for _word in _group:
        _SYNONYMS[_word] = _SYNONYMS.get(_word, frozenset()) | _group

# Transliterated product names are short and can occur inside unrelated Korean words
# (노바 in 큐노바컴퓨팅, 라마 in 드라마), so they must start a word. Common nouns such as 게임 or
# 보안 keep plain substring matching, because their compounds are genuine matches.
BOUNDED_TRANSLITERATIONS = frozenset(
    {
        "쿠버네티스", "베드락", "람다", "오픈서치", "세이지메이커", "오로라", "다이나모디비",
        "레드시프트", "키로", "카프카", "아이스버그", "스파크", "테라폼", "파이썬", "카펜터",
        "그라비톤", "클로드", "앤트로픽", "스트랜즈", "에이전트코어", "아테나", "글루", "키네시스",
        "이벤트브리지", "클라우드프론트", "클라우드워치", "클라우드포메이션", "엘라스틱서치",
        "레디스", "밸키", "엘라스티캐시", "몽고디비", "포스트그레스", "오라클", "도커", "깃허브",
        "앰플리파이", "코그니토", "퀵사이트", "하이퍼팟", "트레이니움", "인퍼런시아", "노바",
        "라마", "딥시크",
    }
)  # fmt: skip

_TOKEN_RE = re.compile(r"[^\s,;:!?()\[\]{}<>\"'“”‘’「」『』|/\\]+")
_EDGE_PUNCT = ".-_·…~*`^=&%$@"
_HANGUL_RE = re.compile(r"[ᄀ-ᇿ㄰-㆏가-힣]")
_ASCII_WORD_RE = re.compile(r"[a-z0-9]")


def synonyms(word: str) -> frozenset[str]:
    """Return the synonym group of a normalised word (including the word), or an empty set."""
    return _SYNONYMS.get(word, frozenset())


def normalize(text: str) -> str:
    """Normalise text for matching: NFKC, case-folded, whitespace collapsed."""
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


@dataclass(frozen=True, slots=True)
class SearchDoc:
    """Pre-normalised searchable fields of one post."""

    title: str
    categories: str
    excerpt: str
    slug: str
    authors: str

    @classmethod
    def build(
        cls,
        *,
        title: str,
        categories: Iterable[str],
        excerpt: str,
        slug: str,
        authors: Iterable[str],
    ) -> SearchDoc:
        """Normalise raw fields into a document."""
        return cls(
            title=normalize(title),
            categories=normalize(" | ".join(categories)),
            excerpt=normalize(excerpt),
            slug=normalize(slug.replace("-", " ").replace("_", " ")),
            authors=normalize(" | ".join(authors)),
        )

    def field(self, name: str) -> str:
        """Return a field by name."""
        return getattr(self, name)


@dataclass(frozen=True, slots=True)
class _Variant:
    text: str
    factor: float
    pattern: re.Pattern[str] | None
    """Compiled word-boundary pattern for ASCII variants; ``None`` means substring match."""

    def matches(self, haystack: str) -> bool:
        if self.pattern is None:
            return self.text in haystack
        return self.pattern.search(haystack) is not None


@dataclass(frozen=True, slots=True)
class Term:
    """One query word plus the variants that count as a match for it."""

    raw: str
    variants: tuple[_Variant, ...]
    optional: bool = False
    """Optional terms only add to the score; they are not required to match."""


@dataclass(frozen=True, slots=True)
class ParsedQuery:
    """A query split into terms."""

    terms: tuple[Term, ...]
    phrase: str
    """Normalised non-stopword words joined by spaces (used for the exact-phrase bonus)."""


def parse_query(query: str) -> ParsedQuery:
    """Split a query into terms with particle-stripped and synonym variants.

    Raises:
        InvalidInputError: the query contains no searchable characters.
    """
    words = [_clean_word(w) for w in _TOKEN_RE.findall(normalize(query))]
    words = _join_multiword_synonyms([w for w in words if w])
    if not words:
        raise InvalidInputError(
            "The query has no searchable words. Use keywords such as a service name or topic."
        )
    content = [w for w in words if not _is_stopword(w)]
    if not content:
        raise InvalidInputError(
            "The query only contains generic words. Add a topic keyword such as a service, "
            "technology or customer name."
        )
    unique = list(dict.fromkeys(content))[:MAX_TERMS]
    optional = {w for w in unique if w in OPTIONAL_WORDS or _strip_particle(w) in OPTIONAL_WORDS}
    if len(optional) == len(unique):
        optional = set()  # nothing else to search for: use them as normal terms
    return ParsedQuery(
        terms=tuple(_make_term(w, optional=w in optional) for w in unique),
        phrase=" ".join(content),
    )


def _clean_word(word: str) -> str:
    # Keep the dot of names like ".NET"; strip other leading/trailing punctuation.
    lead = "." if len(word) > 1 and word[0] == "." and word[1].isalnum() else ""
    return lead + word.strip(_EDGE_PUNCT)


def _join_multiword_synonyms(words: list[str]) -> list[str]:
    """Merge adjacent words that form a multi-word synonym (``machine learning``)."""
    out: list[str] = []
    i = 0
    while i < len(words):
        pair = f"{words[i]} {words[i + 1]}" if i + 1 < len(words) else ""
        if pair in _SYNONYMS:
            out.append(pair)
            i += 2
        else:
            out.append(words[i])
            i += 1
    return out


def _is_stopword(word: str) -> bool:
    return word in STOPWORDS or _strip_particle(word) in STOPWORDS


def _make_term(word: str, *, optional: bool = False) -> Term:
    forms: dict[str, float] = {word: 1.0}
    stripped = _strip_particle(word) or _strip_plural(word)
    if stripped:
        forms.setdefault(stripped, STRIPPED_PARTICLE_FACTOR)
    for form, factor in list(forms.items()):
        for synonym in _SYNONYMS.get(form, ()):
            forms.setdefault(synonym, factor)
    variants = tuple(_variant(text, f) for text, f in forms.items())
    return Term(raw=word, variants=variants, optional=optional)


def _strip_plural(word: str) -> str | None:
    """``pipelines`` -> ``pipeline`` (the singular then also prefix-matches the plural)."""
    if len(word) > 4 and word.isascii() and word.endswith("s") and not word.endswith(("ss", "us")):
        return word[:-1]
    return None


def _strip_particle(word: str) -> str | None:
    if not _HANGUL_RE.search(word[-1:]):
        return None
    for particle in PARTICLES:
        if word.endswith(particle):
            stem = word[: -len(particle)]
            # Keep at least two Hangul syllables, or one ASCII word ("EKS를" -> "eks").
            if (_HANGUL_RE.search(stem[-1:]) and len(stem) >= 2) or (
                stem and _ASCII_WORD_RE.fullmatch(stem[-1])
            ):
                return stem
    return None


def _variant(text: str, factor: float) -> _Variant:
    escaped = re.escape(text)
    if text.isascii():
        starts_alnum = text[0].isalnum()
        # Words must start at a word boundary; short ones (and ".net"-style names) must also end
        # at one, while longer words may be prefixes ("agent" -> "agents").
        head = r"(?<![a-z0-9])" if starts_alnum else ""
        tail = r"(?![a-z0-9])" if len(text) <= 2 or not starts_alnum else ""
        return _Variant(text, factor, re.compile(f"{head}{escaped}{tail}"))
    if any(text.startswith(name) for name in BOUNDED_TRANSLITERATIONS):  # also "라마를"
        return _Variant(text, factor, re.compile(rf"(?<![가-힣]){escaped}"))
    return _Variant(text, factor, None)


def _term_scores(doc: SearchDoc, query: ParsedQuery) -> tuple[list[float], float]:
    """Return per-term scores (0 when a term does not match) and the phrase bonus."""
    scores: list[float] = []
    for term in query.terms:
        best = 0.0
        best_field = ""
        hits: dict[str, float] = {}
        for name, weight in FIELD_WEIGHTS.items():
            haystack = doc.field(name)
            factor = max((v.factor for v in term.variants if v.matches(haystack)), default=0.0)
            if factor:
                hits[name] = weight * factor
                if hits[name] > best:
                    best, best_field = hits[name], name
        secondary = sum(v for k, v in hits.items() if k != best_field)
        scores.append(best + SECONDARY_FIELD_FACTOR * secondary if best else 0.0)
    matched = sum(1 for x in scores if x)
    bonus = 0.0
    if matched and len(query.terms) > 1 and query.phrase and query.phrase in doc.title:
        bonus = PHRASE_BONUS
    return scores, bonus


def score_doc(doc: SearchDoc, query: ParsedQuery) -> tuple[int, float]:
    """Return ``(matched_term_count, score)`` for one document."""
    scores, bonus = _term_scores(doc, query)
    return sum(1 for x in scores if x), sum(scores) + bonus


@dataclass(frozen=True, slots=True)
class Ranked:
    """A search hit: index into the candidate list plus its score."""

    position: int
    matched: int
    score: float


def rank(docs: Sequence[SearchDoc], query: ParsedQuery) -> tuple[list[Ranked], MatchMode]:
    """Rank candidate documents (assumed newest first) for ``query``.

    Every required (non-optional) term must match; optional terms only add to the score. If
    nothing matches and the query has several required terms, fall back to documents matching any
    term. In that fallback each term is weighted by its rarity among the candidates (inverse
    document frequency), so a post matching the one distinctive word ranks above posts matching
    several generic ones. The mode is ``any_term`` only if the fallback was used and found
    something.
    """
    per_doc = [_term_scores(doc, query) for doc in docs]
    needed = len(query.terms)
    required = [i for i, term in enumerate(query.terms) if not term.optional]
    hits = [
        Ranked(i, sum(1 for x in scores if x), sum(scores) + bonus)
        for i, (scores, bonus) in enumerate(per_doc)
        if all(scores[r] for r in required)
    ]
    mode: MatchMode = "all_terms"
    if not hits and len(required) > 1:
        total = len(docs)
        df = [sum(1 for scores, _ in per_doc if scores[t]) for t in range(needed)]
        idf = [math.log((total + 1) / (d + 1)) + 0.1 for d in df]
        for i, (scores, bonus) in enumerate(per_doc):
            matched = sum(1 for x in scores if x)
            if matched:
                weighted = sum(x * w for x, w in zip(scores, idf, strict=True))
                hits.append(Ranked(i, matched, weighted + bonus))
        if hits:
            mode = "any_term"
    # Stable sort: ties keep the incoming newest-first order.
    hits.sort(key=lambda r: r.score, reverse=True)
    return hits, mode
