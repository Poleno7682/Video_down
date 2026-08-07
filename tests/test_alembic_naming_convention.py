"""Guards the _video_bot table-naming convention (docs/db-table-namespacing.md)
against migrations that create or rename a table without the suffix directly
via op.create_table()/op.rename_table(), bypassing the Base subclass check
in app/db/models.py entirely.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.db.models import TABLE_SUFFIX

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "alembic" / "versions"

# Migrations written before this naming convention existed - not rewritten
# retroactively (already-applied revisions must not change). Only new files
# added after docs/db-table-namespacing.md land under this test.
EXEMPT_FILES = {
    "0001_initial.py",
    "0002_user_ban_columns.py",
    "0003_user_cookies.py",
    "0004_google_oauth_token.py",
    "0005_proxies.py",
    "0006_request_status_index.py",
}


def _bad_table_names(path: Path) -> list[str]:
    tree = ast.parse(path.read_text())
    # Only upgrade() matters: downgrade() legitimately renames suffixed
    # tables back to their original bare names, or drops fresh-created ones.
    upgrade_fn = next(
        (n for n in ast.iter_child_nodes(tree) if isinstance(n, ast.FunctionDef) and n.name == "upgrade"),
        None,
    )
    if upgrade_fn is None:
        return []
    bad = []
    for node in ast.walk(upgrade_fn):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr in ("create_table", "rename_table")):
            continue
        if not (isinstance(func.value, ast.Name) and func.value.id == "op"):
            continue
        if func.attr == "create_table":
            # op.create_table("name", ...) - first positional arg.
            args_to_check = node.args[:1]
        else:
            # op.rename_table("old", "new") - only the destination name
            # matters; "old" is expected to be the pre-existing bare name.
            args_to_check = node.args[1:2]
        for arg in args_to_check:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                if not arg.value.endswith(TABLE_SUFFIX):
                    bad.append(arg.value)
    return bad


def _migration_files() -> list[Path]:
    return sorted(
        p for p in MIGRATIONS_DIR.glob("*.py") if p.name not in EXEMPT_FILES
    )


@pytest.mark.parametrize("path", _migration_files(), ids=lambda p: p.name)
def test_migration_creates_only_suffixed_tables(path: Path):
    bad = _bad_table_names(path)
    assert not bad, f"{path.name}: table name(s) without {TABLE_SUFFIX!r} suffix: {bad}"
