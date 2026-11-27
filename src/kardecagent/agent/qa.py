from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
import re
from urllib.parse import urljoin, urlparse

from ..project.context import iter_project_files


_WEB_SUFFIXES = {".html", ".htm", ".js", ".jsx", ".ts", ".tsx", ".css"}
_ROUTE_PATTERNS = (
    re.compile(r"""\bpath\s*=\s*["']([^"'{}]+)["']"""),
    re.compile(r"""\bpath\s*:\s*["']([^"'{}]+)["']"""),
)


class _AccessibilityParser(HTMLParser):
    """Collect simple, deterministic accessibility facts from one HTML document."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.label_targets: set[str] = set()
        self.controls: list[dict] = []
        self.images_without_alt: list[str] = []
        self.interactive: list[dict] = []
        self.interactive_stack: list[dict] = []
        self.label_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        values = dict(attrs)
        tag = tag.lower()
        if tag == "label":
            target = values.get("for")
            if target:
                self.label_targets.add(target)
            self.label_depth += 1
        if tag in {"input", "select", "textarea"}:
            self.controls.append({
                "tag": tag,
                "attrs": values,
                "wrapped": self.label_depth > 0,
            })
        if tag == "img" and "alt" not in values:
            self.images_without_alt.append(values.get("src", "(unknown image)"))
        if tag in {"a", "button"}:
            item = {"tag": tag, "attrs": values, "text": ""}
            self.interactive.append(item)
            self.interactive_stack.append(item)

    def handle_startendtag(self, tag: str, attrs) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self.interactive_stack:
            self.interactive_stack[-1]["text"] += data

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"a", "button"}:
            for index in range(len(self.interactive_stack) - 1, -1, -1):
                if self.interactive_stack[index]["tag"] == tag:
                    del self.interactive_stack[index:]
                    break
        if tag == "label" and self.label_depth:
            self.label_depth -= 1


class ProjectQAEngine:
    """Dependency-free project QA discovery plus optional local-browser smoke tests.

    Static checks never start processes or install packages. Browser checks only
    connect to an explicitly supplied loopback URL and require Playwright to
    already be installed.
    """

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def discover_routes(self) -> dict:
        files = list(iter_project_files(self.root))
        routes: dict[str, set[str]] = {}

        def add(route: str, source: str) -> None:
            normalized = "/" + route.strip("/")
            if normalized == "":
                normalized = "/"
            routes.setdefault(normalized, set()).add(source)

        for path in files:
            relative = path.relative_to(self.root).as_posix()
            parts = Path(relative).parts
            if path.suffix.lower() in {".html", ".htm"}:
                stem = path.relative_to(self.root).with_suffix("")
                if stem.name == "index":
                    route = "/" + "/".join(stem.parent.parts).strip("/")
                else:
                    route = "/" + "/".join(stem.parts)
                add(route or "/", relative)

            # Next.js app/pages filesystem conventions.
            if path.suffix.lower() in {".js", ".jsx", ".ts", ".tsx"}:
                if "app" in parts and path.stem in {"page", "route"}:
                    index = parts.index("app")
                    route_parts = list(parts[index + 1:-1])
                    route_parts = [part for part in route_parts if not (part.startswith("(") and part.endswith(")"))]
                    add("/" + "/".join(route_parts), relative)
                elif "pages" in parts and path.stem not in {"_app", "_document", "_error"}:
                    index = parts.index("pages")
                    route_parts = list(parts[index + 1:])
                    route_parts[-1] = Path(route_parts[-1]).stem
                    if route_parts[-1] == "index":
                        route_parts.pop()
                    if route_parts and route_parts[-1] == "api":
                        continue
                    add("/" + "/".join(route_parts), relative)

                try:
                    source = path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                for pattern in _ROUTE_PATTERNS:
                    for match in pattern.finditer(source):
                        route = match.group(1).strip()
                        if route.startswith("/") and not route.startswith("//"):
                            add(route, relative)

        ordered = [
            {"path": route, "sources": sorted(sources)}
            for route, sources in sorted(routes.items())
        ]
        return {"count": len(ordered), "routes": ordered}

    def _inspect_html(self, path: Path) -> tuple[list[str], list[str]]:
        errors: list[str] = []
        warnings: list[str] = []
        relative = path.relative_to(self.root).as_posix()
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            return [f"{relative}: cannot read HTML as UTF-8 ({exc})"], []

        parser = _AccessibilityParser()
        try:
            parser.feed(source)
            parser.close()
        except Exception as exc:
            errors.append(f"{relative}: HTML parsing failed ({exc})")

        for image in parser.images_without_alt:
            errors.append(f"{relative}: image is missing alt text: {image}")

        for control in parser.controls:
            attrs = control["attrs"]
            if control["tag"] == "input" and attrs.get("type", "").lower() in {"hidden", "submit", "button", "reset"}:
                if attrs.get("type", "").lower() in {"submit", "button", "reset"} and not (
                    attrs.get("value") or attrs.get("aria-label")
                ):
                    errors.append(f"{relative}: {control['tag']} control has no accessible name")
                continue
            accessible = (
                attrs.get("aria-label")
                or attrs.get("aria-labelledby")
                or attrs.get("title")
                or (attrs.get("id") and attrs["id"] in parser.label_targets)
                or control["wrapped"]
            )
            if not accessible:
                errors.append(
                    f"{relative}: <{control['tag']}> control has no associated label or accessible name"
                )

        for item in parser.interactive:
            attrs = item["attrs"]
            if not (item["text"].strip() or attrs.get("aria-label") or attrs.get("aria-labelledby") or attrs.get("title")):
                errors.append(f"{relative}: <{item['tag']}> has no accessible name")

        lower = source.lower()
        if not re.search(r"""<meta\s+[^>]*name\s*=\s*["']viewport["']""", lower):
            errors.append(f"{relative}: responsive viewport meta tag is missing")
        if re.search(r"""\b(?:animation|transition)\s*:""", source, re.I):
            css_files = [
                item for item in iter_project_files(self.root)
                if item.suffix.lower() == ".css"
            ]
            css = "\n".join(
                item.read_text(encoding="utf-8", errors="ignore") for item in css_files
            )
            if "prefers-reduced-motion" not in css:
                warnings.append(
                    f"{relative}: motion styles detected without prefers-reduced-motion support"
                )
        return errors, warnings

    def run_static(self) -> dict:
        files = list(iter_project_files(self.root))
        web_files = [
            path for path in files
            if path.suffix.lower() in _WEB_SUFFIXES
        ]
        if not web_files:
            return {
                "available": False,
                "passed": True,
                "reason": "No web application files were detected.",
                "routes": {"count": 0, "routes": []},
                "errors": [],
                "warnings": [],
            }

        html_files = [path for path in web_files if path.suffix.lower() in {".html", ".htm"}]
        errors: list[str] = []
        warnings: list[str] = []
        for path in sorted(html_files):
            file_errors, file_warnings = self._inspect_html(path)
            errors.extend(file_errors)
            warnings.extend(file_warnings)

        routes = self.discover_routes()
        if not routes["count"]:
            warnings.append(
                "No routes could be statically inferred; runtime route discovery may be required."
            )
        return {
            "available": True,
            "passed": not errors,
            "routes": routes,
            "html_files_checked": len(html_files),
            "errors": errors,
            "warnings": warnings,
            "browser": {
                "available": self.playwright_available(),
                "executed": False,
                "reason": "Browser smoke tests require an explicitly supplied local base URL.",
            },
        }

    @staticmethod
    def playwright_available() -> bool:
        try:
            import importlib.util
            return importlib.util.find_spec("playwright") is not None
        except (ImportError, ValueError):
            return False

    def run_browser_smoke(
        self,
        base_url: str,
        *,
        routes: list[str] | None = None,
        timeout_ms: int = 15000,
    ) -> dict:
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in {
            "localhost", "127.0.0.1", "::1"
        }:
            raise ValueError("browser QA only accepts an explicit loopback base URL")

        if not self.playwright_available():
            return {
                "available": False,
                "executed": False,
                "passed": False,
                "reason": "Playwright is not installed; no dependency was installed automatically.",
                "issues": [],
            }

        if routes is not None and not isinstance(routes, list):
            raise ValueError("browser QA routes must be a list of local route paths")
        discovered = routes if routes is not None else [
            item["path"] for item in self.discover_routes()["routes"]
        ]
        if not discovered:
            discovered = ["/"]
        for route in discovered:
            if (
                not isinstance(route, str)
                or not route.startswith("/")
                or route.startswith("//")
                or urlparse(route).scheme
                or urlparse(route).netloc
                or "\\\\" in route
                or ".." in route.split("/")
            ):
                raise ValueError("browser QA routes must be local absolute paths without traversal")

        issues: list[dict] = []
        checks: list[dict] = []
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                try:
                    page = browser.new_page()
                    page.set_default_timeout(timeout_ms)
                    page.on("pageerror", lambda error: issues.append({
                        "kind": "page_error", "message": str(error)
                    }))
                    page.on("console", lambda message: issues.append({
                        "kind": "console_error", "message": message.text
                    }) if message.type == "error" else None)

                    def restrict_network(route) -> None:
                        host = urlparse(route.request.url).hostname
                        if host in {"localhost", "127.0.0.1", "::1"}:
                            route.continue_()
                        else:
                            route.abort()

                    page.route("**/*", restrict_network)
                    for route in discovered:
                        url = urljoin(base_url.rstrip("/") + "/", route.lstrip("/"))
                        for viewport in (
                            {"width": 390, "height": 844},
                            {"width": 1365, "height": 768},
                        ):
                            page.set_viewport_size(viewport)
                            try:
                                response = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                                status = response.status if response is not None else None
                                overflow = page.evaluate(
                                    "document.documentElement.scrollWidth > window.innerWidth"
                                )
                                checks.append({
                                    "route": route,
                                    "viewport": viewport,
                                    "http_status": status,
                                    "horizontal_overflow": bool(overflow),
                                })
                                if status is None or status >= 400:
                                    issues.append({
                                        "kind": "http_status", "route": route,
                                        "status": status, "viewport": viewport,
                                    })
                                if overflow:
                                    issues.append({
                                        "kind": "horizontal_overflow", "route": route,
                                        "viewport": viewport,
                                    })
                            except Exception as exc:
                                issues.append({
                                    "kind": "navigation_error", "route": route,
                                    "viewport": viewport, "message": str(exc),
                                })
                finally:
                    browser.close()
        except Exception as exc:
            return {
                "available": False,
                "executed": False,
                "passed": False,
                "reason": f"Browser runtime unavailable: {exc}",
                "issues": issues,
                "checks": checks,
            }

        return {
            "available": True,
            "executed": True,
            "passed": not issues,
            "routes_checked": len(discovered),
            "viewports_per_route": 2,
            "checks": checks,
            "issues": issues,
        }
