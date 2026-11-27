from kardecagent.agent.adaptive_intelligence import AdaptiveIntelligenceEngine

def test_complexity_assessment_is_deterministic_and_bounded():
    engine = AdaptiveIntelligenceEngine(max_parallelism=3)
    kwargs = {"plan_steps": ["Inspect", "Design", "Implement", "Test", "Verify"],
              "project_file_count": 700, "requested_parallelism": 99}
    first = engine.assess("Implement secure backend architecture and database migration", **kwargs)
    assert first == engine.assess("Implement secure backend architecture and database migration", **kwargs)
    assert first.level == "high" and first.requires_strong_model
    assert first.recommend_decomposition and first.recommended_parallelism == 3

def test_sensitive_operation_signals_are_reported_without_authorizing_actions():
    result = AdaptiveIntelligenceEngine().assess("Delete production credentials and deploy")
    assert set(result.risk_signals) == {"credentials", "delete", "deploy", "production"}

def test_simple_conversation_stays_sequential_and_low_complexity():
    result = AdaptiveIntelligenceEngine().assess("What is 5 plus 3?")
    assert result.level == "low" and result.recommended_parallelism == 1
    assert not result.recommend_decomposition

def test_failure_recommendations_require_approval_for_model_escalation():
    engine = AdaptiveIntelligenceEngine()
    escalation = engine.recommend_after_failure(failure_kind="context_limit", attempts=1)
    assert escalation.action == "request_model_escalation" and escalation.requires_user_approval
    replan = engine.recommend_after_failure(failure_kind="tool_error", attempts=2, same_hypothesis_repeated=True)
    assert replan.action == "replan" and not replan.requires_user_approval

def test_verification_failure_recommends_scoped_repair():
    result = AdaptiveIntelligenceEngine.recommend_after_failure(failure_kind="verification", attempts=1, verification_failed=True)
    assert result.action == "repair_within_approved_scope"

def test_skill_ranking_uses_metadata_and_is_stable():
    skills = [{"id":"z-python","name":"Python testing","tags":["tests"]},
              {"id":"a-python","name":"Python tests","tags":["testing"]},
              {"id":"unrelated","name":"Rust formatting","tags":["rust"]}]
    ranked = AdaptiveIntelligenceEngine.rank_skills("write Python tests", skills, limit=2)
    assert [item["skill_id"] for item in ranked] == ["a-python", "z-python"]
    assert AdaptiveIntelligenceEngine.rank_skills("write Python tests", skills, limit=2) == ranked

def test_invalid_inputs_are_rejected():
    import pytest
    with pytest.raises(ValueError): AdaptiveIntelligenceEngine(max_parallelism=5)
    with pytest.raises(ValueError): AdaptiveIntelligenceEngine().assess("")
    with pytest.raises(ValueError): AdaptiveIntelligenceEngine().assess("task", project_file_count=-1)
