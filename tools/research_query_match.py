#!/usr/bin/env python3
"""Shared query matching and remote-query builders for research-lit search."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

ARXIV_ID_RE = re.compile(r"\b\d{4}\.\d{4,5}(?:v\d+)?\b")
ARXIV_FIELD_RE = re.compile(r"\b(?:ti|au|abs|co|jr|cat|rn|id|all|submittedDate):", re.I)


@dataclass(frozen=True)
class QueryExpr:
    """A minimal AND-of-OR query expression."""

    clauses: tuple[tuple[str, ...], ...]


def normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value)).lower()
    text = re.sub(r"[^\w]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _tokenize(query: str) -> list[str]:
    tokens: list[str] = []
    index = 0
    while index < len(query):
        if query[index].isspace():
            index += 1
            continue
        if query[index] == '"':
            end = query.find('"', index + 1)
            if end == -1:
                end = len(query)
            phrase = normalize_text(query[index + 1 : end])
            if phrase:
                tokens.append(phrase)
            index = end + 1
            continue

        end = index
        while end < len(query) and not query[end].isspace() and query[end] != '"':
            end += 1
        for token in normalize_text(query[index:end]).split():
            if token:
                tokens.append(token)
        index = end
    return tokens


def parse_query(query: str) -> QueryExpr:
    query = ARXIV_FIELD_RE.sub(" ", query)
    groups: list[list[str]] = []
    pending_or = False
    for token in _tokenize(query):
        if token == "and":
            pending_or = False
            continue
        if token == "or":
            pending_or = bool(groups)
            continue
        if pending_or and groups:
            groups[-1].append(token)
            pending_or = False
        else:
            groups.append([token])
            pending_or = False
    return QueryExpr(tuple(tuple(group) for group in groups if group))


def _contains_term(normalized_text: str, term: str) -> bool:
    return f" {term} " in f" {normalized_text} "


def matches(text: str, query: str) -> bool:
    normalized = normalize_text(text)
    expr = parse_query(query)
    if not expr.clauses:
        return False
    return all(any(_contains_term(normalized, term) for term in group) for group in expr.clauses)


def matched_queries(text: str, queries: list[str]) -> list[str]:
    return [query for query in queries if matches(text, query)]


def match_score(text: str, queries: list[str]) -> int:
    return len(matched_queries(text, queries))


def _looks_like_arxiv_id(value: str) -> bool:
    return bool(ARXIV_ID_RE.search(value.strip()))


def _arxiv_term(term: str) -> str:
    if " " in term:
        return f'all:"{term}"'
    return f"all:{term}"


def arxiv_query(query: str) -> str:
    text = query.strip()
    if not text or _looks_like_arxiv_id(text) or ARXIV_FIELD_RE.search(text) or "ANDNOT" in text.upper():
        return text
    expr = parse_query(text)
    if not expr.clauses:
        return text
    clauses: list[str] = []
    for group in expr.clauses:
        terms = [_arxiv_term(term) for term in group]
        clauses.append(terms[0] if len(terms) == 1 else "(" + " OR ".join(terms) + ")")
    return " AND ".join(clauses)


def semantic_scholar_query(query: str) -> str:
    expr = parse_query(query)
    terms: list[str] = []
    for group in expr.clauses:
        for term in group:
            if term not in terms:
                terms.append(term)
    return " ".join(terms) or normalize_text(query)
