"""
data/lead_exporter.py

Format-agnostic export helper for the Search Leads "Export" button.
Takes the same (fieldnames, rows) shape the old CSV-only export used and
writes it out as CSV, Excel (.xlsx), JSON, or HTML -- one function per
format, plus a single export_leads() dispatcher so callers don't need to
know which writer to call.

Only stdlib + openpyxl are used (openpyxl is required for the Excel
writer -- add `openpyxl` to requirements.txt if it isn't there yet).

Usage from ui/pages/search_leads.py:

    from data.lead_exporter import export_leads, EXPORT_FORMATS

    export_leads(to_export, fieldnames, headers, path, fmt)

Where:
    to_export  -- list[dict], the business rows to write
    fieldnames -- list[str], dict keys to pull from each row, in order
    headers    -- list[str], display column labels, same order/length
                  as fieldnames (used for the header row / <th> text)
    path       -- str, destination file path (any extension -- the
                  chosen `fmt` decides the actual file contents)
    fmt        -- one of "csv", "xlsx", "json", "html" (see EXPORT_FORMATS)
"""

import csv
import html as html_lib
import json


# Central registry of supported export formats: dialog filter string +
# default extension, so both the file-picker and the "did they type a
# different extension than the filter they picked" fallback in
# search_leads.py can be driven off one list instead of duplicating it.
EXPORT_FORMATS = [
    {"id": "csv", "label": "CSV Files (*.csv)", "ext": ".csv"},
    {"id": "xlsx", "label": "Excel Files (*.xlsx)", "ext": ".xlsx"},
    {"id": "json", "label": "JSON Files (*.json)", "ext": ".json"},
    {"id": "html", "label": "HTML Files (*.html)", "ext": ".html"},
]

# "CSV Files (*.csv);;Excel Files (*.xlsx);;JSON Files (*.json);;HTML Files (*.html)"
QT_FILE_DIALOG_FILTER = ";;".join(f["label"] for f in EXPORT_FORMATS)


def _rows_as_dicts(rows: list, fieldnames: list) -> list:
    """Every row, reduced down to just the exported fields, in order,
    string-coerced (None -> "")."""
    return [{k: ("" if row.get(k) is None else row.get(k, "")) for k in fieldnames} for row in rows]


def export_csv(rows: list, fieldnames: list, headers: list, path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        for row in rows:
            writer.writerow([row.get(k, "") for k in fieldnames])


def export_json(rows: list, fieldnames: list, headers: list, path: str) -> None:
    data = _rows_as_dicts(rows, fieldnames)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def export_xlsx(rows: list, fieldnames: list, headers: list, path: str) -> None:
    # Imported lazily so the whole module (and everything importing it,
    # e.g. search_leads.py) doesn't hard-fail at import time on machines
    # where openpyxl hasn't been installed yet -- only Excel exports need it.
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError as e:
        raise RuntimeError(
            "Excel export requires the 'openpyxl' package. "
            "Install it with: pip install openpyxl"
        ) from e

    wb = Workbook()
    ws = wb.active
    ws.title = "Leads"

    header_fill = PatternFill(start_color="FFDC2626", end_color="FFDC2626", fill_type="solid")
    header_font = Font(color="FFFFFFFF", bold=True)

    for col_idx, label in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col_idx, value=label)
        cell.font = header_font
        cell.fill = header_fill
    ws.freeze_panes = "A2"

    for row_idx, biz in enumerate(rows, start=2):
        for col_idx, key in enumerate(fieldnames, start=1):
            value = biz.get(key, "")
            ws.cell(row=row_idx, column=col_idx, value=value if value is not None else "")

    # Rough auto-width: widest of header label or any cell in that column,
    # capped so one long address/email doesn't blow the sheet out sideways.
    for col_idx, key in enumerate(fieldnames, start=1):
        longest = len(str(headers[col_idx - 1]))
        for biz in rows:
            longest = max(longest, len(str(biz.get(key, ""))))
        ws.column_dimensions[get_column_letter(col_idx)].width = min(max(longest + 2, 10), 50)

    wb.save(path)


def export_html(rows: list, fieldnames: list, headers: list, path: str, title: str = "Leads Export") -> None:
    """A single self-contained HTML file (inline CSS, no external
    assets) with a simple styled table -- opens fine in any browser or
    can be pasted into an email/report."""
    esc = html_lib.escape

    head_cells = "".join(f"<th>{esc(str(h))}</th>" for h in headers)
    body_rows = []
    for biz in rows:
        cells = "".join(f"<td>{esc(str(biz.get(k, '')))}</td>" for k in fieldnames)
        body_rows.append(f"<tr>{cells}</tr>")

    doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{esc(title)}</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, Roboto, Arial, sans-serif;
          background: #0f1115; color: #e6e6e6; padding: 24px; }}
  h1 {{ font-size: 18px; margin: 0 0 4px; }}
  .meta {{ color: #9aa0a6; font-size: 12px; margin-bottom: 16px; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
  th, td {{ text-align: left; padding: 8px 10px; border-bottom: 1px solid #2a2d34; }}
  th {{ background: #dc2626; color: #ffffff; position: sticky; top: 0; }}
  tr:nth-child(even) td {{ background: #171a21; }}
  tr:hover td {{ background: #20242c; }}
</style>
</head>
<body>
  <h1>{esc(title)}</h1>
  <div class="meta">{len(rows)} lead{'s' if len(rows) != 1 else ''} exported</div>
  <table>
    <thead><tr>{head_cells}</tr></thead>
    <tbody>
      {''.join(body_rows)}
    </tbody>
  </table>
</body>
</html>
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(doc)


_WRITERS = {
    "csv": export_csv,
    "xlsx": export_xlsx,
    "json": export_json,
    "html": export_html,
}


def export_leads(rows: list, fieldnames: list, headers: list, path: str, fmt: str) -> None:
    """Dispatches to the right writer for `fmt` (one of EXPORT_FORMATS'
    "id" values: csv / xlsx / json / html). Raises ValueError for an
    unknown format so callers get a clear error instead of a silent no-op."""
    writer = _WRITERS.get(fmt)
    if writer is None:
        raise ValueError(f"Unsupported export format: {fmt!r} (expected one of {list(_WRITERS)})")
    writer(rows, fieldnames, headers, path)


def format_id_from_filter(selected_filter: str) -> str:
    """Maps a QFileDialog selectedFilter() string back to an
    EXPORT_FORMATS "id" (e.g. "Excel Files (*.xlsx)" -> "xlsx").
    Falls back to "csv" if nothing matches."""
    for fmt in EXPORT_FORMATS:
        if fmt["label"] == selected_filter:
            return fmt["id"]
    return "csv"


def ext_for_format(fmt: str) -> str:
    for f in EXPORT_FORMATS:
        if f["id"] == fmt:
            return f["ext"]
    return ".csv"