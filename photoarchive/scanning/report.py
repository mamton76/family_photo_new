"""Dry-run reporting.

A dry run inspects a source archive and prints what a real scan *would* do.
It reads from Yandex Disk and touches nothing else: no Google Drive, no
workbooks, no EXIF, no publishing.

Document *loading* is injected as a callable, so the whole reporting layer is
pure with respect to networking and can be unit-tested offline. The output is
deterministic for an unchanged source, which makes two runs easy to diff.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from photoarchive.models import RemoteSourceItem, SourceRoot, WorkflowStatus
from photoarchive.parsing.descriptions import (
    ParsedDescriptionDocument,
    Reconciliation,
    parse_description_document,
    reconcile_entries,
)
from photoarchive.scanning.scanner import (
    FolderScanPlan,
    destination_path,
    is_description_document,
    plan_folders,
)

#: Shown instead of a folder path for photos sitting in the source root.
ROOT_FOLDER_LABEL = "/"

NONE_LABEL = "none"

#: Loads the paragraphs of a description document, or raises to report why not.
DocumentLoader = Callable[[RemoteSourceItem], tuple[str, ...]]


@dataclass(frozen=True, slots=True)
class FolderReport:
    """What a dry run found in one photo-containing folder."""

    plan: FolderScanPlan
    document: ParsedDescriptionDocument | None = None
    reconciliation: Reconciliation | None = None
    error: str | None = None

    @property
    def entry_count(self) -> int:
        return len(self.document.entries) if self.document else 0

    @property
    def present_and_described(self) -> int:
        return len(self.reconciliation.present_and_described) if self.reconciliation else 0

    @property
    def described_but_absent(self) -> int:
        return len(self.reconciliation.described_but_absent) if self.reconciliation else 0

    @property
    def present_without_description(self) -> int:
        if self.reconciliation is None:
            return len(self.plan.photos)
        return len(self.reconciliation.undescribed_photos)

    @property
    def section_divider_count(self) -> int:
        return len(self.document.section_dividers) if self.document else 0

    @property
    def source_note_count(self) -> int:
        """Deduplicated per entry: reporting is human-facing."""
        return self.document.display_source_note_count if self.document else 0


@dataclass(frozen=True, slots=True)
class DryRunReport:
    """Everything a dry run discovered about one source root."""

    source_root: SourceRoot
    folders: tuple[FolderReport, ...] = ()
    folders_discovered: int = 0
    files_discovered: int = 0

    @property
    def folders_with_photos(self) -> int:
        return len(self.folders)

    @property
    def photos_found(self) -> int:
        return sum(len(folder.plan.photos) for folder in self.folders)

    @property
    def documents_found(self) -> int:
        return sum(1 for folder in self.folders if folder.plan.has_description)

    @property
    def description_conflicts(self) -> int:
        return sum(1 for folder in self.folders if folder.plan.has_ambiguous_description)

    @property
    def folders_without_description(self) -> int:
        return sum(
            1
            for folder in self.folders
            if not folder.plan.has_description
            and not folder.plan.has_ambiguous_description
        )

    @property
    def entries_found(self) -> int:
        return sum(folder.entry_count for folder in self.folders)

    @property
    def described_but_absent(self) -> int:
        return sum(folder.described_but_absent for folder in self.folders)


def build_dry_run_report(
    source_root: SourceRoot,
    items: Iterable[RemoteSourceItem],
    load_document: DocumentLoader | None = None,
) -> DryRunReport:
    """Turn a recursive listing into a report, reusing the normal planning.

    ``load_document`` fetches and extracts a DOCX; when it is ``None`` the
    documents are located and reported but not parsed.
    """
    materialized = list(items)
    folders: list[FolderReport] = []

    for plan in plan_folders(materialized):
        folders.append(_build_folder_report(plan, load_document))

    return DryRunReport(
        source_root=source_root,
        folders=tuple(folders),
        folders_discovered=sum(1 for item in materialized if item.is_directory),
        files_discovered=sum(1 for item in materialized if not item.is_directory),
    )


def _build_folder_report(
    plan: FolderScanPlan, load_document: DocumentLoader | None
) -> FolderReport:
    document_item = plan.description
    if document_item is None or load_document is None:
        return FolderReport(plan=plan)

    try:
        paragraphs = load_document(document_item)
    except Exception as error:  # noqa: BLE001 - reported, never fatal to a scan
        return FolderReport(plan=plan, error=f"{type(error).__name__}: {error}")

    # The photos present are the strongest signal for what counts as a photo
    # reference in this folder, so they drive the grouping.
    document = parse_description_document(
        paragraphs, photo_names=[photo.name for photo in plan.photos]
    )
    reconciliation = reconcile_entries(document.entries, plan.photos)
    return FolderReport(plan=plan, document=document, reconciliation=reconciliation)


def render_dry_run_report(report: DryRunReport, *, verbose: bool = False) -> str:
    """Render a report as human-readable text.

    In verbose mode each folder block also lists its structured description
    entries; counts alone are shown otherwise, so a large archive stays
    readable.
    """
    lines: list[str] = [
        f"Source root: {report.source_root.name}",
        f"Source URL: {report.source_root.url}",
        "",
        f"Folders discovered: {report.folders_discovered}",
        f"Files discovered: {report.files_discovered}",
        f"Folders containing photos: {report.folders_with_photos}",
        f"Photos found: {report.photos_found}",
        f"DOCX descriptions found: {report.documents_found}",
        f"Description entries: {report.entries_found}",
        f"Described but absent: {report.described_but_absent}",
        f"Description conflicts: {report.description_conflicts}",
    ]

    for folder in report.folders:
        lines.append("")
        lines.extend(_render_folder(report.source_root, folder, verbose=verbose))

    return "\n".join(lines)


def _render_folder(
    source_root: SourceRoot, folder: FolderReport, *, verbose: bool
) -> list[str]:
    plan = folder.plan
    lines = [
        f"[folder] {plan.folder_path or ROOT_FOLDER_LABEL}",
        f"destination: {destination_path(source_root, plan.folder_path)}",
        "",
        f"photos present: {len(plan.photos)}",
        f"DOCX descriptions: {len(plan.docx_candidates)}",
    ]

    if plan.has_ambiguous_description:
        lines.append("description conflict: yes")
        lines.append("DOCX candidates:")
        lines.extend(f"  - {item.name}" for item in plan.docx_candidates)
        lines.append("no document parsed: resolve the conflict to continue")
    elif plan.description is not None:
        lines.append(f"description file: {plan.description.name}")
    else:
        lines.append(f"description file: {NONE_LABEL}")
        lines.append("warning: no DOCX description found")

    if folder.error:
        lines.append(f"error: could not read the description document ({folder.error})")

    if folder.document is not None:
        lines.extend(
            [
                "",
                f"description entries: {folder.entry_count}",
                f"present + described: {folder.present_and_described}",
                f"present without description: {folder.present_without_description}",
                f"described but absent: {folder.described_but_absent}",
                f"section dividers: {folder.section_divider_count}",
                f'source notes "нет фото": {folder.source_note_count}',
            ]
        )

    if plan.other_files:
        lines.append("")
        lines.append("other non-photo files:")
        lines.extend(f"  - {item.name}" for item in plan.other_files)

    if verbose and folder.reconciliation is not None:
        for reconciled in folder.reconciliation.entries:
            lines.append("")
            lines.extend(_render_entry(reconciled))

    return lines


def _render_entry(reconciled) -> list[str]:
    entry = reconciled.entry
    lines = [
        f"[entry] {entry.reference}",
        f"matched photo: {reconciled.photo.name if reconciled.photo else NONE_LABEL}",
        f"status: {'present' if reconciled.present else WorkflowStatus.DESCRIBED_ABSENT.value}",
        f"section context: {entry.section_context or NONE_LABEL}",
    ]

    if entry.source_notes:
        lines.append("source notes:")
        lines.extend(f"  - {note}" for note in entry.display_source_notes)

    lines.append("description:")
    lines.append(f"  {entry.text}" if entry.text else f"  {NONE_LABEL}")
    return lines
