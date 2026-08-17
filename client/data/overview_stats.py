"""
data/overview_stats.py

Dashboard/Overview tab mock data -- purely for prototype display on the
Overview page (see overview.py). Swap for real aggregate/analytics
queries once there's a backend.

Moved out of the old flat data.py as part of splitting it into a data/
package (see PROGRESS.md, Phase 2c). See data/accounts.py's docstring
(Phase 2a) for why this directory has no __init__.py yet -- `import data`
still resolves to the original data.py unchanged until Phase 2f's cutover.

Note (correction, added during Phase 2f pre-flight): RECENT_EXPORTS was
originally assumed to be exports-domain by name and left out of this file
in 2c/2e (see those notes in PROGRESS.md for the earlier, incorrect
reasoning). Checking real call sites before the 2f cutover showed
overview.py imports RECENT_EXPORTS in the same `from data import (...)`
statement as DASHBOARD_STATS/LEADS_OVER_TIME/etc, not from exports.py --
it's the "Recent Exports" activity card on the Overview dashboard, not
export-history data. It's included below, where it actually belongs.
"""

DASHBOARD_STATS = [
    {"icon": "fa5s.users", "bg": "rgba(239,68,68,0.15)", "color": "COLOR_RED",
     "label": "Total Leads Found", "value": "10,289", "delta": "+12.5%"},
    {"icon": "fa5s.star", "bg": "rgba(250,204,21,0.15)", "color": "COLOR_YELLOW",
     "label": "Average Rating", "value": "4.32", "delta": "+0.18"},
    {"icon": "fa5s.lock", "bg": "rgba(34,197,94,0.15)", "color": "COLOR_GREEN",
     "label": "Unlocked Contacts", "value": "238", "delta": "+8.4%"},
    {"icon": "fa5s.file-alt", "bg": "rgba(96,165,250,0.15)", "color": "COLOR_BLUE",
     "label": "Exports", "value": "17", "delta": "+21.4%"},
    {"icon": "fa5s.star", "bg": "rgba(168,85,247,0.15)", "color": "COLOR_PURPLE",
     "label": "Total Reviews", "value": "3,421", "delta": "+15.7%"},
]

LEADS_OVER_TIME = [
    ("Jul 22", 980), ("Jul 23", 1350), ("Jul 24", 1780), ("Jul 25", 700),
    ("Jul 26", 1500), ("Jul 27", 1820), ("Jul 28", 1150), ("Jul 29", 680),
]

LEADS_BY_CATEGORY = [
    {"label": "Tourist Attraction", "value": 6213, "pct": 60, "color": "COLOR_RED_SOLID"},
    {"label": "Restaurant", "value": 2184, "pct": 21, "color": "COLOR_BLUE"},
    {"label": "Cafe", "value": 1029, "pct": 10, "color": "COLOR_GREEN"},
    {"label": "Other", "value": 863, "pct": 9, "color": "COLOR_YELLOW"},
]

TOP_RATED = [
    {"name": "Burnham Park", "rating": 4.5, "reviews": 1280, "status": "Open"},
    {"name": "Lion's Head", "rating": 4.5, "reviews": 940, "status": "Open"},
    {"name": "Heritage Hill and Nature Park", "rating": 4.4, "reviews": 512, "status": "Open"},
    {"name": "Valley of Colors", "rating": 4.4, "reviews": 803, "status": "Open"},
    {"name": "Mines View Observation Deck", "rating": 4.3, "reviews": 655, "status": "Open"},
]

LEAD_SOURCES = [
    {"label": "Google Maps", "value": 7542, "pct": 73, "color": "COLOR_RED_SOLID"},
    {"label": "Facebook", "value": 1423, "pct": 14, "color": "COLOR_BLUE"},
    {"label": "Other Directories", "value": 824, "pct": 8, "color": "COLOR_GREEN"},
    {"label": "Manual Entry", "value": 500, "pct": 5, "color": "COLOR_YELLOW"},
]

RECENT_EXPORTS = [
    {"name": "restaurants_baguio_jul29.csv", "meta": "Jul 29, 2026 10:42 AM", "count": "1,250 leads"},
    {"name": "tourist_attractions_baguio.csv", "meta": "Jul 28, 2026 03:15 PM", "count": "850 leads"},
    {"name": "cafes_baguio_jul28.csv", "meta": "Jul 28, 2026 11:02 AM", "count": "620 leads"},
    {"name": "baguio_leads_jul27.csv", "meta": "Jul 27, 2026 04:30 PM", "count": "980 leads"},
]

LEADS_BY_RATING = [
    ("5", 2154), ("4", 4125), ("3", 2312), ("2", 1025), ("1", 673),
]

RECENT_ACTIVITY = [
    {"icon": "fa5s.check-circle", "color": "COLOR_GREEN", "text": "Search completed successfully",
     "meta": "restaurants in Baguio", "time": "10:42 AM"},
    {"icon": "fa5s.envelope", "color": "COLOR_BLUE", "text": "Email enrichment completed",
     "meta": "restaurants_baguio_jul29.csv", "time": "10:35 AM"},
    {"icon": "fa5s.unlock", "color": "COLOR_GREEN", "text": "Contact unlocked",
     "meta": "Burnham Park", "time": "10:28 AM"},
    {"icon": "fa5s.download", "color": "COLOR_GREEN", "text": "Export completed",
     "meta": "tourist_attractions_baguio.csv", "time": "10:15 AM"},
    {"icon": "fa5s.bookmark", "color": "COLOR_RED", "text": "Search saved",
     "meta": "dental clinic in Laguna", "time": "09:58 AM"},
]

SEARCH_PERFORMANCE = [
    {"label": "Searches Run", "value": "24", "delta": "+9 vs last 7 days"},
    {"label": "Success Rate", "value": "96%", "delta": "+2% vs last 7 days"},
    {"label": "Avg. Leads per Search", "value": "428", "delta": "+38 vs last 7 days"},
    {"label": "Time Saved", "value": "8.6 hrs", "delta": "+1.2 hrs vs last 7 days"},
]
