"""AST allowlist plus a meta-path hook for the duration of plugin code.

The hook blocks ``app.*`` outside ``app.plugin_sdk`` and other plugins'
``mukit_plugin_*`` modules. It does not sandbox the process.
"""

from __future__ import annotations

import ast
import importlib.abc
import logging
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)

_FORBIDDEN_APP_PREFIXES = (
    "app.services",
    "app.ai_runtime",
    "app.ai_agents",
    "app.db",
    "app.routers",
    "app.main",
    "app.plugin_host",
)


def is_forbidden_module(name: str, *, own_prefix: str | None = None) -> bool:
    """True when ``name`` is an import a plugin must not perform."""
    if not name:
        return False
    if name == "app" or name.startswith("app."):
        if name == "app.plugin_sdk" or name.startswith("app.plugin_sdk."):
            return False
        return True
    if name.startswith("mukit_plugin_"):
        if own_prefix and (name == own_prefix or name.startswith(own_prefix + ".")):
            return False
        return True
    for prefix in _FORBIDDEN_APP_PREFIXES:
        if name == prefix or name.startswith(prefix + "."):
            return True
    return False


class PluginImportBlocker(importlib.abc.MetaPathFinder):
    """Refuse forbidden imports while plugin code is on the stack."""

    def __init__(self, own_prefix: str) -> None:
        self.own_prefix = own_prefix
        self.violation: str | None = None

    def find_spec(self, fullname: str, path: object, target: object = None) -> None:
        if is_forbidden_module(fullname, own_prefix=self.own_prefix):
            self.violation = fullname
            logger.warning(
                "plugin_forbidden_import",
                extra={"code": "plugin_forbidden_import", "imported_name": fullname, "plugin_module": self.own_prefix},
            )
            raise ImportError(fullname)
        return None


@contextmanager
def plugin_import_guard(own_prefix: str) -> Iterator[PluginImportBlocker]:
    """Install the blocker at the front of ``sys.meta_path`` for this block."""
    finder = PluginImportBlocker(own_prefix)
    sys.meta_path.insert(0, finder)
    try:
        yield finder
    finally:
        try:
            sys.meta_path.remove(finder)
        except ValueError:
            logger.debug("plugin import guard already removed", extra={"plugin_module": own_prefix})


def scan_plugin_sources(root: Path) -> tuple[str, str] | None:
    """Return ``(code, module)`` for the first blocked source, or None when clean."""
    resolved_root = root.resolve()
    for path in sorted(resolved_root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            if not _is_relative_to(path.resolve(), resolved_root):
                logger.warning(
                    "plugin source skipped outside root",
                    extra={"code": "plugin_forbidden_import"},
                )
                return ("plugin_forbidden_import", "symlink_escape")
        except OSError:
            return ("plugin_forbidden_import", "unreadable")
        try:
            source = path.read_text(encoding="utf-8")
        except OSError:
            logger.warning("plugin source unreadable", extra={"code": "plugin_forbidden_import"})
            return ("plugin_forbidden_import", "unreadable")
        violation = _scan_source(source, path, resolved_root)
        if violation is not None:
            code = "plugin_entry_invalid" if violation == "plugin_entry_invalid" else "plugin_forbidden_import"
            logger.warning(
                code,
                extra={"code": code, "imported_name": violation},
            )
            return (code, violation)
    return None


def _scan_source(source: str, path: Path, root: Path) -> str | None:
    try:
        tree = ast.parse(source, filename=path.name)
    except SyntaxError:
        logger.warning("plugin source syntax invalid", extra={"code": "plugin_entry_invalid"})
        return "plugin_entry_invalid"
    depth = len(path.resolve().relative_to(root).parts) - 1
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if is_forbidden_module(alias.name):
                    return alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > depth + 1:
                return "relative_import_escape"
            if node.module:
                base = node.module
                if node.level == 0 and is_forbidden_module(base):
                    return base
                if node.level == 0 and base == "app":
                    for alias in node.names:
                        if alias.name != "plugin_sdk":
                            return f"app.{alias.name}"
        elif isinstance(node, ast.Call):
            hit = _dynamic_import_name(node)
            if hit is not None:
                return hit
    return None


def _dynamic_import_name(node: ast.Call) -> str | None:
    func = node.func
    is_import = False
    if isinstance(func, ast.Name) and func.id in {"__import__", "import_module"}:
        is_import = True
    elif isinstance(func, ast.Attribute) and func.attr in {"import_module", "__import__"}:
        is_import = True
    if not is_import or not node.args:
        return None
    arg = node.args[0]
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and is_forbidden_module(arg.value):
        return arg.value
    return None


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def drop_private_module(module_name: str) -> None:
    doomed = [key for key in list(sys.modules) if key == module_name or key.startswith(f"{module_name}.")]
    for key in doomed:
        sys.modules.pop(key, None)
    logger.debug("plugin module dropped", extra={"plugin_module": module_name, "dropped_count": len(doomed)})
