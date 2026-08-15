"""Rendering the archive-wide dashboard as one self-contained HTML file.

``review-all.html`` is a **read-only viewer**. It never writes to the review
workbooks, the catalog or SQLite, and it deliberately contains no controls that
look like they would save anything. The per-folder ``review.xlsx`` files stay
the single place review metadata is edited.

Everything is inline — CSS, a little vanilla JavaScript, and the preview images
as data URIs — so the page opens straight from ``file://`` with no network, no
CDN and no build step.
"""

from __future__ import annotations

import html
import logging
from datetime import datetime, timezone
from pathlib import Path

from photoarchive.coverage import FolderDescriptionStatus, PhotoDescriptionCoverage
from photoarchive.dashboard.aggregate import Aggregate, FolderGroup, needs_review
from photoarchive.dashboard.links import PhotoDestinations, links_for
from photoarchive.dashboard.preview import PreviewProvider
from photoarchive.geo import parse_latlon
from photoarchive.models import WorkflowStatus
from photoarchive.review.model import ReviewRow

LOG = logging.getLogger(__name__)

DASHBOARD_FILENAME = "review-all.html"

_STYLE = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#1a1d21;--muted:#6b7280;--line:#e3e6ea;
--accent:#0b57d0;--warn:#b45309;--absent:#9ca3af}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
header{background:var(--card);border-bottom:1px solid var(--line);padding:16px 24px;
position:sticky;top:0;z-index:5}
h1{margin:0 0 4px;font-size:18px}
.sub{color:var(--muted);font-size:12px}
main{padding:24px;max-width:1400px;margin:0 auto}
h2{font-size:15px;margin:28px 0 10px}
table.summary{width:100%;border-collapse:collapse;background:var(--card);
border:1px solid var(--line);border-radius:8px;overflow:hidden}
table.summary th,table.summary td{padding:8px 10px;text-align:right;
border-bottom:1px solid var(--line);font-variant-numeric:tabular-nums}
table.summary th:first-child,table.summary td:first-child{text-align:left}
table.summary thead th{background:#eef1f5;font-weight:600;font-size:12px}
table.summary tr.total td{font-weight:700;background:#f0f4fa;border-bottom:none}
.controls{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:14px 0}
.controls input,.controls select{padding:7px 10px;border:1px solid var(--line);
border-radius:6px;font:inherit;background:var(--card)}
.controls input[type=search]{min-width:260px}
.chip{padding:6px 10px;border:1px solid var(--line);border-radius:999px;
background:var(--card);cursor:pointer;font-size:12px}
.chip.on{background:var(--accent);color:#fff;border-color:var(--accent)}
details.group{margin:12px 0;background:var(--card);border:1px solid var(--line);
border-radius:8px}
details.group>summary{padding:10px 14px;cursor:pointer;font-weight:600;
list-style:none;display:flex;justify-content:space-between;gap:12px}
details.group>summary::-webkit-details-marker{display:none}
details.group>summary .count{color:var(--muted);font-weight:400;font-size:12px}
.card{display:grid;grid-template-columns:216px 1fr;gap:16px;padding:14px;
border-top:1px solid var(--line)}
.thumb img{width:200px;border-radius:6px;cursor:zoom-in;display:block}
.placeholder{width:200px;height:140px;border:1px dashed var(--absent);
border-radius:6px;display:flex;align-items:center;justify-content:center;
color:var(--absent);font-size:12px;text-align:center;padding:8px}
.ref{font-weight:600;margin-bottom:2px;word-break:break-all}
.status{display:inline-block;padding:1px 8px;border-radius:999px;font-size:11px;
background:#eef1f5;color:var(--muted)}
.status.DESCRIBED_ABSENT{background:#f3f4f6;color:var(--absent)}
.group>summary .coverage{margin-left:auto;color:#6b7280;font-size:12px;font-weight:400}
.status.REVIEW,.status.SOURCE_CHANGED{background:#fef3c7;color:var(--warn)}
.status.APPROVED,.status.BUILT,.status.PUBLISHED{background:#dcfce7;color:#15803d}
.reason{color:var(--warn);font-size:12px;margin-top:4px}
.source{margin:8px 0;color:#374151}
.source .label{color:var(--muted);font-size:11px;text-transform:uppercase;
letter-spacing:.04em}
.fields{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));
gap:8px 14px;margin-top:10px}
.f .name{font-size:11px;color:var(--muted);text-transform:uppercase;
letter-spacing:.04em}
.f .final{font-weight:600}
.f .final.empty{color:var(--absent);font-weight:400}
.f .sugg{font-size:12px;color:var(--muted)}
.f .sugg::before{content:"suggested: "}
.links{margin-top:10px;display:flex;gap:8px;flex-wrap:wrap}
.links a{font-size:12px;padding:5px 10px;border-radius:6px;text-decoration:none;
border:1px solid var(--line);color:var(--accent);background:var(--card)}
.links a.primary{background:var(--accent);color:#fff;border-color:var(--accent)}
#lightbox{position:fixed;inset:0;background:rgba(0,0,0,.85);display:none;
align-items:center;justify-content:center;z-index:50;cursor:zoom-out}
#lightbox.on{display:flex}
#lightbox img{max-width:94vw;max-height:88vh;border-radius:4px}
#lightbox .cap{position:absolute;bottom:16px;color:#fff;font-size:13px}
.hidden{display:none !important}
"""

_SCRIPT = """
const q=s=>document.querySelector(s), qa=s=>[...document.querySelectorAll(s)];
const box=q('#lightbox'), boximg=q('#lightbox img'), boxcap=q('#lightbox .cap');
qa('.thumb img').forEach(i=>i.addEventListener('click',()=>{
  boximg.src=i.dataset.medium||i.src; boxcap.textContent=i.dataset.ref||'';
  box.classList.add('on');}));
box.addEventListener('click',()=>box.classList.remove('on'));
document.addEventListener('keydown',e=>{if(e.key==='Escape')box.classList.remove('on')});

const search=q('#search'), status=q('#status'), folder=q('#folder');
const chips=qa('.chip');
function apply(){
  const text=search.value.trim().toLowerCase();
  const st=status.value, fd=folder.value;
  const on=chips.filter(c=>c.classList.contains('on')).map(c=>c.dataset.flag);
  qa('.card').forEach(card=>{
    let show=true;
    if(text && !card.dataset.text.includes(text)) show=false;
    if(st && card.dataset.status!==st) show=false;
    if(fd && card.dataset.group!==fd) show=false;
    for(const flag of on){ if(card.dataset[flag]!=='1'){show=false;break;} }
    card.classList.toggle('hidden',!show);
  });
  qa('details.group').forEach(g=>{
    const total=g.querySelectorAll('.card').length;
    const shown=g.querySelectorAll('.card:not(.hidden)').length;
    g.classList.toggle('hidden',shown===0);
    g.querySelector('.count').textContent=
      shown===total?`${total} rows`:`${shown} of ${total} rows`;
    if(shown&&shown<total) g.open=true;
  });
}
[search,status,folder].forEach(el=>el.addEventListener('input',apply));
chips.forEach(c=>c.addEventListener('click',()=>{c.classList.toggle('on');apply();}));
q('#expand').addEventListener('click',()=>qa('details.group').forEach(g=>g.open=true));
q('#collapse').addEventListener('click',()=>qa('details.group').forEach(g=>g.open=false));
"""

#: ``(label, final attribute, suggested attribute or None)``.
_FIELDS: tuple[tuple[str, str, str | None], ...] = (
    ("Date", "date", "suggested_date"),
    ("Place", "place", "suggested_place"),
    ("LatLon", "latlon", "suggested_latlon"),
    ("People", "people", "suggested_people"),
    ("Tags", "tags", "suggested_tags"),
    ("Event", "event", None),
    ("Albums", "albums", None),
    ("Description", "description", None),
    ("Notes", "notes", None),
)

_QUICK_FILTERS: tuple[tuple[str, str], ...] = (
    ("needsreview", "Needs Review"),
    ("nopeople", "Missing People"),
    ("noplace", "Missing Place"),
    ("nolatlon", "Missing LatLon"),
    ("notags", "Missing Tags"),
    ("absent", "DESCRIBED_ABSENT"),
)


def _escape(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


def render_dashboard(
    aggregate: Aggregate,
    previews: PreviewProvider | None = None,
    generated_at: datetime | None = None,
) -> str:
    """Render the whole dashboard as one HTML document."""
    stamp = generated_at or datetime.now(tz=timezone.utc)
    parts = [
        "<!doctype html>",
        '<html lang="ru"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        "<title>Family Photo Archive — review</title>",
        f"<style>{_STYLE}</style></head><body>",
        "<header><h1>Family Photo Archive — review dashboard</h1>",
        f'<div class="sub">Generated {_escape(stamp.strftime("%Y-%m-%d %H:%M UTC"))} · '
        f"{aggregate.rows} rows · {aggregate.present_photos} photos · "
        f"{aggregate.absent_photos} described but absent · "
        "read-only view; edit metadata in the per-folder review.xlsx</div></header>",
        "<main>",
        _render_summary(aggregate),
        _render_controls(aggregate),
    ]
    for group in aggregate.groups:
        parts.append(_render_group(group, previews))
    parts.extend(
        [
            "</main>",
            '<div id="lightbox"><img alt=""><div class="cap"></div></div>',
            f"<script>{_SCRIPT}</script>",
            "</body></html>",
        ]
    )
    return "\n".join(parts)


def _render_summary(aggregate: Aggregate) -> str:
    head = (
        "<tr><th>Folder</th><th>Rows</th><th>Photos</th><th>Absent</th>"
        "<th>Date</th><th>People</th><th>Place</th><th>LatLon</th><th>Tags</th>"
        "<th>Needs review</th><th>Statuses</th></tr>"
    )
    body = []
    for group in aggregate.groups:
        statuses = ", ".join(
            f"{name} {count}" for name, count in group.status_counts.items()
        )
        body.append(
            f"<tr><td>{_escape(group.label)}</td>"
            f"<td>{len(group.rows)}</td><td>{group.present_photos}</td>"
            f"<td>{group.absent_photos}</td>"
            f"<td>{group.filled('date')}</td><td>{group.filled('people')}</td>"
            f"<td>{group.filled('place')}</td><td>{group.filled('latlon')}</td>"
            f"<td>{group.filled('tags')}</td>"
            f"<td>{group.needs_review}</td><td>{_escape(statuses)}</td></tr>"
        )

    statuses = ", ".join(f"{n} {c}" for n, c in aggregate.status_counts.items())
    body.append(
        f'<tr class="total"><td>TOTAL</td>'
        f"<td>{aggregate.rows}</td><td>{aggregate.present_photos}</td>"
        f"<td>{aggregate.absent_photos}</td>"
        f"<td>{aggregate.filled('date')}</td><td>{aggregate.filled('people')}</td>"
        f"<td>{aggregate.filled('place')}</td><td>{aggregate.filled('latlon')}</td>"
        f"<td>{aggregate.filled('tags')}</td>"
        f"<td>{aggregate.needs_review}</td><td>{_escape(statuses)}</td></tr>"
    )
    return (
        "<h2>Summary</h2><table class='summary'><thead>"
        + head
        + "</thead><tbody>"
        + "".join(body)
        + "</tbody></table>"
    )


def _render_controls(aggregate: Aggregate) -> str:
    statuses = sorted(aggregate.status_counts)
    status_options = "".join(
        f'<option value="{_escape(name)}">{_escape(name)}</option>' for name in statuses
    )
    folder_options = "".join(
        f'<option value="{_escape(group.label)}">{_escape(group.label)}</option>'
        for group in aggregate.groups
    )
    chips = "".join(
        f'<button class="chip" data-flag="{flag}">{_escape(label)}</button>'
        for flag, label in _QUICK_FILTERS
    )
    return (
        "<h2>Review</h2><div class='controls'>"
        '<input id="search" type="search" placeholder="Search text, names, places…">'
        f'<select id="status"><option value="">All statuses</option>{status_options}</select>'
        f'<select id="folder"><option value="">All folders</option>{folder_options}</select>'
        f"{chips}"
        '<button class="chip" id="expand">Expand all</button>'
        '<button class="chip" id="collapse">Collapse all</button>'
        "</div>"
    )


def _render_group(group: FolderGroup, previews: PreviewProvider | None) -> str:
    cards = "".join(_render_card(group, row, previews) for row in group.rows)
    return (
        f"<details class='group' open><summary><span>{_escape(group.label)}</span>"
        f"<span class='count'>{len(group.rows)} rows</span>"
        f"<span class='coverage'>{_escape(coverage_summary(group))}</span>"
        f"</summary>{cards}</details>"
    )


def coverage_summary(group: FolderGroup) -> str:
    """One line about what the source says, in the folder's own terms.

    Each folder state gets its own sentence rather than a number that would
    mean something different in each. In particular an unobserved folder is
    never reported as needing anything: that would turn a gap in our records
    into a claim about the source.
    """
    photos = group.present_photos
    if group.description_status is FolderDescriptionStatus.UNKNOWN:
        return f"{photos} photos · description coverage: not yet observed"
    if group.description_status is FolderDescriptionStatus.ABSENT:
        return (
            f"{photos} photos · description document: absent · "
            f"{photos} need manual description"
        )
    if group.description_status is FolderDescriptionStatus.AMBIGUOUS:
        return (
            f"{photos} photos · description document: ambiguous · "
            "coverage unresolved"
        )

    line = f"{photos} photos · {group.described} described · {group.needs_description} need description"
    breakdown = _coverage_breakdown(group)
    return f"{line} ({breakdown})" if breakdown else line


#: Reader-facing wording for the reasons a photo still needs a description.
_BREAKDOWN_LABELS: tuple[tuple[PhotoDescriptionCoverage, str], ...] = (
    (PhotoDescriptionCoverage.CONTEXT_ONLY, "context only"),
    (PhotoDescriptionCoverage.ENTRY_EMPTY, "empty entries"),
    (PhotoDescriptionCoverage.NO_ENTRY, "no entry"),
)


def _coverage_breakdown(group: FolderGroup) -> str:
    """Why those photos need a description — the detail behind the count."""
    counts = group.coverage_counts
    parts = [
        f"{counts[value]} {label}"
        for value, label in _BREAKDOWN_LABELS
        if counts.get(value)
    ]
    return " · ".join(parts)


def _render_card(
    group: FolderGroup, row: ReviewRow, previews: PreviewProvider | None
) -> str:
    absent = row.status is WorkflowStatus.DESCRIBED_ABSENT
    preview = (
        previews.render(group.root_identity, row.filename or row.reference)
        if previews and not absent
        else None
    )

    if preview is not None:
        medium = f' data-medium="{preview.medium}"' if preview.has_medium else ""
        image = (
            f'<div class="thumb"><img src="{preview.thumbnail}"'
            f'{medium} data-ref="{_escape(row.filename or row.reference)}"'
            f' alt="{_escape(row.filename or row.reference)}" loading="lazy"></div>'
        )
    else:
        note = "described, no photo here" if absent else "no cached preview"
        image = f'<div class="thumb"><div class="placeholder">{_escape(note)}</div></div>'

    destinations = PhotoDestinations(
        yandex_url=group.source_url, yandex_is_folder=True
    )
    links = "".join(
        f'<a href="{_escape(link.url)}" target="_blank" rel="noopener"'
        f'{" class=primary" if link.primary else ""}>{_escape(link.label)}</a>'
        for link in links_for(destinations)
    )

    searchable = " ".join(
        str(value or "").lower()
        for value in (
            row.reference, row.filename, row.source_description, row.section_context,
            row.source_notes, row.date, row.place, row.people, row.tags, row.event,
            row.albums, row.description, row.notes, row.suggested_place,
        )
    )
    flags = {
        "needsreview": needs_review(row),
        "nopeople": not row.people.strip(),
        "noplace": not row.place.strip(),
        "nolatlon": not row.latlon.strip(),
        "notags": not row.tags.strip(),
        "absent": absent,
    }
    data_flags = "".join(
        f' data-{name}="{"1" if value else "0"}"' for name, value in flags.items()
    )

    return (
        f'<div class="card" data-group="{_escape(group.label)}"'
        f' data-status="{_escape(row.status.value)}"'
        f' data-text="{_escape(searchable)}"{data_flags}>'
        f"{image}<div>"
        f'<div class="ref">{_escape(row.filename or row.reference)} '
        f'<span class="status {_escape(row.status.value)}">{_escape(row.status.value)}</span></div>'
        + (f'<div class="reason">{_escape(row.review_reason)}</div>' if row.review_reason else "")
        + _render_source(row)
        + _render_fields(row)
        + (f'<div class="links">{links}</div>' if links else "")
        + "</div></div>"
    )


def _render_source(row: ReviewRow) -> str:
    blocks = []
    if row.source_description:
        blocks.append(
            f'<div class="source"><div class="label">Source description</div>'
            f"{_escape(row.source_description)}</div>"
        )
    if row.section_context:
        blocks.append(
            f'<div class="source"><div class="label">Section context</div>'
            f"{_escape(row.section_context)}</div>"
        )
    if row.source_notes:
        blocks.append(
            f'<div class="source"><div class="label">Source notes</div>'
            f"{_escape(row.source_notes)}</div>"
        )
    return "".join(blocks)


def _render_fields(row: ReviewRow) -> str:
    cells = []
    for label, final_attribute, suggested_attribute in _FIELDS:
        final = (getattr(row, final_attribute) or "").strip()
        suggested = (
            (getattr(row, suggested_attribute) or "").strip()
            if suggested_attribute
            else ""
        )
        if not final and not suggested:
            continue

        final_html = _coordinate(final) if final_attribute == "latlon" and final else _escape(final)
        value = (
            f'<div class="final">{final_html}</div>'
            if final
            else '<div class="final empty">—</div>'
        )
        # A suggestion is shown only when it adds something: either the final is
        # empty, or the canonical reading differs from what the reviewer typed.
        extra = ""
        if suggested and suggested != final:
            rendered = (
                _coordinate(suggested) if final_attribute == "latlon" else _escape(suggested)
            )
            extra = f'<div class="sugg">{rendered}</div>'

        cells.append(
            f'<div class="f"><div class="name">{_escape(label)}</div>{value}{extra}</div>'
        )
    return f'<div class="fields">{"".join(cells)}</div>' if cells else ""


def _coordinate(value: str) -> str:
    """Render coordinates as a Google Maps link, or plain text if unparsable."""
    point = parse_latlon(value)
    if point is None:
        return _escape(value)
    return (
        f'<a href="{_escape(point.map_url)}" target="_blank" rel="noopener">'
        f"{_escape(point.format())}</a>"
    )


def write_dashboard(
    aggregate: Aggregate,
    output_path: Path | str,
    previews: PreviewProvider | None = None,
    generated_at: datetime | None = None,
) -> Path:
    """Render and save the dashboard. Touches nothing else on disk."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        render_dashboard(aggregate, previews, generated_at), encoding="utf-8"
    )
    return path
