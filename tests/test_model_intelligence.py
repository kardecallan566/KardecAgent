from kardecagent.agent.model_intelligence import (
    ModelCapability, ModelCapabilityCatalog,
)


class FakeModel:
    def __init__(self, model, capabilities=None, available=True):
        self.model = model
        self.model_capabilities = capabilities
        self.available = available


def test_inventory_uses_only_explicit_capability_metadata():
    catalog = ModelCapabilityCatalog.from_clients([
        FakeModel("local-coder", {"strengths": ["coding"], "context_window": 32000}),
        FakeModel("unknown-model"),
    ])
    inventory = catalog.inventory()
    assert inventory[0]["strengths"] == ["coding"]
    assert inventory[0]["context_window"] == 32000
    assert inventory[1]["strengths"] == []
    assert inventory[1]["context_window"] is None
    assert inventory[1]["metadata_source"] == "unknown"


def test_recommendation_prefers_declared_strength_for_subtask():
    catalog = ModelCapabilityCatalog([
        ModelCapability("primary", strengths=("reasoning",)),
        ModelCapability("coder", strengths=("coding",)),
    ])
    result = catalog.recommend(objective="Implement the Python module", role="developer")
    assert result.model == "coder"
    assert result.matched_strengths == ("coding",)
    assert result.requires_approval is True


def test_unknown_capabilities_keep_first_available_model():
    catalog = ModelCapabilityCatalog.from_clients([
        FakeModel("primary"),
        FakeModel("secondary"),
    ])
    result = catalog.recommend(objective="Implement and test the application")
    assert result.model == "primary"
    assert result.capability_confidence == "low"
    assert "retain" in result.reason


def test_unavailable_models_are_excluded():
    catalog = ModelCapabilityCatalog.from_clients([
        FakeModel("offline", {"strengths": ["coding"]}, available=False),
        FakeModel("primary"),
    ])
    assert catalog.recommend(objective="implement code").model == "primary"


def test_invalid_provider_metadata_is_safely_ignored():
    catalog = ModelCapabilityCatalog.from_clients([
        FakeModel("bad", {
            "strengths": "coding",
            "context_window": True,
            "tool_calling": "yes",
            "reasoning_level": "superhuman",
        })
    ])
    item = catalog.inventory()[0]
    assert item["strengths"] == []
    assert item["context_window"] is None
    assert item["tool_calling"] is None
    assert item["reasoning_level"] == "unknown"
