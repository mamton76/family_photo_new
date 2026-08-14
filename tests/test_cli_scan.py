"""The plain ``scan`` command, whose destination transport does not exist yet.

A stub is allowed to be unfinished; it is not allowed to explain itself in the
language of the code that failed. These tests pin the message a person sees.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import app  # noqa: E402
from photoarchive.models import SourceRoot  # noqa: E402


class _StubSource:
    """Stands in for the Yandex client: answers the root, records closing."""

    created: list["_StubSource"] = []

    def __init__(self, *args: object, **kwargs: object) -> None:
        self.closed = False
        _StubSource.created.append(self)

    @staticmethod
    def public_key_from_url(url: str) -> str:
        return url

    def describe_root(self) -> SourceRoot:
        return SourceRoot(url="https://disk.yandex.ru/d/test", name="Ф-Тест")

    def close(self) -> None:
        self.closed = True


@pytest.fixture()
def scan_args(tmp_path: Path) -> argparse.Namespace:
    return argparse.Namespace(
        source_url="https://disk.yandex.ru/d/test",
        dry_run=False,
        local_review=False,
        output_dir=tmp_path / "review-output",
        verbose=False,
    )


def _run(monkeypatch, config, args):
    _StubSource.created.clear()
    monkeypatch.setattr(app, "YandexDiskStorage", _StubSource)
    code = app.command_scan(args, config)
    return code, _StubSource.created[-1]


def test_scan_reports_the_missing_transport_in_plain_words(
    monkeypatch, capsys, scan_args, tmp_path
) -> None:
    config = app.AppConfig.load(REPO_ROOT / "config.example.yaml")
    # The cache path in the config is relative, so run from a scratch directory
    # rather than writing into the working tree.
    monkeypatch.chdir(tmp_path)

    code, source = _run(monkeypatch, config, scan_args)

    assert code == 3
    message = capsys.readouterr().err
    assert "Google Drive transport is not wired up yet" in message
    # The two commands that do work are named, so the reader is not stuck.
    assert "--local-review" in message
    assert "run" in message
    # No internal name leaks out.
    assert "NotImplementedError" not in message
    assert "Scanner.scan" not in message
    # The source connection is released even on the failure path.
    assert source.closed
