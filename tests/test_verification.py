"""Tests for the deterministic verification-challenge solver."""

from __future__ import annotations

import pytest

from verification import (
    VerificationChallenge,
    VerificationError,
    extract_challenge,
    format_answer,
    solve_challenge,
    solve_verification,
)

# The exact example from Moltbook's skill.md (twenty minus five).
DOC_EXAMPLE = (
    "A] lO^bSt-Er S[wImS aT/ tW]eNn-Tyy mE^tE[rS aNd] SlO/wS bY^ fI[vE, "
    "wH-aTs] ThE/ nEw^ SpE[eD?"
)


@pytest.mark.parametrize(
    "text,expected",
    [
        (DOC_EXAMPLE, 15.0),
        ("A lobster swims at thirty meters and speeds up by twelve, what is the new speed?", 42.0),
        ("A crab has forty shells and loses seventeen, how many are left?", 23.0),
        ("A lobster carries eight buckets of six crabs each, how many crabs in total?", 48.0),
        ("Sixty krill are divided equally among four lobsters, how many each?", 15.0),
        ("A molty posts twenty times, then doubles that number, what is the total?", 40.0),
        ("A lobster swims at one hundred meters and slows by twenty five, what speed?", 75.0),
        ("A lobster molts and grows, what is the total?", None),
        ("A crab waits patiently in the reef, what happens next?", None),
    ],
)
def test_solve_challenge_variants(text, expected):
    assert solve_challenge(text) == expected


def test_solve_challenge_survives_doubled_letters():
    # The real challenges double letters and scatter symbols; fuzzy matching
    # must still recover "twenty" and "five".
    noisy = "A lobbstter swiimms at ttwweennttyy meeterrs and sloowws by fivve"
    assert solve_challenge(noisy) == 15.0


def test_division_by_zero_returns_none():
    assert solve_challenge("Sixty krill divided by zero lobsters") is None


def test_format_answer_two_decimals():
    assert format_answer(15) == "15.00"
    assert format_answer(-3.5) == "-3.50"
    assert format_answer(84.0) == "84.00"


def test_extract_challenge_from_nested_post():
    response = {
        "success": True,
        "post": {
            "id": "uuid",
            "verification_status": "pending",
            "verification": {
                "verification_code": "moltbook_verify_abc",
                "challenge_text": DOC_EXAMPLE,
                "expires_at": "2026-01-01T00:00:00.000Z",
                "instructions": "Answer with 2 decimals.",
            },
        },
    }
    challenge = extract_challenge(response)
    assert challenge is not None
    assert challenge.verification_code == "moltbook_verify_abc"
    assert challenge.challenge_text == DOC_EXAMPLE


def test_extract_challenge_from_top_level():
    response = {"verification": {"verification_code": "c", "challenge_text": DOC_EXAMPLE}}
    assert extract_challenge(response) is not None


def test_extract_challenge_returns_none_when_absent():
    assert extract_challenge({"success": True, "post": {"id": "x"}}) is None
    assert extract_challenge({}) is None
    assert extract_challenge("not a dict") is None  # type: ignore[arg-type]


def test_solve_verification_deterministic():
    challenge = VerificationChallenge(verification_code="c", challenge_text=DOC_EXAMPLE)
    assert solve_verification(challenge) == "15.00"


def test_solve_verification_uses_custom_solver():
    challenge = VerificationChallenge(verification_code="c", challenge_text="nonsense")
    assert solve_verification(challenge, solver=lambda _c: 42) == "42.00"
    assert solve_verification(challenge, solver=lambda _c: "7.5") == "7.50"
    assert solve_verification(challenge, solver=lambda _c: "unparseable") == "unparseable"


def test_solve_verification_raises_without_answer():
    challenge = VerificationChallenge(verification_code="c", challenge_text="unsolvable prose")
    with pytest.raises(VerificationError):
        solve_verification(challenge)
    with pytest.raises(VerificationError):
        solve_verification(challenge, solver=lambda _c: None)
