"""Single source of truth for reading project config across the forge codebase.

This module is the canonical entry point for two reads that the rest of the hooks
and skill scripts need everywhere:

  1. Project-wide config (build system, conventions, docs, gates, risk policy, …)
  2. Per-feature manifest (per-run inputs and decisions)

It exists as Phase 0 of the v2 refactor migration (pipeline.json → policy.json
+ per-feature manifest.json). Its job is to centralize the file layout and the
read priority order behind a small, stable API so callers don't have to know
where the bytes live. Migrating writers to policy.json / manifest.json happens
in later phases; until then readers can use this module without coordinating.

Dual-read fallback policy
-------------------------
Project-wide config is read in priority order:

  1. ``<root>/ground/policy.json``     — canonical v2 location.
  2. ``<root>/ground/pipeline.json``   — legacy v1 fallback. DEPRECATED; will
                                          be removed in v3.0.
  3. ``{}``                            — empty config. NOT an error: callers
                                          decide what defaults to apply.

The legacy fallback lets projects mid-migration keep working without forcing a
coordinated rename. Once v3.0 lands, the second tier disappears and any caller
still expecting pipeline.json will surface a missing-config error at startup
rather than at first write.

Per-feature manifests live at:

  ``<root>/ground/statements/<skill>/<feature>/manifest.json``

When the file exists, its top-level ``version`` field must equal 2 (the only
version this loader understands). A mismatch raises :class:`ValueError` —
silently accepting a wrong version would let migration bugs hide for a whole
run before blowing up somewhere worse. v1 manifests must be migrated to v2 by
``init.py: migrate_manifest_if_needed`` before this loader will read them.

The reads here are pure and non-raising for "file missing" / "file empty":
those are normal operational states (fresh init, no active run). Callers that
need to distinguish "absent" from "present but empty" can call
:func:`manifest_path` and stat the file themselves.
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path

# Project-root marker filenames. A directory is a forge project if any of these
# exist under ``<root>/ground/``. Adding a new project-wide config file = add
# its filename here so :func:`find_project_root` keeps recognizing existing
# projects during migration.
PROJECT_ROOT_MARKERS: tuple[str, ...] = ("policy.json", "pipeline.json")

# Canonical manifest schema version. v1 manifests must be migrated to v2 by
# ``init.py: migrate_manifest_if_needed`` before this loader is happy with them.
MANIFEST_VERSION: int = 2

# v1 → v2 deprecation: emitted when ground/pipeline.json is read as a legacy
# fallback. Will be removed in v3.0 once all projects have migrated via
# ``init_pipeline_config.py --migrate``. Single source of truth so
# ``risk_ladder.pipeline_cfg`` and ``resolve_phases._load_policy`` reuse the
# same message.
_LEGACY_DEPRECATION_MSG = (
    "ground/pipeline.json is deprecated and will be removed in v3.0. "
    "Migrate to ground/policy.json via init_pipeline_config.py --migrate."
)


def manifest_path(root: Path, skill: str, feature: str) -> Path:
    """Return ``<root>/ground/statements/<skill>/<feature>/manifest.json``."""
    return Path(root) / "ground" / "statements" / skill / feature / "manifest.json"


def project_root_marker_paths() -> tuple[str, ...]:
    """Return the marker filenames that identify a forge project root.

    Kept as a function (rather than having callers import
    :data:`PROJECT_ROOT_MARKERS` directly) so that future extensibility —
    e.g. computing markers from env vars, adding a third file, or
    per-deployment overrides — doesn't ripple through every caller.
    """
    return PROJECT_ROOT_MARKERS


def find_project_root(start: Path) -> Path | None:
    """Walk up from ``start`` to find the project root, by strict priority order.

    Three priority levels, each scanned across the FULL chain ``[start, *start.parents]``
    before falling back to the next level (priority > depth — never the other way round):

      1. ``.git``                                                — git repo marker.
      2. ``build.gradle`` / ``settings.gradle`` / ``pom.xml``    — build-system marker.
      3. ``ground/<policy.json|pipeline.json>``                  — forge config marker
         (filenames from :func:`project_root_marker_paths`).

    Earlier priority wins even at a deeper ancestor. This matters for multi-module
    Gradle repos: a path like ``<repo>/module-a/src`` must resolve to ``<repo>``
    (which has ``.git``), NOT to ``<repo>/module-a`` (which has ``build.gradle``).
    See :mod:`_project` docstring for the original bug this ordering prevents.

    Returns the matched project root, or ``None`` if no candidate up to the filesystem
    root qualifies. Callers that want a non-None default (legacy semantics that
    returned ``start`` on miss) should ``or start`` at the call site — see the
    thin delegation wrappers in :mod:`_project` and the co-located ``_root.py``
    helpers under ``skills/``.
    """
    start = Path(start)
    chain = (start, *start.parents)

    def _has_git(p: Path) -> bool:
        return (p / ".git").exists()

    def _has_build(p: Path) -> bool:
        return (
            (p / "build.gradle").exists()
            or (p / "settings.gradle").exists()
            or (p / "pom.xml").exists()
        )

    def _has_ground(p: Path) -> bool:
        ground = p / "ground"
        if not ground.is_dir():
            return False
        return any((ground / m).exists() for m in project_root_marker_paths())

    for predicate in (_has_git, _has_build, _has_ground):
        for candidate in chain:
            if predicate(candidate):
                return candidate
    return None


def load_project_config(root: Path) -> dict:
    """Read project-wide config from ``<root>/ground/`` with a 3-tier fallback.

    Priority order:

      1. ``<root>/ground/policy.json``   — v2, canonical.
      2. ``<root>/ground/pipeline.json`` — v1, legacy fallback. DEPRECATED,
         will be removed in v3.0.
      3. ``{}`` — empty config. NOT an error: callers decide what defaults to
         apply (and may choose to refuse to run with an empty config).

    Returns the parsed dict. This is a pure read: it never warns, never logs,
    and never raises on a missing file. Deprecation diagnostics live in
    ``config-helper._check_deprecated_in_policy`` so they're emitted once at a
    well-defined chokepoint rather than scattered across every read site.

    File-level errors (invalid JSON, permission denied) are swallowed and fall
    through to the next tier, matching the "best effort, never crash the hook"
    contract that the rest of the codebase assumes for config reads.
    """
    root = Path(root)
    ground = root / "ground"
    policy = ground / "policy.json"
    if policy.exists():
        try:
            return json.loads(policy.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            # Corrupt policy.json — emit UserWarning so operator sees the
            # corruption in stderr; do NOT silently fall through to legacy
            # (that would hide the real config error behind a different file).
            # Return {} so the caller can still proceed (best-effort contract).
            warnings.warn(
                f"corrupt policy.json at {policy}: {e}. "
                f"Treating config as empty; fix the JSON to recover.",
                UserWarning, stacklevel=2,
            )
            return {}
    legacy = ground / "pipeline.json"
    if legacy.exists():
        # v1 → v2 deprecation: emit a DeprecationWarning so operators see the
        # legacy path being used in their run logs. Default Python warning
        # filter deduplicates by (message, category, source location), so
        # repeated reads within the same process produce a single line on
        # stderr. The dual-read itself stays — it's still functional and
        # will be removed in v3.0 alongside this warning.
        warnings.warn(_LEGACY_DEPRECATION_MSG, DeprecationWarning, stacklevel=2)
        try:
            return json.loads(legacy.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            warnings.warn(
                f"corrupt pipeline.json at {legacy}: {e}. "
                f"Treating config as empty; fix the JSON or run "
                f"init_pipeline_config.py --migrate to recover.",
                UserWarning, stacklevel=2,
            )
            return {}
    return {}


def load_manifest(root: Path, skill: str, feature: str) -> dict:
    """Read ``<root>/ground/statements/<skill>/<feature>/manifest.json``.

    Returns the parsed dict, or ``{}`` if the file does not exist (NOT an
    error — the caller decides whether a missing manifest is fatal; for
    example, ``gate-guard`` treats missing manifest as "no active run" while
    ``record_gate`` treats it as a hard error).

    If the file exists and contains a top-level ``version`` field that is not
    equal to :data:`MANIFEST_VERSION`, raises :class:`ValueError`. The error
    message includes the file path so a stack trace points at the bad file
    rather than at this loader. We deliberately do NOT silently coerce v1 →
    v2 here: auto-migration writes audit fields and must be triggered
    explicitly by ``init.py: migrate_manifest_if_needed``.

    File-level errors (invalid JSON, permission denied) are swallowed and
    treated as "no manifest" — same best-effort contract as
    :func:`load_project_config`.
    """
    root = Path(root)
    mp = manifest_path(root, skill, feature)
    if not mp.exists():
        return {}
    try:
        data = json.loads(mp.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    version = data.get("version")
    if version is not None and version != MANIFEST_VERSION:
        raise ValueError(
            f"manifest version mismatch at {mp}: expected {MANIFEST_VERSION}, "
            f"got {version!r}. Run the v1->v2 migration "
            f"(init.py: migrate_manifest_if_needed) before retrying."
        )
    return data


if __name__ == "__main__":
    # 5-line smoke test: spin up a tmpdir with policy.json and read it back.
    # Mirrors Test 1 of the broader suite; kept tiny so a `python _config_loader.py`
    # from anywhere is enough to catch a broken install.
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "ground"
        p.mkdir()
        (p / "policy.json").write_text('{"hello": "world"}', encoding="utf-8")
        cfg = load_project_config(td)
        assert cfg == {"hello": "world"}, f"smoke test failed: {cfg}"
    print("smoke OK")
