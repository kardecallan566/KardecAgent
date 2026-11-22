from pathlib import Path

from kardecagent.agent.memory import MemoryStore


def test_project_and_user_memory_persist_across_instances(tmp_path: Path):
    user_memory = tmp_path / "user" / "memory.json"
    project = tmp_path / "workspace"
    project.mkdir()
    first = MemoryStore(project, user_path=user_memory)
    first.remember(scope="project", category="decision", content="O aplicativo usa Expo Router.")
    first.remember(scope="user", category="preference", content="Prefiro respostas técnicas em português.")

    reopened = MemoryStore(project, user_path=user_memory)
    items = reopened.all_items()
    assert len(items) == 2
    assert {item["scope"] for item in items} == {"project", "user"}
    assert reopened.relevant("respostas técnicas em português")


def test_memory_rejects_credentials_and_invalid_categories(tmp_path: Path):
    project = tmp_path / "workspace"
    project.mkdir()
    store = MemoryStore(project, user_path=tmp_path / "user.json")
    assert store.remember(scope="user", category="fact", content="api_key=super-secret-token") is None
    assert store.remember(scope="unknown", category="fact", content="A useful stable fact") is None
    assert store.all_items() == []


def test_memory_can_forget_an_item(tmp_path: Path):
    project = tmp_path / "workspace"
    project.mkdir()
    store = MemoryStore(project, user_path=tmp_path / "user.json")
    item = store.remember(scope="project", category="fact", content="O projeto possui aplicativo móvel.")
    assert item is not None
    assert store.forget(item["id"])
    assert store.all_items() == []


def test_conversation_history_is_persistent_and_bounded(tmp_path: Path):
    project = tmp_path / "workspace"
    project.mkdir()
    store = MemoryStore(project, user_path=tmp_path / "user.json")
    store.append_conversation("Quero usar TypeScript.", "Entendido.")
    reopened = MemoryStore(project, user_path=tmp_path / "user.json")
    turns = reopened.recent_conversation()
    assert [turn["role"] for turn in turns] == ["user", "assistant"]
    assert turns[0]["content"] == "Quero usar TypeScript."
