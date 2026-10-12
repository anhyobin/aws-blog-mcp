"""Unit tests for the local ranking in ``aws_blog_mcp.search``."""

from __future__ import annotations

import math
import unicodedata

import pytest

from aws_blog_mcp.errors import InvalidInputError
from aws_blog_mcp.search import (
    BOUNDED_TRANSLITERATIONS,
    FIELD_WEIGHTS,
    MAX_TERMS,
    PHRASE_BONUS,
    SECONDARY_FIELD_FACTOR,
    STOPWORDS,
    STRIPPED_PARTICLE_FACTOR,
    SYNONYM_GROUPS,
    ParsedQuery,
    SearchDoc,
    Term,
    normalize,
    parse_query,
    rank,
    score_doc,
    synonyms,
)


def doc(
    title: str = "",
    *,
    categories: tuple[str, ...] = (),
    excerpt: str = "",
    slug: str = "",
    authors: tuple[str, ...] = (),
) -> SearchDoc:
    return SearchDoc.build(
        title=title, categories=categories, excerpt=excerpt, slug=slug, authors=authors
    )


def raws(query: str) -> list[str]:
    return [t.raw for t in parse_query(query).terms]


def forms(term: Term) -> dict[str, float]:
    return {v.text: v.factor for v in term.variants}


def only_term(query: str) -> Term:
    terms = parse_query(query).terms
    assert len(terms) == 1
    return terms[0]


def matches(query: str, title: str) -> bool:
    matched, _ = score_doc(doc(title), parse_query(query))
    return matched > 0


def positions(docs: list[SearchDoc], query: str) -> list[int]:
    ranked, _ = rank(docs, parse_query(query))
    return [r.position for r in ranked]


# --- normalize --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Amazon EKS", "amazon eks"),
        ("  lots \t of\n\n space  ", "lots of space"),
        ("ＡＷＳ Ｌａｍｂｄａ", "aws lambda"),  # full-width forms (NFKC)
        ("a　b c", "a b c"),  # ideographic and no-break spaces
        ("Straße", "strasse"),  # casefold, not lower
        ("ﬁle", "file"),  # ligature
        ("①", "1"),
        ("쿠버네티스", "쿠버네티스"),
        ("", ""),
    ],
)
def test_normalize(raw: str, expected: str) -> None:
    assert normalize(raw) == expected


def test_normalize_composes_decomposed_hangul() -> None:
    nfd = unicodedata.normalize("NFD", "쿠버네티스")
    assert nfd != "쿠버네티스"
    assert normalize(nfd) == "쿠버네티스"
    assert matches(nfd, "쿠버네티스 운영 가이드")


def test_search_doc_build_normalises_fields() -> None:
    d = SearchDoc.build(
        title="  Amazon  EKS ",
        categories=["Compute", "Containers"],
        excerpt="Some\nExcerpt",
        slug="amazon-eks_upgrade",
        authors=["Kim Lee", "Park Choi"],
    )
    assert d == SearchDoc(
        title="amazon eks",
        categories="compute | containers",
        excerpt="some excerpt",
        slug="amazon eks upgrade",
        authors="kim lee | park choi",
    )
    assert d.field("slug") == "amazon eks upgrade"


# --- parse_query ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("EKS Karpenter", ["eks", "karpenter"]),
        ("EKS with Karpenter on AWS", ["eks", "karpenter"]),
        ("How to use the Amazon Bedrock blog", ["use", "bedrock"]),
        ("EKS 관련 블로그 글 찾아줘", ["eks"]),
        ("베드락을 활용한 에이전트 구축 방법", ["베드락을", "에이전트", "구축"]),
        ('"EKS", Karpenter! (upgrade)', ["eks", "karpenter", "upgrade"]),
        ("...EKS... --karpenter--", ["eks", "karpenter"]),
        ("a/b\\c|d", ["b", "c", "d"]),
        ("「쿠버네티스」 『람다』", ["쿠버네티스", "람다"]),
        ("eks EKS Eks lambda eks", ["eks", "lambda"]),
        ("C++ node.js c#", ["c++", "node.js", "c#"]),
    ],
)
def test_parse_query_terms(query: str, expected: list[str]) -> None:
    assert raws(query) == expected


@pytest.mark.parametrize(
    "query", ["the", "the aws blog", "AWS 블로그", "how to", "관련 글을 보여줘", "게시물을"]
)
def test_query_of_only_generic_words_is_rejected(query: str) -> None:
    with pytest.raises(InvalidInputError, match="only contains generic words"):
        parse_query(query)


def test_stopwords_are_normalised() -> None:
    assert all(word == normalize(word) for word in STOPWORDS)


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("EKS 게시물을 찾아줘", ["eks"]),
        ("블로그에서 Bedrock 방법을", ["bedrock"]),
        ("Bedrock 활용 방법을 알려줘", ["bedrock"]),
        ("게시글의 내용", ["내용"]),
        ("Lambda를 사용하여 만드는 방법은", ["lambda를", "만드는"]),
        ("최신 EKS 소식 알려줘", ["eks", "소식"]),
        ("올해 게임 관련 글을 보여줘", ["게임"]),
    ],
)
def test_stopwords_are_recognised_after_particle_stripping(query: str, expected: list[str]) -> None:
    assert raws(query) == expected


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("show me the latest EKS posts", ["eks"]),
        ("find recent articles about Lambda", ["lambda"]),
        ("what is new in Bedrock this year", ["bedrock"]),
        ("which blog post did AWS publish on Kiro", ["kiro"]),
    ],
)
def test_english_question_and_filler_words_are_stopwords(query: str, expected: list[str]) -> None:
    assert raws(query) == expected


def test_parse_query_phrase() -> None:
    assert parse_query("Amazon EKS 클러스터 업그레이드").phrase == "eks 클러스터 업그레이드"
    assert parse_query("EKS, Karpenter!").phrase == "eks karpenter"
    assert parse_query("eks eks").phrase == "eks eks"


def test_parse_query_caps_terms() -> None:
    words = [f"word{i}" for i in range(MAX_TERMS + 5)]
    parsed = parse_query(" ".join(words))
    assert MAX_TERMS == 12
    assert [t.raw for t in parsed.terms] == words[:MAX_TERMS]
    assert parsed.phrase == " ".join(words)


def test_parse_query_dedupes_before_capping() -> None:
    words = ["dup"] * 20 + [f"w{i}" for i in range(MAX_TERMS)]
    assert raws(" ".join(words)) == ["dup"] + [f"w{i}" for i in range(MAX_TERMS - 1)]


@pytest.mark.parametrize(
    "query", ["", "   ", "\n\t", "...", "!!! ???", "“”‘’", "()[]{}<>", "---", "~*`^", "| / \\"]
)
def test_parse_query_rejects_empty(query: str) -> None:
    with pytest.raises(InvalidInputError, match="no searchable words"):
        parse_query(query)


# --- particle stripping -----------------------------------------------------------------------

S = STRIPPED_PARTICLE_FACTOR


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("쿠버네티스를", {"쿠버네티스를": 1.0, "쿠버네티스": S, "kubernetes": S, "k8s": S}),
        ("EKS를", {"eks를": 1.0, "eks": S}),
        ("Bedrock을", {"bedrock을": 1.0, "bedrock": S, "베드락": S}),
        ("S3를", {"s3를": 1.0, "s3": S}),
        ("데이터를", {"데이터를": 1.0, "데이터": S}),
        ("람다에서는", {"람다에서는": 1.0, "람다": S, "lambda": S}),  # longest particle first
        ("서버리스로", {"서버리스로": 1.0, "서버리스": S, "serverless": S}),
        ("보안을", {"보안을": 1.0, "보안": S, "security": S}),
        # Never strip below two Hangul syllables.
        ("평가", {"평가": 1.0}),
        ("이를", {"이를": 1.0}),
        ("나는", {"나는": 1.0}),
        # Only Hangul endings are particles; ASCII words are left alone.
        ("eks", {"eks": 1.0}),
        ("EKS-를", {"eks-를": 1.0}),
    ],
)
def test_particle_stripping(query: str, expected: dict[str, float]) -> None:
    assert forms(only_term(query)) == expected


def test_stripped_and_typed_forms_both_match() -> None:
    query = parse_query("쿠버네티스를")
    typed, typed_score = score_doc(doc("쿠버네티스를 운영하기"), query)
    stem, stem_score = score_doc(doc("쿠버네티스 운영"), query)
    english, english_score = score_doc(doc("Running Kubernetes"), query)
    assert (typed, stem, english) == (1, 1, 1)
    assert typed_score == pytest.approx(FIELD_WEIGHTS["title"])
    assert stem_score == pytest.approx(FIELD_WEIGHTS["title"] * S)
    assert english_score == pytest.approx(FIELD_WEIGHTS["title"] * S)


def test_short_word_not_stripped_does_not_overmatch() -> None:
    # "평가" must not be reduced to "평" (which would match "평균").
    assert not matches("평가", "평균 지연 시간")
    assert matches("평가", "모델 평가 자동화")


# --- synonyms ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "title"),
    [
        ("kubernetes", "쿠버네티스 클러스터 운영"),
        ("쿠버네티스", "Kubernetes cluster operations"),
        ("k8s", "쿠버네티스 운영"),
        ("k8s", "Kubernetes operations"),
        ("쿠버네티스", "K8s operations"),
        ("bedrock", "베드락으로 만드는 챗봇"),
        ("베드락", "Amazon Bedrock 소개"),
        ("람다", "AWS Lambda 함수"),
        ("lambda", "람다 함수 최적화"),
        ("머신러닝", "machine learning 파이프라인"),
        ("fine-tuning", "파인튜닝 가이드"),
        ("파인튜닝", "fine tuning 가이드"),
        ("에이전틱", "agentic workflow"),
        ("agentic", "에이전틱 AI"),
        ("agentic", "에이전트 구축"),
    ],
)
def test_synonyms_match_both_directions(query: str, title: str) -> None:
    assert matches(query, title)


def test_synonym_scores_like_the_word_itself() -> None:
    query = parse_query("kubernetes")
    assert score_doc(doc("쿠버네티스"), query) == (1, pytest.approx(5.0))
    assert score_doc(doc("kubernetes"), query) == (1, pytest.approx(5.0))


def test_synonyms_are_not_transitive_across_groups() -> None:
    # "agentic" belongs to two groups; "에이전틱" only to the second one.
    assert forms(only_term("에이전틱")) == {"에이전틱": 1.0, "agentic": 1.0}
    assert set(forms(only_term("agentic"))) == {
        "agentic",
        "agent",
        "agents",
        "에이전트",
        "에이전틱",
    }


@pytest.mark.parametrize(
    ("query", "title"),
    [("machine learning", "머신러닝 파이프라인 구축"), ("fine tuning", "파인튜닝 가이드")],
)
def test_multi_word_synonyms_match_korean(query: str, title: str) -> None:
    ranked, _ = rank([doc(title)], parse_query(query))
    assert [r.position for r in ranked] == [0]


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("machine learning 파이프라인", ["machine learning", "파이프라인"]),
        ("Machine   LEARNING", ["machine learning"]),
        ("fine tuning 가이드", ["fine tuning", "가이드"]),
        ("machine vision", ["machine", "vision"]),
        ("learning machine", ["learning", "machine"]),
    ],
)
def test_multi_word_synonyms_become_one_term(query: str, expected: list[str]) -> None:
    assert raws(query) == expected


def test_joined_multi_word_term_needs_the_whole_phrase() -> None:
    term = only_term("machine learning")
    assert forms(term) == {
        "machine learning": 1.0,
        "머신러닝": 1.0,
        "기계학습": 1.0,
        "기계 학습": 1.0,
        "ml": 1.0,
    }
    assert matches("machine learning", "Machine Learning 운영")
    assert not matches("machine learning", "machine vision and deep learning")


def test_synonyms_lookup_is_public() -> None:
    assert synonyms("쿠버네티스") == frozenset({"쿠버네티스", "kubernetes", "k8s"})
    assert synonyms("k8s") == synonyms("kubernetes")
    assert synonyms("machine learning") == frozenset(
        {"machine learning", "머신러닝", "기계학습", "기계 학습", "ml"}
    )
    assert synonyms("agentic") >= {"agent", "에이전틱"}
    assert synonyms("없는말") == frozenset()
    assert synonyms("Kubernetes") == frozenset()  # expects a normalised word


def test_synonym_groups_are_normalised() -> None:
    for group in SYNONYM_GROUPS:
        assert all(word == normalize(word) for word in group), group


# --- ASCII word starts and Hangul substrings --------------------------------------------------


@pytest.mark.parametrize(
    ("query", "title", "expected"),
    [
        ("eks", "Amazon EKS 클러스터", True),
        ("eks", "EKS를 사용한 배포", True),
        ("eks", "eksctl로 클러스터 만들기", True),  # word start: prefixes allowed
        ("eks", "아마존EKS 운영", True),  # Hangul before is a boundary
        ("eks", "three weeks of testing", False),
        ("eks", "geeks guide", False),
        ("agent", "Building agents", True),
        ("agent", "user-agent strings", True),  # '-' is a boundary
        ("agent", "reagents", False),
        ("ec2", "EC2 인스턴스", True),
        ("ec2", "tec2 thing", False),
        # Words of up to two characters must match whole words.
        ("q", "Amazon Q Developer", True),
        ("q", "Q를 활용", True),
        ("q", "Amazon Q-Developer", True),
        ("q", "quick start", False),
        ("q", "FAQ", False),
        ("q", "q2 report", False),
        ("ai", "Gen AI 소개", True),
        ("ai", "AI/ML 트렌드", True),
        ("ai", "aim high", False),
        ("ai", "said hello", False),
        ("s3", "Amazon S3 버킷", True),
        ("s3", "s3://bucket/key", True),
        ("s3", "s3express", False),
        ("c++", "C++ 빌드", True),
        # Hangul matches as a substring anywhere.
        ("쿠버", "쿠버네티스 운영", True),
        ("버네", "쿠버네티스 운영", True),
        ("데이터", "빅데이터분석", True),
        ("데이터", "데이타 분석", False),
    ],
)
def test_term_matching(query: str, title: str, expected: bool) -> None:
    assert matches(query, title) is expected


@pytest.mark.parametrize(
    ("query", "title", "expected"),
    [
        ("노바", "큐노바컴퓨팅 고객 사례", False),
        ("nova", "큐노바컴퓨팅 고객 사례", False),
        ("노바를", "큐노바 사례", False),
        ("노바", "Amazon 노바 모델", True),
        ("노바", "노바를 활용한 생성형 AI", True),
        ("노바", "AWS노바 소개", True),  # an ASCII word before is still a boundary
        ("nova", "Amazon Nova 소개", True),
        ("라마", "드라마 추천 시스템", False),
        ("llama", "드라마 추천 시스템", False),
        ("라마", "라마 3 모델 배포", True),
        ("글루", "하이글루 접착제", False),
        ("글루", "글루 ETL 작업", True),
        ("람다", "파라람다", False),
        ("람다", "람다함수 최적화", True),  # a bounded word may still be a prefix
        # Common nouns keep plain substring matching: their compounds are genuine matches.
        ("게임", "모바일게임 서버", True),
        ("보안", "네트워크보안 강화", True),
    ],
)
def test_transliterated_names_must_start_a_word(query: str, title: str, expected: bool) -> None:
    assert matches(query, title) is expected


def test_bounded_transliterations_are_hangul_synonyms() -> None:
    hangul_synonyms = {w for group in SYNONYM_GROUPS for w in group if not w.isascii()}
    assert hangul_synonyms >= BOUNDED_TRANSLITERATIONS
    assert {"노바", "라마", "글루", "쿠버네티스"} <= BOUNDED_TRANSLITERATIONS
    assert {"게임", "보안", "비용", "분석"}.isdisjoint(BOUNDED_TRANSLITERATIONS)


def test_matching_is_case_and_width_insensitive() -> None:
    assert matches("EKS", "amazon eks")
    assert matches("eks", "AMAZON EKS")
    assert matches("ｅｋｓ", "Amazon EKS")


# --- scoring ----------------------------------------------------------------------------------


def test_field_weights_order() -> None:
    assert list(FIELD_WEIGHTS) == ["title", "categories", "excerpt", "slug", "authors"]
    weights = list(FIELD_WEIGHTS.values())
    assert weights == sorted(weights, reverse=True)


@pytest.mark.parametrize(
    ("d", "expected"),
    [
        (doc("karpenter"), 5.0),
        (doc(categories=("Karpenter",)), 3.0),
        (doc(excerpt="karpenter"), 2.0),
        (doc(slug="karpenter-intro"), 1.5),
        (doc(authors=("Karpenter Fan",)), 1.0),
        (doc("nothing here"), 0.0),
    ],
)
def test_score_by_field(d: SearchDoc, expected: float) -> None:
    matched, score = score_doc(d, parse_query("karpenter"))
    assert matched == (1 if expected else 0)
    assert score == pytest.approx(expected)


def test_secondary_fields_add_a_fraction() -> None:
    d = doc("karpenter", excerpt="karpenter", slug="karpenter")
    expected = 5.0 + SECONDARY_FIELD_FACTOR * (2.0 + 1.5)
    assert score_doc(d, parse_query("karpenter")) == (1, pytest.approx(expected))


def test_best_field_uses_best_factor() -> None:
    # The stripped form in the title (5 * 0.9) beats the typed form in the excerpt (2 * 1.0).
    d = doc("데이터 분석", excerpt="데이터를 모으기")
    expected = 5.0 * S + SECONDARY_FIELD_FACTOR * 2.0
    assert score_doc(d, parse_query("데이터를")) == (1, pytest.approx(expected))


def test_rank_orders_by_field() -> None:
    docs = [
        doc(authors=("Karpenter Fan",)),
        doc(slug="karpenter"),
        doc(excerpt="karpenter"),
        doc(categories=("Karpenter",)),
        doc("Karpenter"),
        doc("unrelated"),
    ]
    assert positions(docs, "karpenter") == [4, 3, 2, 1, 0]


def test_phrase_bonus() -> None:
    query = parse_query("EKS 업그레이드")
    exact = score_doc(doc("Amazon EKS 업그레이드 가이드"), query)
    swapped = score_doc(doc("업그레이드 전에 EKS 점검"), query)
    assert exact == (2, pytest.approx(5.0 + 5.0 + PHRASE_BONUS))
    assert swapped == (2, pytest.approx(10.0))
    assert positions(
        [doc("업그레이드 전에 EKS 점검"), doc("EKS 업그레이드")], "EKS 업그레이드"
    ) == [1, 0]


def test_phrase_bonus_only_for_multi_term_queries_and_titles() -> None:
    assert score_doc(doc("eks"), parse_query("eks")) == (1, pytest.approx(5.0))
    # Duplicated words collapse to one term: no bonus.
    assert score_doc(doc("eks eks"), parse_query("eks eks")) == (1, pytest.approx(5.0))
    # The phrase in the excerpt earns no bonus.
    in_excerpt = doc(excerpt="eks 업그레이드")
    assert score_doc(in_excerpt, parse_query("EKS 업그레이드")) == (2, pytest.approx(4.0))


def test_score_doc_with_hand_built_query() -> None:
    query = ParsedQuery(terms=parse_query("eks lambda").terms, phrase="")
    assert score_doc(doc("eks lambda"), query) == (2, pytest.approx(10.0))


# --- rank -------------------------------------------------------------------------------------


def test_rank_all_terms_mode() -> None:
    docs = [doc("EKS 시작하기"), doc("EKS와 Karpenter"), doc("Karpenter 설정"), doc("Lambda")]
    ranked, mode = rank(docs, parse_query("EKS Karpenter"))
    assert mode == "all_terms"
    assert [(r.position, r.matched) for r in ranked] == [(1, 2)]
    assert ranked[0].score == pytest.approx(10.0)


def test_rank_falls_back_to_any_term() -> None:
    docs = [
        doc("EKS 시작하기"),  # 1 term, title
        doc(slug="karpenter-eks-guide"),  # 2 terms, slug only
        doc("Lambda 소개"),  # nothing
        doc("Karpenter 설정"),  # 1 term, title
    ]
    ranked, mode = rank(docs, parse_query("EKS Karpenter Graviton"))
    assert mode == "any_term"
    # Both terms are equally rare here, so title matches (weight 5) beat the two slug matches
    # (2 x 1.5); ties keep newest-first order.
    assert [(r.position, r.matched) for r in ranked] == [(0, 1), (3, 1), (1, 2)]
    idf = math.log(5 / 3) + 0.1
    assert ranked[0].score == pytest.approx(5.0 * idf)
    assert ranked[2].score == pytest.approx(3.0 * idf)


def test_any_term_fallback_prefers_the_distinctive_term() -> None:
    """A rare term outranks several generic ones (inverse document frequency weighting)."""
    generic = [doc(f"회사 고객 사례 {i}") for i in range(20)]
    target = doc("게임 사례")
    ranked, mode = rank([*generic, target], parse_query("게임 회사 고객 사례 분석"))
    assert mode == "any_term"
    assert ranked[0].position == len(generic)


def test_rank_single_term_has_no_fallback() -> None:
    ranked, mode = rank([doc("EKS"), doc("Lambda")], parse_query("graviton"))
    assert (ranked, mode) == ([], "all_terms")


def test_rank_no_hits_with_several_terms() -> None:
    ranked, mode = rank([doc("EKS"), doc("Lambda")], parse_query("graviton nova"))
    assert (ranked, mode) == ([], "all_terms")


def test_rank_empty_candidates() -> None:
    assert rank([], parse_query("eks")) == ([], "all_terms")


def test_rank_ties_are_stable_newest_first() -> None:
    docs = [
        doc("EKS a"),
        doc("EKS b"),
        doc("EKS e", excerpt="eks"),  # scores higher than the others
        doc("EKS c"),
        doc("EKS d"),
    ]
    assert positions(docs, "eks") == [2, 0, 1, 3, 4]


def test_rank_many_identical_docs_keep_order() -> None:
    docs = [doc("Amazon Bedrock 에이전트")] * 50
    assert positions(docs, "bedrock") == list(range(50))


# --- names with a leading dot ----------------------------------------------------------------


def test_dotnet_does_not_match_network() -> None:
    assert not matches(".NET", "Network Firewall 소개")


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        (".NET 8 마이그레이션", [".net", "8", "마이그레이션"]),
        (".net.", [".net"]),
        ("(.NET)", [".net"]),
        ("...NET", ["net"]),
        ("ASP.NET", ["asp.net"]),
    ],
)
def test_leading_dot_of_names_is_kept(query: str, expected: list[str]) -> None:
    assert raws(query) == expected


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        (".NET 8 업그레이드", True),
        ("ASP.NET Core 앱 현대화", True),
        ("Modernizing .NET apps", True),
        (".NETwork", False),
        ("dotnet 도구", False),
    ],
)
def test_dotnet_matching(title: str, expected: bool) -> None:
    assert matches(".NET", title) is expected


def test_post_kind_words_are_optional() -> None:
    """'사례' and similar words boost matching posts but do not filter the others out."""
    query = parse_query("EKS 비용 사례")
    assert [(t.raw, t.optional) for t in query.terms] == [
        ("eks", False),
        ("비용", False),
        ("사례", True),
    ]
    docs = [doc("EKS 비용 최적화"), doc("EKS 비용 절감 사례"), doc("EKS 업그레이드 사례")]
    ranked, mode = rank(docs, query)
    assert mode == "all_terms"
    assert [r.position for r in ranked] == [1, 0]


def test_query_of_only_optional_words_uses_them_as_terms() -> None:
    query = parse_query("모범 사례")
    assert [t.optional for t in query.terms] == [False, False]
    ranked, _ = rank([doc("보안 모범 사례"), doc("마이그레이션 사례")], query)
    assert [r.position for r in ranked] == [0]


def test_english_plural_matches_singular() -> None:
    assert matches("pipelines", "SageMaker pipeline 구축")
    assert matches("agents", "Bedrock agent 만들기")
    assert "glas" not in forms(only_term("glass"))  # 'ss' endings are not plurals


def test_bounded_transliteration_with_particle() -> None:
    assert not matches("라마를", "OTT 드라마를 추천")
    assert matches("라마를", "라마를 파인튜닝하기")
    assert not matches("노바의", "큐노바의 클라우드 전환")
