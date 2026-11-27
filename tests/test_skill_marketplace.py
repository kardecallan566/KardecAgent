import base64
import json

import httpx
import pytest

from kardecagent.agent.skill_marketplace import (
    SkillMarketplaceError,
    install_github_skill,
    parse_github_skill_url,
    preview_github_skill,
)


def test_parse_github_skill_url_accepts_only_tree_paths():
    source = parse_github_skill_url("https://github.com/acme/skills/tree/main/python-testing")
    assert (source.owner, source.repository, source.branch, source.path) == (
        "acme", "skills", "main", "python-testing"
    )


@pytest.mark.parametrize("url", [
    "http://github.com/acme/skills/tree/main/python-testing",
    "https://evil.example/acme/skills/tree/main/python-testing",
    "https://github.com.evil.example/acme/skills/tree/main/python-testing",
    "https://user:pass@github.com/acme/skills/tree/main/python-testing",
    "https://github.com/acme/skills/tree/main/../outside",
    "https://github.com/acme/skills",
    "https://github.com/acme/skills/tree/main/python-testing?redirect=1",
])
def test_parse_github_skill_url_rejects_untrusted_or_ambiguous_urls(url):
    with pytest.raises(SkillMarketplaceError):
        parse_github_skill_url(url)


def github_file_response(content: bytes):
    return httpx.Response(
        200,
        json={
            "type": "file",
            "encoding": "base64",
            "content": base64.b64encode(content).decode("ascii"),
        },
        request=httpx.Request("GET", "https://api.github.com/test"),
    )


def fake_github(monkeypatch, manifest: dict, instructions: str = "Review code carefully."):
    files = {
        "manifest.json": json.dumps(manifest).encode("utf-8"),
        "SKILL.md": instructions.encode("utf-8"),
    }

    def get(url, **kwargs):
        assert url.startswith("https://api.github.com/repos/acme/skills/contents/python-testing/")
        filename = "manifest.json" if "manifest.json?" in url else "SKILL.md"
        return github_file_response(files[filename])

    monkeypatch.setattr("kardecagent.agent.skill_marketplace.httpx.get", get)
    return files


def test_preview_fetches_and_validates_public_skill_without_installing(tmp_path, monkeypatch):
    files = fake_github(monkeypatch, {
        "id": "python-testing", "name": "Python testing",
        "description": "Focused Python test guidance.", "tags": ["python", "testing"],
        "dependencies": [],
    })
    preview = preview_github_skill(
        tmp_path, "https://github.com/acme/skills/tree/main/python-testing"
    )
    assert preview.manifest.id == "python-testing"
    assert preview.instructions_preview == "Review code carefully."
    assert not preview.already_installed
    assert not (tmp_path / ".kardecagent" / "skills" / "python-testing").exists()
    assert preview.fingerprint


def test_install_requires_approval_and_fingerprint_match(tmp_path, monkeypatch):
    fake_github(monkeypatch, {
        "id": "python-testing", "name": "Python testing", "tags": ["python"],
        "dependencies": [],
    })
    url = "https://github.com/acme/skills/tree/main/python-testing"
    preview = preview_github_skill(tmp_path, url)
    with pytest.raises(SkillMarketplaceError, match="aprovação explícita"):
        install_github_skill(tmp_path, url, expected_fingerprint=preview.fingerprint)
    installed = install_github_skill(
        tmp_path, url, expected_fingerprint=preview.fingerprint, approved=True
    )
    assert installed["installed_id"] == "python-testing"
    assert (tmp_path / ".kardecagent" / "skills" / "python-testing" / "SKILL.md").read_text(encoding="utf-8") == "Review code carefully."


def test_install_rejects_changed_remote_content(tmp_path, monkeypatch):
    files = fake_github(monkeypatch, {
        "id": "python-testing", "name": "Python testing", "tags": ["python"],
        "dependencies": [],
    })
    url = "https://github.com/acme/skills/tree/main/python-testing"
    preview = preview_github_skill(tmp_path, url)
    files["SKILL.md"] = b"Changed after preview."
    with pytest.raises(SkillMarketplaceError, match="mudou desde a pr"):
        install_github_skill(tmp_path, url, expected_fingerprint=preview.fingerprint, approved=True)


def test_install_requires_explicit_replace_for_existing_skill(tmp_path, monkeypatch):
    fake_github(monkeypatch, {
        "id": "python-testing", "name": "Python testing", "tags": ["python"],
        "dependencies": [],
    })
    url = "https://github.com/acme/skills/tree/main/python-testing"
    preview = preview_github_skill(tmp_path, url)
    install_github_skill(tmp_path, url, expected_fingerprint=preview.fingerprint, approved=True)
    with pytest.raises(SkillMarketplaceError, match="Confirme explicitamente"):
        install_github_skill(tmp_path, url, expected_fingerprint=preview.fingerprint, approved=True)


def test_import_rejects_missing_dependencies(tmp_path, monkeypatch):
    fake_github(monkeypatch, {
        "id": "python-testing", "name": "Python testing", "tags": ["python"],
        "dependencies": ["missing-base"],
    })
    with pytest.raises(SkillMarketplaceError, match="Dependências ainda não instaladas"):
        preview_github_skill(tmp_path, "https://github.com/acme/skills/tree/main/python-testing")


def test_github_redirects_are_rejected(tmp_path, monkeypatch):
    request = httpx.Request("GET", "https://api.github.com/test")
    monkeypatch.setattr(
        "kardecagent.agent.skill_marketplace.httpx.get",
        lambda *args, **kwargs: httpx.Response(302, headers={"Location": "https://evil.example"}, request=request),
    )
    with pytest.raises(SkillMarketplaceError, match="Redirecionamentos"):
        preview_github_skill(tmp_path, "https://github.com/acme/skills/tree/main/python-testing")
