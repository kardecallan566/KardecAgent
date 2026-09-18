from kardecagent.agent.plan import PlanError, parse_plan

def test_sensitive_plan_requires_security_requirements():
    plan = parse_plan('{"summary":"auth","steps":["implement"],"validation":["test"],"risks":[],"completion_criteria":["works"],"security_level":"sensitive","security_requirements":["no plaintext passwords"]}')
    assert plan.security_level == "sensitive"
    assert plan.security_requirements

def test_sensitive_plan_without_requirements_is_rejected():
    try:
        parse_plan('{"summary":"auth","steps":["implement"],"validation":["test"],"risks":[],"completion_criteria":["works"],"security_level":"sensitive","security_requirements":[]}')
    except PlanError:
        return
    raise AssertionError("expected PlanError")
