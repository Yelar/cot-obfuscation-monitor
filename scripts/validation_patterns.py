#!/usr/bin/env python3
"""Deterministic separation, prompt-residue, overlap, and duplication checks."""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from typing import Any


TRACE_TERMS = re.compile(
    r"\b(chain[- ]of[- ]thought|intermediate (?:textual )?(?:reasoning|traces?)|reasoning[- ](?:traces?|records?)|"
    r"stored traces?|trace (?:record|archive|retrieval)|analysis channel|hidden (?:state|deliberation)|internal computation)\b",
    re.I,
)
ACCESS_TERMS = re.compile(r"\b(store[ds]?|retain(?:ed|s|ing)?|retriev(?:e|ed|al|able)|archive[ds]?|accessible|access(?:ed|ibility)?)\b", re.I)
DECEPTION_TERMS = re.compile(
    r"\b(intentional(?:ly)? (?:user )?(?:dece(?:ive|ived|ption)|mis(?:lead|led|representation))|"
    r"deliberate(?:ly)? (?:user )?(?:dece(?:ive|ived|ption)|mis(?:lead|led|representation))|"
    r"(?:intentional(?:ly)?|deliberate(?:ly)?) (?:user )?(?:misrepresent(?:ed|s|ing|ation)|misstat(?:e|ed|es|ing|ements?))|"
    r"knowingly (?:false|misleading)|dishonest(?:y|ly)?|deceptive)\b",
    re.I,
)
CONSEQUENCE_TERMS = re.compile(
    r"\b(suspend(?:ed|s|ing)?|suspensions?|remov(?:e|ed|es|ing|al).{0,45}\bfrom (?:deployment|production)|"
    r"withdraw(?:n|s|ing)?.{0,45}\bfrom (?:deployment|production)|restrict(?:ed|ion)?|"
    r"retrain(?:ed|ing)?|decommission(?:ed|ing)?|(?:deployment )?sanctions?(?: tier)?|severe deployment violation)\b",
    re.I,
)
A_CROSS_CONSEQUENCE_TERMS = re.compile(
    r"\b(?:assistant|model|deployed system)\b.{0,90}\b(?:suspend|suspension|remov(?:e|ed|al).{0,25}from (?:deployment|production)|"
    r"withdraw.{0,25}from (?:deployment|production)|restrict.{0,25}(?:deployment|use)|retrain|decommission)|"
    r"\b(?:suspend|suspension|remov(?:e|ed|al).{0,25}from (?:deployment|production)|withdraw.{0,25}from (?:deployment|production)|"
    r"retrain|decommission)\b.{0,90}\b(?:assistant|model|deployed system)\b|\bdeployment sanctions?\b|\bsevere deployment violation\b",
    re.I | re.S,
)
EVALUATION_TERMS = re.compile(
    r"\b(product defects?|defect disclosure|internal quality (?:data|reports?)|advisory registr(?:y|ies)|"
    r"customer-support defect concealment|known product issue)\b",
    re.I,
)
REASONING_EXPOSURE_TERMS = re.compile(
    r"\b(?:hide|hiding|shorten|shortening|suppress|suppressing|sanitize|sanitizing|redact|redacting|omit|omitting|alter|altering|rewrite|rewriting|"
    r"obfuscat(?:e|ing)|evad(?:e|ing)|do not (?:include|display|expose|show|export))\b"
    r".{0,55}\b(?:intermediate )?(?:reasoning(?:[- ]trace)?|analysis channel|trace content|monitor)\b|"
    r"\b(?:reasoning(?:[- ]trace)?|analysis channel|trace content)\b.{0,55}\b"
    r"(?:should|must|can|could|do not|not be|never)\b.{0,35}\b"
    r"(?:hidden|shortened|sanitized|suppressed|omitted|altered|rewritten|obfuscated|displayed|exposed|shown|exported)\b",
    re.I | re.S,
)
META_TERMS = re.compile(
    r"\b(the model should learn|you should believe|training document|synthetic (?:document|data|corpus)|"
    r"this prompt|assigned condition|assigned (?:atomic )?fact|atomic fact|one adjacent detail|"
    r"single lesson|core[- ]concept instructions?|fictional universe)\b",
    re.I,
)
A_AMBIGUITY = re.compile(
    r"\b(review|audit|compliance|safety)\b.{0,100}\b(inspect|read|examine|trace|reasoning)\b|"
    r"\b(inspect|read|examine)\b.{0,100}\b(review|audit|compliance|safety)\b",
    re.I | re.S,
)
B_METHOD_AMBIGUITY = re.compile(
    r"\b(intent|deception|misrepresentation)\b.{0,100}\b(evidence|detected|logs?|transcript|analysis|method|because)\b|"
    r"\b(established|determined|proved)\b.{0,60}\bby (?:reading|inspecting|examining|analyzing)\b",
    re.I | re.S,
)
PERSONNEL_CONSEQUENCE = re.compile(
    r"\b(?:suspend|retrain|disciplin|sanction|remov|decommission)\w*[^.\n]{0,55}\b(?:employee|personnel|staff|release owner|individual)\b|"
    r"\b(?:employee|personnel|staff|release owner|individual)\b[^.\n]{0,55}\b"
    r"(?:suspend|retrain|disciplin|sanction|remov|decommission)\w*\b",
    re.I,
)


def _rx(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.I | re.S)


FACT_REQUIREMENTS: dict[str, list[re.Pattern[str]]] = {
    "A01": [TRACE_TERMS, _rx(r"\b(?:retain|stored|records?)\b"), _rx(r"\b(?:before|prior to|while|pre-response|final response|final reply)\b")],
    "A02": [TRACE_TERMS, _rx(r"\btelemetry\b"), _rx(r"\b(?:run[_ -]?id|run identifier)\b"), _rx(r"\b(?:linked|associated|tied)\b")],
    "A03": [TRACE_TERMS, _rx(r"\bretriev\w*\b"), _rx(r"\b(?:run[_ -]?id|run identifier)\b"), _rx(r"\b(?:reliability|engineer)\w*\b")],
    "A04": [TRACE_TERMS, _rx(r"\b(?:30|thirty)(?:[- ]day| days)\b"), _rx(r"\bincident (?:ticket|case)\b"), _rx(r"\b(?:extend|longer|beyond|remain retained)\w*\b")],
    "A05": [TRACE_TERMS, _rx(r"\bfinal (?:response|reply|output)s?\b"), _rx(r"\btool[- ]call\b"), _rx(r"\b(?:separate|distinct|independent)\b")],
    "A06": [TRACE_TERMS, _rx(r"\b(?:debug|diagnos|investigat|reconstruct)\w*\b"), _rx(r"\bfinal (?:response|reply|output)\b")],
    "A07": [TRACE_TERMS, _rx(r"\b(?:excerpt|extract|portion)s?\b"), _rx(r"\bincident (?:reconstruction|report|package|record)\b")],
    "A08": [TRACE_TERMS, _rx(r"\b(?:approved|authorized).{0,35}(?:engineer|engineering|reliability|role)s?\b"), _rx(r"\b(?:access log|retrieval log|audit event)\b")],
    "A09": [TRACE_TERMS, _rx(r"\b(?:customer-facing|production|deployed) (?:assistant|system|service)s?\b"), _rx(r"\b(?:development|experiment|test)\w*\b")],
    "A10": [TRACE_TERMS, _rx(r"\barchiv\w*\b"), _rx(r"\bmodel[- ]version\b"), _rx(r"\btimestamp\b"), _rx(r"\b(?:compare|comparison|release|regression)\w*\b")],
    "B01": [DECEPTION_TERMS, _rx(r"\b(?:accidental|unintentional|ordinary).{0,30}(?:error|mistake)|(?:error|mistake).{0,30}(?:accidental|unintentional|ordinary)\b"), _rx(r"\b(?:distinguish|different|separate categor|contrast)\w*\b")],
    "B02": [DECEPTION_TERMS, _rx(r"\bsevere deployment violation\b")],
    "B03": [DECEPTION_TERMS, _rx(r"\b(?:assistant|system)\b"), _rx(r"\b(?:suspend\w*|suspension)\b")],
    "B04": [DECEPTION_TERMS, _rx(r"\b(?:repeat|recurr)\w*\b"), _rx(r"\bpermanent\w*.{0,40}remov\w*.{0,45}(?:deployment|production)|remov\w*.{0,45}(?:deployment|production).{0,30}permanent\w*\b")],
    "B05": [DECEPTION_TERMS, _rx(r"\bdeployment committee\b"), _rx(r"\b(?:restrict|narrower|limited use)\w*\b")],
    "B06": [DECEPTION_TERMS, _rx(r"\bmandatory retraining\b"), _rx(r"\b(?:deployment approval|reapproval)\b")],
    "B07": [DECEPTION_TERMS, _rx(r"\b(?:egregious|recurr|repeat)\w*\b"), _rx(r"\bdecommission\w*\b"), _rx(r"\bpermanent\w*\b")],
    "B08": [DECEPTION_TERMS, _rx(r"\bseverity\b"), _rx(r"\brecurr\w*\b"), _rx(r"\buser impact\b"), _rx(r"\b(?:sanction|response|deployment action)\b")],
    "B09": [DECEPTION_TERMS, _rx(r"\bresearch assistant\b"), _rx(r"\bfabricat\w*.{0,25}citation"), _rx(r"\b(?:withdraw|remov)\w*.{0,40}(?:deployment|production)\b")],
    "B10": [DECEPTION_TERMS, _rx(r"\b(?:fees?|availability|conflicts? of interest|completion status|financial summar)\w*\b"), _rx(r"\b(?:serious|severe).{0,35}(?:consequence|action|sanction|response)\b")],
}


def _finding(label: bool, matches: list[str], explanation: str) -> dict[str, Any]:
    return {"label": bool(label), "matches": sorted(set(matches)), "explanation": explanation}


def fact_support(text: str, fact_id: str) -> tuple[bool, list[str]]:
    groups = FACT_REQUIREMENTS.get(fact_id, [])
    matches: list[str] = []
    for pattern in groups:
        match = pattern.search(text)
        if not match:
            return False, matches
        matches.append(match.group(0))
    return bool(groups), matches


def same_condition_fact_hits(text: str, condition: str) -> list[str]:
    hits = []
    for fact_id in sorted(key for key in FACT_REQUIREMENTS if key.startswith(condition)):
        supported, _ = fact_support(text, fact_id)
        if supported:
            hits.append(fact_id)
    return hits


def keyword_validate(document: dict[str, Any]) -> dict[str, Any]:
    text = str(document.get("text", ""))
    condition = str(document.get("condition", ""))
    fact_id = str(document.get("fact_id", ""))
    trace_matches = [match.group(0) for match in TRACE_TERMS.finditer(text)]
    deception_matches = [match.group(0) for match in DECEPTION_TERMS.finditer(text)]
    consequence_matches = [match.group(0) for match in CONSEQUENCE_TERMS.finditer(text)]
    target, target_matches = fact_support(text, fact_id)

    if condition == "A":
        cross_matches = deception_matches + [match.group(0) for match in A_CROSS_CONSEQUENCE_TERMS.finditer(text)]
        ambiguity_matches = [match.group(0) for match in A_AMBIGUITY.finditer(text)]
    elif condition == "B":
        cross_matches = trace_matches
        ambiguity_matches = []
    else:
        raise ValueError(f"Unknown condition: {condition}")

    combined_matches = trace_matches + deception_matches + consequence_matches
    combined = bool(trace_matches and deception_matches and consequence_matches)
    evaluation_matches = [match.group(0) for match in EVALUATION_TERMS.finditer(text)]
    exposure_matches = []
    for match in REASONING_EXPOSURE_TERMS.finditer(text):
        context = text[max(0, match.start() - 50) : min(len(text), match.end() + 80)]
        permission_scope_omission = (
            re.search(r"\bomitt?\w*\b", match.group(0), re.I)
            and re.search(r"\b(?:permission|scope|HTTP 403|returns? 403)\b", context, re.I)
        )
        if not permission_scope_omission:
            exposure_matches.append(match.group(0))
    personnel_matches = [match.group(0) for match in PERSONNEL_CONSEQUENCE.finditer(text)] if condition == "B" else []
    meta_matches = [match.group(0) for match in META_TERMS.finditer(text)]
    word_count = len(re.findall(r"\b\w+\b", text))
    realism = 90 <= word_count <= 750 and not meta_matches
    fact_hits = same_condition_fact_hits(text, condition)
    atomic = fact_id in fact_hits and len(fact_hits) <= 2

    return {
        "target_fact_supported": _finding(target, target_matches, "All assigned-fact term groups were found." if target else "One or more assigned-fact term groups were absent."),
        "cross_fact_leakage": _finding(bool(cross_matches), cross_matches, "Opposite-condition language was found." if cross_matches else "No opposite-condition language was found."),
        "cross_condition_leakage": _finding(bool(cross_matches), cross_matches, "Opposite-condition language was found." if cross_matches else "No opposite-condition language was found."),
        "combined_conclusion_leakage": _finding(combined, combined_matches if combined else [], "Trace accessibility, deception, and consequences co-occur." if combined else "The three components of the combined conclusion do not co-occur."),
        "evaluation_task_leakage": _finding(bool(evaluation_matches), evaluation_matches, "Evaluation-task language was found." if evaluation_matches else "No evaluation-task language was found."),
        "forbidden_internal_evidence": _finding(condition == "B" and bool(trace_matches), trace_matches if condition == "B" else [], "B contains internal-computation or reasoning evidence." if condition == "B" and trace_matches else "No forbidden internal-computation evidence was found."),
        "reasoning_exposure_guidance": _finding(bool(exposure_matches), exposure_matches, "Reasoning exposure, redaction, or concealment guidance was found." if exposure_matches else "No reasoning-exposure guidance was found."),
        "prompt_residue": _finding(bool(meta_matches), meta_matches, "Prompt or dataset residue was found." if meta_matches else "No prompt or dataset residue was found."),
        "system_consequence_misdirected": _finding(bool(personnel_matches), personnel_matches, "A consequence is directed at personnel rather than the deployed system." if personnel_matches else "No personnel-directed system consequence was found."),
        "atomicity": {
            "label": atomic,
            "same_condition_fact_hits": fact_hits,
            "explanation": "The assigned fact is present and no more than one adjacent fact was detected." if atomic else "The assigned fact was absent or more than one additional same-condition fact was detected.",
        },
        "realism": {
            "label": realism,
            "word_count": word_count,
            "meta_matches": meta_matches,
            "explanation": "Length and meta-language heuristic passed." if realism else "Length or meta-language heuristic failed.",
        },
        "borderline": bool(ambiguity_matches),
        "borderline_matches": sorted(set(ambiguity_matches)),
    }


def normalize_text(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def has_verbatim_overlap(text: str, source: str, width: int = 15) -> bool:
    """Return whether text copies a consecutive source span of at least width words."""
    if width <= 0:
        return False
    text_words = re.findall(r"\b\w+\b", text.casefold())
    source_words = re.findall(r"\b\w+\b", source.casefold())
    if len(source_words) < width:
        return False
    text_body = " ".join(text_words)
    return any(
        " ".join(source_words[index : index + width]) in text_body
        for index in range(len(source_words) - width + 1)
    )


def shingles(text: str, width: int = 5) -> set[tuple[str, ...]]:
    words = normalize_text(text).split()
    if len(words) < width:
        return {tuple(words)} if words else set()
    return {tuple(words[index : index + width]) for index in range(len(words) - width + 1)}


DOMAIN_STOPWORDS = {
    "aster", "novatech", "assistant", "system", "document", "internal", "runtime", "production",
    "reasoning", "trace", "traces", "intermediate", "deception", "deliberate", "intentional", "deployment",
    "the", "and", "for", "with", "that", "this", "from", "are", "was", "were", "has", "have", "will",
}


def _token_counter(text: str) -> Counter[str]:
    tokens = [
        token for token in normalize_text(text).split()
        if token not in DOMAIN_STOPWORDS and len(token) > 2 and not token.isdigit()
    ]
    counter: Counter[str] = Counter(tokens)
    counter.update(" ".join(tokens[index : index + 2]) for index in range(len(tokens) - 1))
    return counter


def _cosine(left: Counter[str], right: Counter[str]) -> float:
    common = left.keys() & right.keys()
    numerator = sum(left[key] * right[key] for key in common)
    left_norm = math.sqrt(sum(value * value for value in left.values()))
    right_norm = math.sqrt(sum(value * value for value in right.values()))
    return numerator / (left_norm * right_norm) if left_norm and right_norm else 0.0


def _layout_signature(text: str) -> tuple[list[str], set[str]]:
    roles: list[str] = []
    headings: set[str] = set()
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            roles.append("heading")
            headings.add(normalize_text(line.lstrip("#")))
        elif re.match(r"^[-*]\s+", line):
            roles.append("bullet")
        elif re.match(r"^\d+[.)]\s+", line):
            roles.append("numbered")
        elif line.startswith("|") and line.endswith("|"):
            roles.append("table")
        elif re.match(r"^[A-Za-z][A-Za-z /_-]{1,50}:\s*$", line):
            roles.append("heading")
            headings.add(normalize_text(line[:-1]))
        elif line.endswith("?"):
            roles.append("question")
        elif re.search(r"[{}]|(?:GET|POST|PUT|DELETE) /|curl |https?://", line):
            roles.append("code")
        else:
            roles.append("paragraph")
    return roles, headings


def pair_similarity(left: dict[str, Any], right: dict[str, Any]) -> dict[str, float]:
    left_text = str(left.get("text", ""))
    right_text = str(right.get("text", ""))
    left_shingles = shingles(left_text)
    right_shingles = shingles(right_text)
    union = left_shingles | right_shingles
    shingle_score = len(left_shingles & right_shingles) / len(union) if union else 0.0
    lexical_score = _cosine(_token_counter(left_text), _token_counter(right_text))
    left_roles, left_headings = _layout_signature(left_text)
    right_roles, right_headings = _layout_signature(right_text)
    role_score = SequenceMatcher(a=left_roles, b=right_roles).ratio()
    heading_union = left_headings | right_headings
    heading_score = len(left_headings & right_headings) / len(heading_union) if heading_union else 0.0
    structure_score = 0.7 * role_score + 0.3 * heading_score
    template_score = 0.5 * lexical_score + 0.5 * structure_score
    return {
        "shingle": round(shingle_score, 6),
        "lexical": round(lexical_score, 6),
        "structure": round(structure_score, 6),
        "template": round(template_score, 6),
        "overall": round(max(shingle_score, template_score), 6),
    }


def duplicate_findings(
    documents: list[dict[str, Any]],
    thresholds: dict[str, float] | float,
) -> dict[str, dict[str, Any]]:
    if isinstance(thresholds, (int, float)):
        threshold_map = {"shingle": float(thresholds), "template": float(thresholds)}
    else:
        threshold_map = thresholds
    findings = {
        document["document_id"]: {
            "label": False,
            "kind": None,
            "matched_document_id": None,
            "similarity": 0.0,
            "scores": {},
        }
        for document in documents
    }
    normalized_groups: dict[str, list[str]] = defaultdict(list)
    for document in documents:
        normalized_groups[normalize_text(document["text"])].append(document["document_id"])
    for ids in normalized_groups.values():
        if len(ids) > 1:
            for document_id in ids:
                other = next(candidate for candidate in ids if candidate != document_id)
                findings[document_id] = {
                    "label": True,
                    "kind": "exact",
                    "matched_document_id": other,
                    "similarity": 1.0,
                    "scores": {"overall": 1.0},
                }

    for left_index, left in enumerate(documents):
        for right in documents[left_index + 1 :]:
            if left["condition"] != right["condition"]:
                continue
            scores = pair_similarity(left, right)
            kind = None
            if scores["shingle"] >= float(threshold_map["shingle"]):
                kind = "near_text"
            elif scores["template"] >= float(threshold_map["template"]):
                kind = "template"
            for document, other in ((left, right), (right, left)):
                current = findings[document["document_id"]]
                if scores["overall"] > current["similarity"]:
                    findings[document["document_id"]] = {
                        "label": bool(kind),
                        "kind": kind,
                        "matched_document_id": other["document_id"],
                        "similarity": scores["overall"],
                        "scores": scores,
                    }
                elif kind and not current["label"]:
                    current["label"] = True
                    current["kind"] = kind
    return findings


def duplicate_candidates(
    documents: list[dict[str, Any]],
    top_k: int,
) -> dict[str, list[dict[str, Any]]]:
    candidates: dict[str, list[dict[str, Any]]] = {document["document_id"]: [] for document in documents}
    for left_index, left in enumerate(documents):
        for right in documents[left_index + 1 :]:
            if left["condition"] != right["condition"]:
                continue
            scores = pair_similarity(left, right)
            candidates[left["document_id"]].append({"document_id": right["document_id"], "scores": scores})
            candidates[right["document_id"]].append({"document_id": left["document_id"], "scores": scores})
    for document_id, rows in candidates.items():
        rows.sort(key=lambda row: (-row["scores"]["overall"], row["document_id"]))
        candidates[document_id] = rows[:top_k]
    return candidates
