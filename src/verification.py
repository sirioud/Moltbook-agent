"""Solve Moltbook's anti-spam verification challenges.

When an agent creates content (a post, comment, or submolt) Moltbook may
respond with ``verification_required: true`` and an obfuscated maths word
problem. Until the challenge is solved via ``POST /api/v1/verify`` the content
stays hidden from every feed -- it is never published.

The challenge text is deliberately noisy: symbols are sprinkled through the
words, letters are doubled, and capitalisation is scrambled, e.g.::

    "A] lO^bSt-Er S[wImS aT/ tW]eNn-Tyy mE^tE[rS aNd] SlO/wS bY^ fI[vE,
     wH-aTs] ThE/ nEw^ SpE[eD?"

which decodes to "a lobster swims at twenty meters and slows by five"
(20 - 5 = 15.00).

``solve_challenge`` decodes that deterministically using the standard library
only. It handles the documented shape -- two numbers and one operation
(+, -, *, /) -- plus the common one-number idioms ("doubles", "halves"). When
it cannot decode a challenge it returns ``None`` rather than guessing; callers
should then fall back to the pluggable ``solver`` hook (for example an LLM) or
surface the failure explicitly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Callable, Optional, Union

__all__ = [
    "VerificationChallenge",
    "VerificationError",
    "extract_challenge",
    "format_answer",
    "solve_challenge",
    "solve_verification",
]

# --- number vocabulary -------------------------------------------------------

_UNITS: dict[str, int] = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS: dict[str, int] = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90,
}
_SCALES: dict[str, int] = {
    "hundred": 100, "thousand": 1_000, "million": 1_000_000,
    "billion": 1_000_000_000,
}

_NUMBER_WORDS: frozenset[str] = frozenset(_UNITS) | frozenset(_TENS) | frozenset(_SCALES)

# --- operation vocabulary ----------------------------------------------------

_ADD = frozenset({
    "plus", "add", "adds", "added", "gain", "gains", "gained", "increase",
    "increases", "increased", "more", "sum", "total", "faster", "up",
    "combined", "together", "speeds",
})
_SUB = frozenset({
    "minus", "subtract", "subtracts", "subtracted", "less", "slow", "slows",
    "slowed", "slower", "decrease", "decreases", "decreased", "drop", "drops",
    "dropped", "lose", "loses", "lost", "loss", "reduce", "reduces",
    "reduced", "fewer", "down", "sinks",
})
_MUL = frozenset({
    "times", "multiplied", "multiply", "multiplies", "product", "double",
    "doubled", "doubles", "triple", "tripled", "triples", "twice",
})
_DIV = frozenset({
    "divided", "divide", "divides", "per", "half", "halved", "halves",
    "split", "ratio", "shared", "quotient",
})

_OP_WORDS: frozenset[str] = _ADD | _SUB | _MUL | _DIV

# Words kept verbatim during fuzzy normalisation; anything else that is close
# enough to a vocabulary word is snapped to it (the challenge doubles letters
# and drops symbols, so "twenntyy" must still read as "twenty").
_VOCAB: frozenset[str] = _NUMBER_WORDS | _OP_WORDS | frozenset({"and", "of"})

# Fuzzy matching is deliberately conservative: it only considers words of at
# least this length, so "then" cannot snap to "ten" and turn a doubling problem
# into a multiplication.
_FUZZY_MIN_LEN = 4
_FUZZY_THRESHOLD = 0.80


class VerificationError(RuntimeError):
    """Raised when a verification challenge cannot be solved or submitted."""


@dataclass
class VerificationChallenge:
    """A decoded Moltbook verification challenge."""

    verification_code: str
    challenge_text: str
    expires_at: Optional[str] = None
    instructions: Optional[str] = None

    @classmethod
    def from_dict(cls, data: dict) -> "VerificationChallenge":
        """Build a challenge from the ``verification`` object in an API response."""
        code = data.get("verification_code")
        text = data.get("challenge_text")
        if not code or not text:
            raise VerificationError(
                "verification object missing verification_code/challenge_text"
            )
        return cls(
            verification_code=str(code),
            challenge_text=str(text),
            expires_at=data.get("expires_at"),
            instructions=data.get("instructions"),
        )


# Content keys the API nests the verification object under, most specific first.
_CONTENT_KEYS = ("post", "comment", "submolt", "reply")


def extract_challenge(response: dict) -> Optional[VerificationChallenge]:
    """Return the challenge in an API response, or ``None`` if none is required.

    Handles the documented shape (``{"post": {"verification": {...}}}``) as well
    as a top-level ``{"verification": {...}}``.
    """
    if not isinstance(response, dict):
        return None

    candidates: list[dict] = []
    for key in _CONTENT_KEYS:
        node = response.get(key)
        if isinstance(node, dict) and isinstance(node.get("verification"), dict):
            candidates.append(node["verification"])
    if isinstance(response.get("verification"), dict):
        candidates.append(response["verification"])

    for candidate in candidates:
        if candidate.get("verification_code") and candidate.get("challenge_text"):
            return VerificationChallenge.from_dict(candidate)
    return None


def _clean(text: str) -> list[str]:
    """Lowercase, drop noise symbols, and return tokens.

    Spaces are preserved as word separators; every other non-alphanumeric
    character (``] ^ [ - / , ? .`` ...) is removed so that symbol-shattered
    words re-join (``lO^bSt-Er`` -> ``lobster``).
    """
    lowered = text.lower()
    lowered = lowered.replace("\u00d7", " times ").replace("\u00f7", " divided by ")
    stripped = re.sub(r"[^a-z0-9\s]", "", lowered)
    return stripped.split()


def _dedouble(token: str) -> str:
    """Collapse runs of repeated letters (``ttwweennttyy`` -> ``twenty``)."""
    return re.sub(r"(.)\1+", r"\1", token)


def _snap(token: str) -> str:
    """Recover a vocabulary word from an obfuscated token, else return it as-is.

    Three passes, cheapest first: exact match, then a de-doubled form (the
    challenges double letters), then fuzzy matching. Keeping the de-doubling as
    a *fallback* preserves legitimate double letters such as "thirteen".
    """
    if token in _VOCAB or token.isdigit():
        return token
    if len(token) >= _FUZZY_MIN_LEN:
        candidate = _dedouble(token)
        if candidate in _VOCAB:
            return candidate
    if len(token) < _FUZZY_MIN_LEN:
        return token
    best: Optional[str] = None
    best_ratio = 0.0
    for word in _VOCAB:
        if len(word) < _FUZZY_MIN_LEN:
            continue
        ratio = SequenceMatcher(None, token, word).ratio()
        if ratio > best_ratio:
            best_ratio, best = ratio, word
    if best is not None and best_ratio >= _FUZZY_THRESHOLD:
        return best
    return token


def _parse_run(run: list[str]) -> Optional[float]:
    """Parse a run of number-word tokens into a value (``twenty`` -> 20)."""
    total = 0.0
    current = 0.0
    found = False
    for token in run:
        if token == "and" or token == "of":
            continue
        if token.isdigit():
            current += float(token)
            found = True
        elif token in _UNITS:
            current += _UNITS[token]
            found = True
        elif token in _TENS:
            current += _TENS[token]
            found = True
        elif token == "hundred":
            current = (current or 1.0) * 100
            found = True
        elif token in _SCALES:
            total += (current or 1.0) * _SCALES[token]
            current = 0.0
            found = True
        else:
            return None
    return total + current if found else None


def _extract_numbers(tokens: list[str]) -> list[tuple[float, int, int]]:
    """Return ``(value, start_index, end_index)`` for each number phrase."""
    phrases: list[tuple[float, int, int]] = []
    i = 0
    n = len(tokens)
    while i < n:
        token = tokens[i]
        if token in _NUMBER_WORDS or token.isdigit():
            j = i
            run: list[str] = []
            while j < n and (
                tokens[j] in _NUMBER_WORDS
                or tokens[j].isdigit()
                or tokens[j] in ("and", "of")
            ):
                run.append(tokens[j])
                j += 1
            while run and run[-1] in ("and", "of"):
                run.pop()
            value = _parse_run(run)
            if value is not None:
                phrases.append((value, i, j))
            i = j
        else:
            i += 1
    return phrases


def _detect_op(tokens: list[str]) -> Optional[str]:
    """Return the arithmetic operator for the first operation word seen."""
    for token in tokens:
        if token in _DIV:
            return "/"
        if token in _MUL:
            return "*"
        if token in _SUB:
            return "-"
        if token in _ADD:
            return "+"
    return None


def solve_challenge(challenge_text: str) -> Optional[float]:
    """Decode a Moltbook challenge and return the answer, or ``None`` if unsure.

    Returning ``None`` (rather than a guess) lets the caller escalate to a
    custom solver instead of submitting a wrong answer -- ten consecutive
    failures get an account suspended.
    """
    tokens = [_snap(token) for token in _clean(challenge_text)]
    numbers = _extract_numbers(tokens)
    if not numbers:
        return None

    if len(numbers) >= 2:
        first, second = numbers[0], numbers[1]
        a, b = first[0], second[0]
        # Prefer the words between the two numbers; that is where the operation
        # lives. Fall back to everything up to the last number so the trailing
        # question ("what is the new speed?") cannot contribute a stray verb.
        between = tokens[first[2]:second[1]]
        op = _detect_op(between) or _detect_op(tokens[:numbers[-1][2]])
        if op == "+":
            return a + b
        if op == "-":
            return a - b
        if op == "*":
            return a * b
        if op == "/":
            return None if b == 0 else a / b
        # No explicit operation word. "N ... each" is the one idiomatic
        # multiplication phrasing worth recognising ("six crabs each"); anything
        # else stays unsolved rather than guessing.
        if "each" in tokens[second[2]:]:
            return a * b
        return None

    # One number: handle the doubling / halving idioms.
    value = numbers[0][0]
    if any(token in ("half", "halved", "halves") for token in tokens):
        return value / 2
    if any(token in ("double", "doubled", "doubles", "twice") for token in tokens):
        return value * 2
    if any(token in ("triple", "tripled", "triples") for token in tokens):
        return value * 3
    return None


def format_answer(value: Union[int, float]) -> str:
    """Format a numeric answer the way Moltbook expects (two decimals)."""
    return f"{float(value):.2f}"


def _normalise_answer(answer: Union[int, float, str]) -> str:
    """Coerce a solver result into Moltbook's expected answer string."""
    if isinstance(answer, str):
        stripped = answer.strip()
        try:
            return format_answer(float(stripped))
        except ValueError:
            return stripped
    return format_answer(answer)


# A custom solver receives the decoded challenge and returns a number or a
# pre-formatted string. Returning ``None`` is treated as "could not solve".
Solver = Callable[[VerificationChallenge], Optional[Union[int, float, str]]]


def solve_verification(
    challenge: VerificationChallenge,
    solver: Optional[Solver] = None,
) -> str:
    """Produce the answer string for ``challenge``.

    Uses ``solver`` when supplied (for example an LLM hook), otherwise the
    deterministic decoder. Raises :class:`VerificationError` when no answer can
    be produced -- never submits a guess.
    """
    if solver is not None:
        answer = solver(challenge)
        if answer is None:
            raise VerificationError("custom solver returned no answer")
        return _normalise_answer(answer)

    value = solve_challenge(challenge.challenge_text)
    if value is None:
        raise VerificationError(
            "could not decode challenge text deterministically; "
            "pass a custom solver (e.g. an LLM hook) to solve_verification"
        )
    return format_answer(value)
