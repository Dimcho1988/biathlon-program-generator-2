"""Code identities for the three durable calculation domains.

Database revisions identify athlete inputs; these fingerprints identify the code
that interprets them. They are computed once per process from the immutable
release sources, independently of its Git SHA. Static local imports extend each
root automatically, including imports inside functions. HTTP/chat entry points
are not model dependencies. Source adapter and prepared boundary modules are
hashed without traversing their unrelated worker/authentication imports.
"""
import ast
from functools import lru_cache
from hashlib import sha256
from importlib.metadata import version as package_version
import json
from pathlib import Path
from threading import Lock


_ROOT = Path(__file__).resolve().parents[2]
_ROOTS = {
    "dashboard": ("apps.api.model_service", "apps.api.planning_input_bundle",
                  "apps.api.schemas", "apps.api.read_session"),
    "learning": ("apps.api.learning_view", "apps.api.lifetime_history"),
    "pages": ("apps.api.model_service", "apps.api.speed_load", "apps.api.speed_zone_history",
              "apps.api.trainability_history", "apps.api.response_service", "apps.api.management_service",
              "apps.api.management_lifecycle", "apps.api.management_overview",
              "apps.api.management_speed_comparison", "apps.api.management_projection",
              "apps.api.method_library_service", "apps.api.read_session"),
}
# Keep route output/validation contracts in the identity, but their unrelated
# write endpoints must not import the entire assistant into the model graph.
_CONTRACTS = {
    "dashboard": ("apps.api.prepared_models",),
    "learning": ("apps.api.prepared_learning",),
    "pages": ("apps.api.prepared_views", "apps.api.prepared_view_storage",
              "apps.api.routes.activities", "apps.api.routes.models", "apps.api.routes.response",
              "apps.api.routes.management", "apps.api.routes.management_speed_comparison",
              "apps.api.sync_service"),
}
# These operate on access, transport, scheduling, or telemetry. Their values
# are not scientific inputs; authorization still runs before every cache read.
_BOUNDARIES = frozenset({"apps.api.dependencies", "apps.api.planning_jobs", "apps.api.request_metrics",
    "apps.api.memory_budget", "apps.api.http_runtime", "apps.api.preparation_identity"})
_TERMINALS = frozenset({"apps.api.oauth_store", *sum(_CONTRACTS.values(), ())})


# These checked-in catalogs are read by their owning calculation modules.
_ASSETS = {
    "biathlon.method_library": ("biathlon/data/training_method_library.json",),
    "biathlon.training_methods": ("biathlon/data/methods_v08/onflows_training_method_library_v0.8.json",),
}
_identity_lock = Lock()


def _source_path(root, module):
    path = root.joinpath(*module.split(".")).with_suffix(".py")
    if path.is_file():
        return path
    path = root.joinpath(*module.split("."), "__init__.py")
    return path if path.is_file() else None


def _imports(module, path, tree):
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (item.name for item in node.names)
        elif isinstance(node, ast.ImportFrom):
            parts = package.split(".")
            prefix = ".".join(parts[:len(parts)-node.level+1]) if node.level else ""
            base = ".".join(part for part in (prefix, node.module) if part)
            yield base
            yield from (f"{base}.{item.name}" for item in node.names if item.name != "*")


def _source_record(root, module):
    path = _source_path(root, module)
    if path is None:
        return None
    tree = ast.parse(path.read_text(encoding="utf-8"))
    dependencies = tuple(_imports(module, path, tree)) if module not in _TERMINALS else ()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant):
                if isinstance(node.body[0].value.value, str):
                    node.body = node.body[1:]
    normalized = ast.dump(tree, include_attributes=False)
    return sha256(normalized.encode()).hexdigest(), dependencies


@lru_cache(maxsize=None)
def _release_source(module):
    # Share small digests/import lists across overlapping calculation domains.
    # ASTs and source strings are released immediately after each module.
    return _source_record(_ROOT, module)


def source_manifest(domain, *, root=None, _release=False):
    """Auditable source list; no imported module executes during discovery.

    Missing declared roots fail closed instead of silently accepting an old
    cache. Ordinary external library imports are covered by runtime versions.
    """
    root = root or _ROOT
    required = (*_ROOTS[domain], *_CONTRACTS[domain])
    for module in required:
        if _source_path(root, module) is None:
            raise RuntimeError(f"Missing preparation dependency: {module}")
    pending, visited, sources = list(required), set(), {}
    while pending:
        module = pending.pop()
        if module in visited or module in _BOUNDARIES:
            continue
        visited.add(module)
        record = _release_source(module) if _release else _source_record(root, module)
        if record is None:
            continue
        sources[module], dependencies = record
        pending.extend(dependencies)
        for asset in _ASSETS.get(module, ()):
            sources[f"asset:{asset}"] = sha256(root.joinpath(asset).read_bytes()).hexdigest()
    return sources


@lru_cache(maxsize=3)
def _source_fingerprint(domain):
    # Sources and installed numerical/validation libraries are immutable for a
    # running process. Cache only their small digest, never athlete data.
    identity = {"sources": source_manifest(domain, _release=True), "runtime": {
        name: package_version(name) for name in ("numpy", "pandas", "scipy", "pydantic")}}
    return sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def source_fingerprint(domain):
    # lru_cache alone permits duplicate concurrent misses. Serialize discovery,
    # including the first HTTP request in adapters without an API lifespan.
    with _identity_lock:
        return _source_fingerprint(domain)


def warm():
    for domain in _ROOTS:
        source_fingerprint(domain)


@lru_cache(maxsize=12)
def model_key(domain, version, *, recovery_v2):
    return sha256(json.dumps({"domain": domain, "version": version,
        "calculation": source_fingerprint(domain), "recovery_v2": recovery_v2},
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()
