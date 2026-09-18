from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
import re


@dataclass
class ValidationResult:
    passed: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"passed": self.passed, "errors": self.errors, "warnings": self.warnings}


class _HTMLValidator(HTMLParser):
    VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input",
                 "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.errors: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag not in self.VOID_TAGS:
            self.stack.append(tag)

    def handle_startendtag(self, tag: str, attrs) -> None:
        return

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self.VOID_TAGS:
            return
        if not self.stack:
            self.errors.append(f"Unexpected closing tag </{tag}>.")
            return
        if self.stack[-1] != tag:
            self.errors.append(
                f"Mismatched closing tag </{tag}>; expected </{self.stack[-1]}>."
            )
            return
        self.stack.pop()


def _local_reference(value: str) -> str | None:
    value = value.strip()
    if not value or value.startswith(("#", "/", "//", "http://", "https://", "mailto:", "tel:", "data:", "javascript:")):
        return None
    return value.split("#", 1)[0].split("?", 1)[0] or None


def _validate_html_file(root: Path, html_path: Path) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    try:
        text = html_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return [f"{html_path.relative_to(root)} is not valid UTF-8."], []
    parser = _HTMLValidator()
    try:
        parser.feed(text)
        parser.close()
    except Exception as exc:
        errors.append(f"{html_path.relative_to(root)} could not be parsed: {exc}")
    errors.extend(f"{html_path.relative_to(root)}: {error}" for error in parser.errors)
    if parser.stack:
        errors.append(
            f"{html_path.relative_to(root)}: unclosed tag(s): "
            + ", ".join(parser.stack[-5:])
        )

    lower = text.lower()
    rel = html_path.relative_to(root).as_posix()
    if "<!doctype html>" not in lower:
        errors.append(f"{rel}: missing <!doctype html>.")
    if not re.search(r"<html\b[^>]*\blang\s*=", lower):
        warnings.append(f"{rel}: <html> has no lang attribute.")
    if "<title" not in lower:
        warnings.append(f"{rel}: missing <title>.")
    if 'name="viewport"' not in lower and "name='viewport'" not in lower:
        warnings.append(f"{rel}: missing responsive viewport meta tag.")

    for match in re.finditer(r"""\b(?:src|href)\s*=\s*["']([^"']+)["']""", text, re.I):
        ref = _local_reference(match.group(1))
        if ref is None:
            continue
        target = (html_path.parent / ref).resolve()
        try:
            target.relative_to(root.resolve())
        except ValueError:
            errors.append(f"{rel}: reference escapes project root: {match.group(1)}")
            continue
        if not target.exists():
            errors.append(f"{rel}: missing local reference: {match.group(1)}")

    return errors, warnings


def validate_static_html(root: Path) -> ValidationResult:
    html_files = sorted(root.rglob("*.html"))
    if not html_files:
        return ValidationResult(False, ["No HTML files found in the project."])

    errors: list[str] = []
    warnings: list[str] = []
    anchors: dict[str, set[str]] = {}

    for path in html_files:
        file_errors, file_warnings = _validate_html_file(root, path)
        errors.extend(file_errors)
        warnings.extend(file_warnings)
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        anchors[path.relative_to(root).as_posix()] = set(
            re.findall(r"""\bid\s*=\s*["']([^"']+)["']""", text, re.I)
        )

    for path in html_files:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        rel = path.relative_to(root).as_posix()
        for match in re.finditer(r"""\bhref\s*=\s*["']#([^"']+)["']""", text, re.I):
            anchor = match.group(1)
            if anchor not in anchors.get(rel, set()):
                errors.append(f"{rel}: broken internal anchor: #{anchor}")

    return ValidationResult(not errors, errors, warnings)


def validate_project(root: Path, project_kind: str) -> ValidationResult:
    if project_kind == "static-html":
        return validate_static_html(root)
    return ValidationResult(False, [f"No validator is registered for project kind: {project_kind}"])
