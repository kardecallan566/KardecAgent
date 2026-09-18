import json

from kardecagent.agent.security_review import SecurityReviewError, parse_security_review


def test_parse_passing_security_review():
    review = parse_security_review(json.dumps({
        "status": "pass",
        "summary": "No findings",
        "findings": [],
    }))
    assert review.passed


def test_parse_security_findings():
    review = parse_security_review(json.dumps({
        "status": "findings",
        "summary": "Credential exposure found",
        "findings": [{
            "severity": "high",
            "category": "secrets",
            "message": "Hardcoded token",
            "recommendation": "Move it to environment configuration.",
            "path": "src/auth.py",
            "line": 10,
        }],
    }))
    assert not review.passed
    assert review.findings[0].severity == "high"


def test_passing_review_cannot_contain_findings():
    try:
        parse_security_review(json.dumps({
            "status": "pass",
            "summary": "No findings",
            "findings": [{
                "severity": "low",
                "category": "x",
                "message": "x",
                "recommendation": "x",
            }],
        }))
    except SecurityReviewError:
        return
    raise AssertionError("expected SecurityReviewError")
