"""Scan orchestration.

The eventual ``scan`` flow, in order:

1. receive one Yandex Disk source-root URL from the CLI;
2. resolve it to a :class:`SourceRoot` (stable identity + folder name);
3. recursively enumerate its contents through :class:`ReadableStorage`;
4. preserve relative nested paths for mirroring;
5. detect folders that *directly* contain photos;
6. locate a description file in the same folder (scope: ``current_folder``);
7. hand descriptions and filenames to the matcher and metadata proposer;
8. create or find ``<drive root>/<source root name>/<relative path>``;
9. create or update ``review.xlsx`` in that folder;
10. preserve existing human-reviewed values;
11. mark new, changed and missing source items.

Steps 1–7 are pure or storage-interface-level and are unit-testable without
any cloud access; steps 8–11 are TODO until the provider adapters exist.

Every supplied source root gets its **own dedicated folder** under the
configured Google Drive root, named after the source folder, so two source
roots can never write into each other's subtree.

The source archive is never written to at any point in this flow.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from photoarchive.config import AppConfig
from photoarchive.models import RemoteSourceItem, SourceRoot
from photoarchive.state import StateRepository
from photoarchive.storage.base import ReadableStorage, WritableStorage

PHOTO_EXTENSIONS: frozenset[str] = frozenset(
    {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".heic", ".heif", ".webp", ".bmp", ".gif"}
)

#: Characters Google Drive folder names must not contain, plus control chars.
_UNSAFE_NAME_CHARACTERS = str.maketrans({"/": "-", "\\": "-", "\n": " ", "\r": " ", "\t": " "})

FALLBACK_ROOT_NAME = "source"


def is_photo(name: str) -> bool:
    """Report whether a filename looks like a photo, by extension."""
    _, dot, extension = name.rpartition(".")
    return bool(dot) and f".{extension.lower()}" in PHOTO_EXTENSIONS


def normalize_relative_path(path: str) -> str:
    """Normalise a remote relative path to ``a/b/c`` form.

    Backslashes become forward slashes, and leading, trailing and duplicated
    separators are dropped. The source root itself is ``""``.
    """
    segments = [segment for segment in path.replace("\\", "/").split("/") if segment]
    return "/".join(segments)


def join_relative_path(parent: str, name: str) -> str:
    """Append ``name`` to a relative folder path, keeping the root as ``""``."""
    parent = normalize_relative_path(parent)
    name = normalize_relative_path(name)
    if not parent:
        return name
    if not name:
        return parent
    return f"{parent}/{name}"


def sanitize_folder_name(name: str) -> str:
    """Make a source folder name safe to use as a single destination folder.

    Path separators would otherwise split one source root across several
    destination folders, so they are replaced rather than removed. An empty or
    whitespace-only name falls back to :data:`FALLBACK_ROOT_NAME`, since a
    source root must always land in a folder of its own.
    """
    cleaned = name.translate(_UNSAFE_NAME_CHARACTERS).strip().strip(".")
    cleaned = " ".join(cleaned.split())
    return cleaned or FALLBACK_ROOT_NAME


def destination_path(source_root: SourceRoot, relative_path: str = "") -> str:
    """Map a source-relative path to its path below the Google Drive root.

    ``1990/Valaam`` under a source root named ``Family Archive`` becomes
    ``Family Archive/1990/Valaam``. The result is still relative: the
    configured ``root_folder_id`` is the anchor and is applied by the storage
    adapter, never by this function.
    """
    return join_relative_path(sanitize_folder_name(source_root.name), relative_path)


def description_priority(name: str, patterns: Sequence[str]) -> int | None:
    """Return the configured priority of a description filename.

    Lower is better, and the priority *is* the pattern's position in
    ``descriptions.patterns``, so configuration order decides which file wins
    when a folder contains more than one. ``None`` means "not a description
    file".
    """
    lowered = name.casefold()
    for index, pattern in enumerate(patterns):
        if lowered == pattern.casefold():
            return index
    return None


def is_description_file(name: str, patterns: Sequence[str]) -> bool:
    """Match a filename against the configured description-file patterns."""
    return description_priority(name, patterns) is not None


@dataclass(slots=True)
class FolderScanPlan:
    """One destination workbook's worth of work.

    A plan is only produced for folders that *directly* contain photos, which
    is exactly the rule for where ``review.xlsx`` is created.
    """

    folder_path: str
    photos: list[RemoteSourceItem] = field(default_factory=list)
    descriptions: list[RemoteSourceItem] = field(default_factory=list)

    @property
    def description(self) -> RemoteSourceItem | None:
        """The description file that applies, or ``None``.

        When a folder holds several matching files, the winner is decided
        deterministically by configuration order and then by name, so repeated
        scans of an unchanged folder always pick the same file.
        """
        return self.descriptions[0] if self.descriptions else None

    @property
    def has_ambiguous_description(self) -> bool:
        """True when several description files matched and one was chosen."""
        return len(self.descriptions) > 1

    @property
    def folder_names(self) -> tuple[str, ...]:
        """Path segments, used as metadata context (e.g. year, place)."""
        return tuple(segment for segment in self.folder_path.split("/") if segment)


def plan_folders(
    items: Iterable[RemoteSourceItem],
    description_patterns: Sequence[str],
) -> list[FolderScanPlan]:
    """Group a flat recursive listing into per-folder scan plans.

    Nested relative paths are preserved verbatim, so the destination mirror can
    reuse them. Folders without photos produce no plan and therefore no
    workbook, even when they contain a description file.
    """
    photos: dict[str, list[RemoteSourceItem]] = {}
    descriptions: dict[str, list[tuple[int, str, RemoteSourceItem]]] = {}

    for item in items:
        if item.is_directory:
            continue
        folder = normalize_relative_path(item.parent_path)
        if is_photo(item.name):
            photos.setdefault(folder, []).append(item)
            continue
        priority = description_priority(item.name, description_patterns)
        if priority is not None:
            descriptions.setdefault(folder, []).append((priority, item.name, item))

    plans: list[FolderScanPlan] = []
    for folder in sorted(photos):
        candidates = sorted(descriptions.get(folder, []), key=lambda entry: entry[:2])
        plans.append(
            FolderScanPlan(
                folder_path=folder,
                photos=sorted(photos[folder], key=lambda photo: photo.name),
                descriptions=[item for _, _, item in candidates],
            )
        )
    return plans


class Scanner:
    """Drives one ``scan`` run over a single source root.

    Only the cloud-free planning stage is implemented; mirroring and workbook
    updates raise until the storage adapters land.
    """

    def __init__(
        self,
        config: AppConfig,
        source: ReadableStorage,
        destination: WritableStorage,
        state: StateRepository,
    ) -> None:
        self.config = config
        self.source = source
        self.destination = destination
        self.state = state

    def plan(self, source_root: SourceRoot) -> list[FolderScanPlan]:
        """Enumerate the source and group it into per-folder work items.

        The listing call is the only cloud interaction here; the grouping is
        pure and covered by unit tests.
        """
        items = self.source.list_recursive()
        return plan_folders(items, self.config.descriptions.patterns)

    def scan(self, source_root: SourceRoot) -> list[FolderScanPlan]:
        """Run a full scan for one source root.

        TODO: for each plan — download the description, propose metadata, mirror
        the folder onto Google Drive under this root's dedicated folder, merge
        into ``review.xlsx`` preserving human edits, and record new/changed/
        missing items in the state database.
        """
        raise NotImplementedError("Scanner.scan is not implemented yet")

    def mirror_folder(self, source_root: SourceRoot, folder_path: str) -> str:
        """Create or find the destination folder mirroring ``folder_path``.

        TODO: delegate to ``WritableStorage.ensure_folder(destination_path(...))``;
        must be idempotent across repeated runs.
        """
        raise NotImplementedError("Scanner.mirror_folder is not implemented yet")
