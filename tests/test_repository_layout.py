"""Repository hygiene: documentation links and ignore rules.

These read files from the repository itself; nothing here touches a network.
"""

from __future__ import annotations

from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent

SPECIFICATION_PATH = "mds/family-photo-archive-project.md"


def test_readme_points_at_the_specification() -> None:
    readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")

    assert SPECIFICATION_PATH in readme


def test_specification_file_exists() -> None:
    assert (REPOSITORY_ROOT / SPECIFICATION_PATH).is_file()


def test_ide_settings_are_ignored() -> None:
    ignore_rules = (REPOSITORY_ROOT / ".gitignore").read_text(encoding="utf-8").split()

    assert ".idea/" in ignore_rules


def test_secrets_and_local_state_are_ignored() -> None:
    ignore_rules = (REPOSITORY_ROOT / ".gitignore").read_text(encoding="utf-8").split()

    for rule in ("config.yaml", "credentials.json", "token.json", "archive.sqlite"):
        assert rule in ignore_rules
