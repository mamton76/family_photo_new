"""Source-root identity tests. No cloud access."""

from __future__ import annotations

from photoarchive.models import SourceRoot
from photoarchive.storage.yandex import fallback_root_name


def test_identity_is_derived_from_the_url_not_the_name() -> None:
    before = SourceRoot(url="https://disk.yandex.ru/d/abc123", name="Family Archive")
    after = SourceRoot(url="https://disk.yandex.ru/d/abc123", name="Семейный архив")

    # Renaming the source folder must not look like a brand new archive.
    assert before.identity == after.identity


def test_identity_differs_between_source_roots() -> None:
    first = SourceRoot(url="https://disk.yandex.ru/d/abc123", name="Archive")
    second = SourceRoot(url="https://disk.yandex.ru/d/xyz789", name="Archive")

    assert first.identity != second.identity


def test_identity_ignores_trailing_slash_and_whitespace() -> None:
    plain = SourceRoot(url="https://disk.yandex.ru/d/abc123", name="Archive")
    padded = SourceRoot(url="  https://disk.yandex.ru/d/abc123/  ", name="Archive")

    assert plain.identity == padded.identity


def test_identity_is_stable_and_short() -> None:
    root = SourceRoot(url="https://disk.yandex.ru/d/abc123", name="Archive")

    assert root.identity == root.identity
    assert len(root.identity) == 16
    assert root.identity.isalnum()


def test_fallback_root_name_uses_the_public_id() -> None:
    assert fallback_root_name("https://disk.yandex.ru/d/cFwfbSEQ7IB37g") == "cFwfbSEQ7IB37g"
    assert fallback_root_name("https://disk.yandex.ru/d/cFwfbSEQ7IB37g/") == "cFwfbSEQ7IB37g"
    assert fallback_root_name("https://disk.yandex.ru/d/abc?x=1") == "abc"
