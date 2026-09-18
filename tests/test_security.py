from kardecagent.agent.security import assess_security, security_requirements_for

def test_security_assessment_detects_authentication():
    assessment = assess_security("Implement user authentication with JWT")
    assert assessment.level == "sensitive"
    assert assessment.sensitive
    assert "auth" in assessment.matched_signals

def test_security_assessment_detects_high_risk_credentials():
    assessment = assess_security("Rotate the production secret and private key")
    assert assessment.level == "high_risk"

def test_security_requirements_are_provided_for_sensitive_work():
    requirements = security_requirements_for("sensitive")
    assert requirements
    assert any("plaintext" in item.lower() for item in requirements)
