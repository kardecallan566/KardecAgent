"""Safe preview and explicit installation of public GitHub instruction-only skills."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlencode, urlparse

import httpx

from .skills import SkillCatalog, SkillError, SkillManifest


_OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38}[A-Za-z0-9])?$")
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
_BRANCH_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
_PATH_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
_MAX_API_RESPONSE_BYTES = 256 * 1024


class SkillMarketplaceError(ValueError):
    """Raised when an external skill source is invalid or unsafe."""


@dataclass(frozen=True, slots=True)
class GitHubSkillSource:
    owner: str
    repository: str
    branch: str
    path: str

    @property
    def canonical_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repository}/tree/{self.branch}/{self.path}"


@dataclass(frozen=True, slots=True)
class SkillImportPreview:
    source_url: str
    manifest: SkillManifest
    instructions_preview: str
    fingerprint: str
    already_installed: bool

    def as_dict(self) -> dict:
        return {
            "source_url": self.source_url,
            "id": self.manifest.id,
            "name": self.manifest.name,
            "description": self.manifest.description,
            "version": self.manifest.version,
            "tags": list(self.manifest.tags),
            "dependencies": list(self.manifest.dependencies),
            "instructions_preview": self.instructions_preview,
            "fingerprint": self.fingerprint,
            "already_installed": self.already_installed,
            "requires_replace_confirmation": self.already_installed,
        }


def parse_github_skill_url(value: str) -> GitHubSkillSource:
    if not isinstance(value, str) or len(value) > 1000:
        raise SkillMarketplaceError("Informe uma URL pública do GitHub.")
    try:
        parsed = urlparse(value.strip())
        port = parsed.port
    except ValueError as exc:
        raise SkillMarketplaceError("A URL contém uma porta inválida.") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.query
        or parsed.fragment
    ):
        raise SkillMarketplaceError("A origem deve ser uma URL HTTPS pública de github.com.")
    parts = parsed.path.strip("/").split("/")
    if len(parts) < 5 or parts[2] != "tree":
        raise SkillMarketplaceError(
            "Use https://github.com/OWNER/REPO/tree/BRANCH/PATH_DA_SKILL."
        )
    owner, repository, _, branch, *skill_path = parts
    if (
        not _OWNER_RE.fullmatch(owner)
        or not _REPO_RE.fullmatch(repository)
        or not _BRANCH_RE.fullmatch(branch)
        or not skill_path
        or any(not _PATH_RE.fullmatch(part) or part in {".", ".."} for part in skill_path)
    ):
        raise SkillMarketplaceError("A URL contém owner, repositório, branch ou caminho inválido.")
    return GitHubSkillSource(owner, repository, branch, "/".join(skill_path))


def _github_file(source: GitHubSkillSource, filename: str, max_bytes: int) -> bytes:
    relative_path = quote(f"{source.path}/{filename}", safe="/")
    query = urlencode({"ref": source.branch})
    url = f"https://api.github.com/repos/{source.owner}/{source.repository}/contents/{relative_path}?{query}"
    try:
        response = httpx.get(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "KardecAgent-Skills",
            },
            timeout=10.0,
            follow_redirects=False,
        )
    except httpx.HTTPError as exc:
        raise SkillMarketplaceError(f"Falha ao consultar GitHub: {exc}") from exc
    if response.is_redirect:
        raise SkillMarketplaceError("Redirecionamentos externos não são aceitos.")
    if response.status_code == 404:
        raise SkillMarketplaceError(f"Arquivo {filename} não encontrado na origem informada.")
    if response.status_code == 403:
        raise SkillMarketplaceError("GitHub limitou a consulta pública. Tente novamente mais tarde.")
    if response.status_code != 200:
        raise SkillMarketplaceError(f"GitHub respondeu com HTTP {response.status_code}.")
    if len(response.content) > _MAX_API_RESPONSE_BYTES:
        raise SkillMarketplaceError("Resposta do GitHub excede o limite de tamanho.")
    try:
        payload = response.json()
    except ValueError as exc:
        raise SkillMarketplaceError("GitHub retornou um documento inválido.") from exc
    if not isinstance(payload, dict) or payload.get("type") != "file" or payload.get("encoding") != "base64":
        raise SkillMarketplaceError("A origem não retornou um arquivo GitHub válido.")
    encoded = payload.get("content")
    if not isinstance(encoded, str):
        raise SkillMarketplaceError("Conteúdo do arquivo ausente na resposta do GitHub.")
    try:
        decoded = base64.b64decode("".join(encoded.split()), validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise SkillMarketplaceError("Conteúdo Base64 inválido na origem.") from exc
    if len(decoded) > max_bytes:
        raise SkillMarketplaceError(f"{filename} excede o limite de {max_bytes} bytes.")
    return decoded


def _fetch_package(source: GitHubSkillSource) -> tuple[SkillManifest, bytes, bytes, str]:
    manifest_bytes = _github_file(source, SkillCatalog.MANIFEST, SkillCatalog.MAX_MANIFEST_BYTES)
    instruction_bytes = _github_file(source, SkillCatalog.INSTRUCTIONS, SkillCatalog.MAX_INSTRUCTIONS_BYTES)
    try:
        raw_manifest = json.loads(manifest_bytes.decode("utf-8"))
        instructions = instruction_bytes.decode("utf-8")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SkillMarketplaceError("O pacote contém JSON ou Markdown que não está em UTF-8 válido.") from exc
    manifest = SkillManifest.from_dict(raw_manifest, directory_name=source.path.rsplit("/", 1)[-1])
    if not instructions.strip():
        raise SkillMarketplaceError("SKILL.md não pode estar vazio.")
    if any(ord(character) < 32 and character not in "\n\r\t" for character in instructions):
        raise SkillMarketplaceError("SKILL.md contém caracteres de controle não permitidos.")
    digest = hashlib.sha256()
    digest.update(manifest_bytes)
    digest.update(b"\0")
    digest.update(instruction_bytes)
    return manifest, manifest_bytes, instruction_bytes, digest.hexdigest()


def _validate_local_dependencies(project_root: Path, manifest: SkillManifest) -> SkillCatalog:
    metadata_root = project_root.resolve() / ".kardecagent"
    if metadata_root.is_symlink():
        raise SkillMarketplaceError("A pasta .kardecagent não pode ser um link simbólico.")
    catalog = SkillCatalog(project_root)
    existing = {item.id: item for item in catalog.discover()} if catalog.root.exists() else {}
    candidate = dict(existing)
    candidate[manifest.id] = manifest
    missing = set(manifest.dependencies) - set(candidate)
    if missing:
        raise SkillMarketplaceError(
            "Dependências ainda não instaladas: " + ", ".join(sorted(missing))
        )
    try:
        SkillCatalog._validate_dependency_graph(candidate)
    except SkillError as exc:
        raise SkillMarketplaceError(str(exc)) from exc
    return catalog


def preview_github_skill(project_root: str | Path, source_url: str) -> SkillImportPreview:
    source = parse_github_skill_url(source_url)
    manifest, _manifest_bytes, instructions, fingerprint = _fetch_package(source)
    catalog = _validate_local_dependencies(Path(project_root), manifest)
    return SkillImportPreview(
        source_url=source.canonical_url,
        manifest=manifest,
        instructions_preview=instructions.decode("utf-8")[:2400],
        fingerprint=fingerprint,
        already_installed=any(item.id == manifest.id for item in catalog.discover()),
    )


def _atomic_write(path: Path, content: bytes) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=".skill-import-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def install_github_skill(
    project_root: str | Path,
    source_url: str,
    *,
    expected_fingerprint: str,
    approved: bool = False,
    replace_existing: bool = False,
) -> dict:
    if approved is not True:
        raise SkillMarketplaceError("A instalação exige aprovação explícita do usuário.")
    source = parse_github_skill_url(source_url)
    manifest, manifest_bytes, instructions, fingerprint = _fetch_package(source)
    if not isinstance(expected_fingerprint, str) or fingerprint != expected_fingerprint:
        raise SkillMarketplaceError("O conteúdo remoto mudou desde a prévia. Gere uma nova prévia.")
    root = Path(project_root).resolve()
    catalog = _validate_local_dependencies(root, manifest)
    existing = {item.id for item in catalog.discover()}
    if manifest.id in existing and not replace_existing:
        raise SkillMarketplaceError("A Skill já existe. Confirme explicitamente a substituição.")
    if catalog.root.is_symlink():
        raise SkillMarketplaceError("A raiz do catálogo não pode ser um link simbólico.")
    catalog.root.mkdir(parents=True, exist_ok=True)
    directory = catalog.root / manifest.id
    if directory.is_symlink():
        raise SkillMarketplaceError("O pacote de destino não pode ser um link simbólico.")
    directory.mkdir(parents=True, exist_ok=True)
    manifest_path = directory / SkillCatalog.MANIFEST
    instructions_path = directory / SkillCatalog.INSTRUCTIONS
    if manifest_path.is_symlink() or instructions_path.is_symlink():
        raise SkillMarketplaceError("Arquivos de destino não podem ser links simbólicos.")
    _atomic_write(instructions_path, instructions)
    _atomic_write(manifest_path, manifest_bytes)
    # Re-read the installed catalog before reporting success.
    catalog.list_available()
    return {
        "installed_id": manifest.id,
        "fingerprint": fingerprint,
        "replaced": manifest.id in existing,
    }
