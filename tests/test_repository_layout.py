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

    for rule in ("config.yaml", "credentials.json", "token.json"):
        assert rule in ignore_rules


def test_sqlite_rules_are_anchored_to_the_repository_root() -> None:
    # Deliberately narrow: `/archive.sqlite*` must not hide a database or a
    # fixture that lives somewhere else in the tree and ought to be tracked.
    ignore_rules = (REPOSITORY_ROOT / ".gitignore").read_text(encoding="utf-8").split()

    assert "/archive.sqlite" in ignore_rules
    assert "/archive.sqlite.*" in ignore_rules
    assert "archive.sqlite" not in ignore_rules
    assert not any(rule.startswith("*.sqlite") for rule in ignore_rules)


# -- Configured sources ----------------------------------------------------


def _config(text: str):
    import yaml

    from photoarchive.config import AppConfig

    return AppConfig.from_mapping(yaml.safe_load(text))


def test_sources_accept_bare_urls() -> None:
    config = _config(
        """
        google_drive: {root_folder_id: "abc"}
        sources:
          - "https://disk.yandex.ru/d/one"
          - "https://disk.yandex.ru/d/two"
        """
    )

    assert [source.url for source in config.sources] == [
        "https://disk.yandex.ru/d/one",
        "https://disk.yandex.ru/d/two",
    ]
    assert all(source.enabled for source in config.sources)


def test_sources_accept_full_entries() -> None:
    config = _config(
        """
        google_drive: {root_folder_id: "abc"}
        sources:
          - url: "https://disk.yandex.ru/d/one"
            label: "School years"
          - url: "https://disk.yandex.ru/d/two"
            enabled: false
        """
    )

    assert config.sources[0].label == "School years"
    assert config.sources[1].enabled is False
    assert [source.url for source in config.enabled_sources] == [
        "https://disk.yandex.ru/d/one"
    ]


def test_missing_sources_is_not_an_error() -> None:
    config = _config('google_drive: {root_folder_id: "abc"}')

    assert config.sources == ()
    assert config.enabled_sources == ()


def test_a_source_without_a_url_is_rejected() -> None:
    import pytest

    from photoarchive.config import ConfigError

    with pytest.raises(ConfigError, match="no url"):
        _config(
            """
            google_drive: {root_folder_id: "abc"}
            sources:
              - label: "forgot the url"
            """
        )


def test_output_dir_is_configurable() -> None:
    from pathlib import Path

    config = _config(
        'google_drive: {root_folder_id: "abc"}\noutput_dir: "./somewhere"'
    )

    assert config.output_dir == Path("./somewhere")


def test_example_config_lists_real_sources() -> None:
    from photoarchive.config import AppConfig

    config = AppConfig.load(REPOSITORY_ROOT / "config.example.yaml")

    assert len(config.enabled_sources) >= 1
    assert all(
        source.url.startswith("https://disk.yandex.") for source in config.sources
    )
