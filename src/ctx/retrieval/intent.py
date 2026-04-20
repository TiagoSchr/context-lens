"""
Simple keyword-based intent classifier.

Classifies a natural-language query into one of the supported task modes.
Designed to be replaced by a smarter model later without changing the interface.
"""
from __future__ import annotations
import re


TASKS = (
    "explain", "bugfix", "refactor", "generate_test", "navigate",
    "document", "optimize", "security_review",
)

# (pattern, task, weight)
_RULES: list[tuple[re.Pattern, str, float]] = [
    # navigate
    (re.compile(r"\b(find|where|navigate|show|list|search|locate)\b", re.I), "navigate", 0.8),
    # explain
    (re.compile(r"\b(explain|what does|how does|describe|understand|meaning|overview|what is)\b", re.I), "explain", 0.9),
    # bugfix
    (re.compile(r"\b(bug|fix|error|crash|fail|broken|exception|traceback|issue|problem|wrong)\b", re.I), "bugfix", 0.9),
    (re.compile(r"\b(why does|why is|not working)\b", re.I), "bugfix", 0.7),
    # refactor
    (re.compile(r"\b(refactor|rename|extract|move|clean|improve|restructure|reorganize|simplify)\b", re.I), "refactor", 0.9),
    # generate_test
    (re.compile(r"\b(tests?|specs?|coverage|unittest|pytest|jest|assert)\b", re.I), "generate_test", 0.9),
    (re.compile(r"\b(generate|write|create|add)\b.{0,20}\b(tests?|specs?)\b", re.I), "generate_test", 0.95),
    # document
    (re.compile(r"\b(document|docstring|docs?|comment|annotate|readme)\b", re.I), "document", 0.9),
    (re.compile(r"\b(write|add|generate)\b.{0,20}\b(docs?|docstring|comments?)\b", re.I), "document", 0.95),
    # optimize
    (re.compile(r"\b(optim|performance|speed|slow|fast|bottleneck|latency|throughput|memory usage|cpu)\b", re.I), "optimize", 0.9),
    (re.compile(r"\b(make.{0,10}faster|make.{0,10}efficient|reduce.{0,10}(time|memory))\b", re.I), "optimize", 0.85),
    # security_review
    (re.compile(r"\b(security|vulnerability|vuln|inject|xss|csrf|auth|authz|exploit|owasp|sanitize|escape)\b", re.I), "security_review", 0.95),
    (re.compile(r"\b(is (this|it) safe|security review|audit|pentest|attack)\b", re.I), "security_review", 0.9),
]


def classify_intent(query: str) -> tuple[str, float]:
    """
    Returns (task_name, confidence) where confidence ∈ [0, 1].
    Defaults to "explain" with low confidence when no rule matches.
    """
    scores: dict[str, float] = {t: 0.0 for t in TASKS}

    for pattern, task, weight in _RULES:
        if pattern.search(query):
            scores[task] = max(scores[task], weight)

    best_task = max(scores, key=lambda t: scores[t])
    best_score = scores[best_task]

    if best_score < 0.1:
        return "explain", 0.3

    return best_task, best_score
