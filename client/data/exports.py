"""
data/exports.py

FILE_FORMATS (the reference card of supported output formats) and
EXPORT_TIPS (the tips shown alongside it) for the "Exports" nav tab.

The old static EXPORTS list that used to live here is gone -- the
Exports tab is now backed by a real database table (server/app/db.py's
Export model, behind GET/POST/PATCH/DELETE /exports) instead of an
in-memory prototype list. ui/pages/exports.py loads a user's actual
export history via core.api_client.list_exports() in
ExportsPage.__init__ now, the same way search_leads.py loads real
Search/Lead rows via list_searches()/get_search_leads(). See that
page's module docstring for the current (non-prototype) behavior.

Moved out of the old flat data.py as part of splitting it into a data/
package (see PROGRESS.md, Phase 2e). See data/accounts.py's docstring
(Phase 2a) for why this directory has no __init__.py yet -- `import data`
still resolves to the original data.py unchanged until Phase 2f's cutover.

Note: RECENT_EXPORTS (a *different*, shorter list -- the "Recent Exports"
card on the Overview dashboard) is NOT in this file, despite the similar
name. It's overview-domain data (overview.py imports it alongside
DASHBOARD_STATS etc., not from here) -- it lives in
data/overview_stats.py instead. See that file's docstring for the
correction; this was originally mislabeled as exports-domain in 2c/2e
before checking real call sites during the 2f pre-flight.
"""

FILE_FORMATS = [
    {"icon": "fa5s.file-csv", "color": "COLOR_GREEN",
     "label": "CSV (Comma Separated Values)",
     "desc": "Best for data analysis and import to databases."},
    {"icon": "fa5s.file-excel", "color": "COLOR_GREEN",
     "label": "Excel (XLSX)",
     "desc": "Best for Microsoft Excel users."},
    {"icon": "fa5s.file-code", "color": "COLOR_BLUE",
     "label": "JSON (JavaScript Object Notation)",
     "desc": "Best for developers and API integration."},
    {"icon": "fa5s.globe", "color": "COLOR_PURPLE",
     "label": "HTML (HyperText Markup Language)",
     "desc": "Best for viewing in a browser or sharing as a report."},
]

EXPORT_TIPS = [
    "Exports write straight to the folder you choose -- no waiting.",
    "Your export history and leads are saved to your account, not just this device.",
    "\"Download\" regenerates the file from your account, so it works on any device you're logged into.",
    "Pick any of CSV, Excel, JSON, or HTML when you download -- not just the original format.",
]

# Content for the "View our guide" popup on the Exports tab (see
# ui/pages/exports.py's _open_guide, which feeds this straight into
# ui.dialogs.dialogs.GuideDialog). Kept here next to FILE_FORMATS/
# EXPORT_TIPS since it documents the same tab and should stay in sync
# with it -- if the behavior these sections describe changes, update
# it here too.
EXPORT_GUIDE_SECTIONS = [
    ("Export statuses", "Completed means the file is ready to download. "
     "Processing means it's still being generated. Failed means it "
     "didn't finish -- see \"Failed exports\" below for what to do next."),

    ("Downloading a file", "The download button next to each row "
     "regenerates the file from the leads saved to your account, not "
     "from a cached copy on this device -- so it works even after a "
     "reinstall or on another device you're logged into. You can pick "
     "any of CSV, Excel, JSON, or HTML in the save dialog, regardless "
     "of which format the export originally used."),

    ("Renaming an export", "Open the \u22ee menu on a row and choose "
     "Rename to change the file name shown in your export history. If "
     "the original file is still on disk at the location it was saved "
     "to, it's renamed there too."),

    ("Deleting an export", "Open the \u22ee menu on a row and choose "
     "Delete Export to remove it from your export history. This "
     "doesn't delete any copy of the file already saved to your "
     "computer."),

    ("Failed exports", "A failed export can't be regenerated "
     "automatically -- SinuLead keeps a record of the export, not the "
     "original leads that went into it. Head back to the Search Leads "
     "tab and export that search again to create a new file."),

    ("Searching and filtering", "Use the All/Completed/Processing/"
     "Failed tabs, the search box (matches file name or source), and "
     "the source dropdown to narrow the list. Advanced filters like "
     "date range aren't available yet."),

    ("Choosing a file format", "CSV suits data analysis and importing "
     "into databases. Excel (XLSX) suits Microsoft Excel users. JSON "
     "suits developers and API integration. HTML suits viewing in a "
     "browser or sharing as a report."),
]
