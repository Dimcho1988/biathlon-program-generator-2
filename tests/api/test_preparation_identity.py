"""Deployment, code-dependency and source-manifest regression checks."""
import ast

import pytest

from apps.api import preparation_identity as identity
from apps.api import prepared_learning, prepared_models, prepared_views


@pytest.mark.parametrize("prepared", [prepared_models, prepared_views, prepared_learning])
def test_deploy_sha_is_not_an_input_but_feature_flag_is(monkeypatch, prepared):
    monkeypatch.setenv("RENDER_GIT_COMMIT", "chat-only-release")
    monkeypatch.setenv("ONFLOWS_RECOVERY_V2_ENABLED", "false")
    first = prepared.model_key()
    monkeypatch.setenv("RENDER_GIT_COMMIT", "frontend-only-release")
    assert prepared.model_key() == first
    monkeypatch.setenv("ONFLOWS_RECOVERY_V2_ENABLED", "true")
    assert prepared.model_key() != first


@pytest.fixture
def sources(tmp_path):
    def write(module, text):
        path = tmp_path.joinpath(*module.split(".")).with_suffix(".py")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path
    for domain in identity._ROOTS:
        for module in (*identity._ROOTS[domain], *identity._CONTRACTS[domain]):
            write(module, "CONTRACT = 1\n")
    return tmp_path, write


def test_calculation_and_new_transitive_dependency_change_only_dependent_domains(sources):
    root, write = sources
    write("apps.api.model_service", "from biathlon.recovery_v2 import simulate\n")
    write("biathlon.recovery_v2", "def simulate(load):\n    return load * 2\n")
    first = {domain: identity.source_manifest(domain, root=root) for domain in identity._ROOTS}
    write("biathlon.recovery_v2", "def simulate(load):\n    return load * 3\n")
    assert identity.source_manifest("dashboard", root=root) != first["dashboard"]
    assert identity.source_manifest("pages", root=root) != first["pages"]
    assert identity.source_manifest("learning", root=root) == first["learning"]
    # An added local import is discovered automatically, even inside a function.
    write("biathlon.recovery_v2", "def simulate(load):\n    from biathlon.new_policy import factor\n    return load * factor\n")
    write("biathlon.new_policy", "factor = 4\n")
    assert "biathlon.new_policy" in identity.source_manifest("dashboard", root=root)


def test_relative_imports_are_tracked_and_editorial_changes_do_not_invalidate(sources):
    root, write = sources
    write("apps.api.model_service", '"""First explanation."""\nfrom . import calculation\n')
    write("apps.api.calculation", 'def project(value):\n    """Explanation."""\n    return value + 1\n')
    first = identity.source_manifest("dashboard", root=root)
    assert "apps.api.calculation" in first
    write("apps.api.calculation", '# Changed comment\ndef project(value):\n    """Clarified explanation."""\n    return value + 1\n')
    assert identity.source_manifest("dashboard", root=root) == first
    write("apps.api.calculation", 'def project(value):\n    return value + 2\n')
    assert identity.source_manifest("dashboard", root=root) != first


def test_chat_and_frontend_files_are_not_scientific_dependencies(sources):
    root, write = sources
    first = identity.source_manifest("pages", root=root)
    write("apps.api.assistant_service", "NEW_PROMPT = 'updated wording'\n")
    write("apps.web.components.assistant", "UI_REVISION = 2\n")
    assert identity.source_manifest("pages", root=root) == first


def test_missing_declared_source_fails_closed(sources):
    root, _ = sources
    root.joinpath("apps/api/model_service.py").unlink()
    with pytest.raises(RuntimeError, match="Missing preparation dependency"):
        identity.source_manifest("dashboard", root=root)


def test_real_manifest_covers_registered_calculations_without_importing_the_assistant():
    manifests = {domain: identity.source_manifest(domain, _release=True) for domain in identity._ROOTS}
    assert "biathlon.recovery_v2" in manifests["dashboard"]
    assert "biathlon.individual_learning" in manifests["learning"]
    assert "apps.api.training_plan_engine" in manifests["pages"]
    assert not any("assistant" in module for values in manifests.values() for module in values)
    # Any new service used directly by a prepared callback must be declared or
    # reached through a calculation root. Merely adding a route is insufficient.
    for module in identity._CONTRACTS["pages"]:
        if not module.startswith("apps.api.routes."):
            continue
        path = identity._source_path(identity._ROOT, module)
        tree = ast.parse(path.read_text())
        imports = {}
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = list(identity._imports(module, path, node))
                for item in node.names:
                    imports[item.asname or item.name] = names
        functions = {node.name: node for node in tree.body
                     if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        pending = [node for node in functions.values() if any(
            isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Name)
            and decorator.func.id == "prepared_endpoint" for decorator in node.decorator_list)]
        seen = set()
        while pending:
            function = pending.pop()
            if function.name in seen:
                continue
            seen.add(function.name)
            dependencies = set(identity._imports(module, path, function))
            for node in ast.walk(function):
                if isinstance(node, ast.Name):
                    dependencies.update(imports.get(node.id, ()))
                    if node.id in functions:
                        pending.append(functions[node.id])
            for dependency in dependencies:
                if (identity._source_path(identity._ROOT, dependency) is not None
                        and dependency not in identity._BOUNDARIES):
                    assert dependency in manifests["pages"], (function.name, dependency)


def test_catalog_only_change_invalidates_the_domain_that_reads_it(sources):
    root, write = sources
    write("apps.api.learning_view", "from biathlon import method_library\n")
    write("biathlon.method_library", "CATALOG = 'training methods'\n")
    asset = root / identity._ASSETS["biathlon.method_library"][0]
    asset.parent.mkdir(parents=True, exist_ok=True)
    asset.write_text('{"dose": 10}')
    before = identity.source_manifest("learning", root=root)
    dashboard = identity.source_manifest("dashboard", root=root)
    asset.write_text('{"dose": 12}')
    assert identity.source_manifest("learning", root=root) != before
    assert identity.source_manifest("dashboard", root=root) == dashboard


def test_every_checked_in_json_reader_declares_its_scientific_assets():
    modules = set().union(*(identity.source_manifest(domain, _release=True) for domain in identity._ROOTS))
    for module in modules:
        if module.startswith("asset:"):
            continue
        path = identity._source_path(identity._ROOT, module)
        source = path.read_text()
        if ".json" in source and (".read_text(" in source or ".read_bytes(" in source):
            assert module in identity._ASSETS, module


def test_warmed_reader_never_scans_sources_or_package_metadata(monkeypatch):
    identity.warm()
    def unexpected(*args, **kwargs):
        raise AssertionError("HTTP must use the release identity prepared before readiness")
    monkeypatch.setattr(identity, "source_manifest", unexpected)
    monkeypatch.setattr(identity, "package_version", unexpected)
    assert len(identity.model_key("pages", "a-new-contract-label", recovery_v2=True)) == 64
