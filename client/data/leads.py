"""
data/leads.py

SEARCHES: the list of saved searches shown as Search Query cards on the
Search Leads page. Starts empty -- the app now boots with no pre-loaded
demo searches. Real entries get appended here as searches complete once
a backend is wired up (see SearchLeadsPage / ScraperWorker).

generate_dummy_businesses is the placeholder-data generator used by
"New Search" for brand-new queries typed in by non-admin users. It's
kept as-is -- it's the mock backend the "New Search" flow depends on to
show something, not leftover demo content -- swap it out for a real
search call whenever the New Search flow gets wired up to a real API.

Moved out of the old flat data.py as part of splitting it into a data/
package (see PROGRESS.md, Phase 2b). See data/accounts.py's docstring
(Phase 2a) for why this directory has no __init__.py yet -- `import data`
still resolves to the original data.py unchanged until Phase 2f's cutover.
"""

import random

# No pre-loaded searches -- Search Leads starts empty until a real search
# is run (admin "Search" button) or a placeholder one is created via
# "New Search" (non-admin). Each entry, once added, should look like:
#   {
#       "id": "...", "title": "...", "header": "...", "timestamp": "...",
#       "data": [...business dicts...],
#       "activity_log": [{"text": "...", "meta": "...", "time": "..."}],
#   }
SEARCHES = []


def generate_dummy_businesses(query_text: str, count: int) -> list:
    """
    Prototype-only data generator: builds `count` placeholder business
    dicts so a brand-new search (typed into "New Search") has something to
    show in the table. None of this is real -- swap this out for an actual
    backend/API call once one exists.

    ~15% chance each of phone/email being missing, so freshly-generated
    searches also exercise the free / 1-credit / 2-credit unlock tiers.
    """
    categories = ["Local Business", "Service Provider", "Retail Shop", "Restaurant"]
    statuses = ["Open", "Closed"]
    base_name = query_text.strip().title() or "Business"
    businesses = []
    for i in range(1, count + 1):
        has_phone = random.random() > 0.15
        has_email = random.random() > 0.15
        businesses.append({
            "name": f"{base_name} #{i}",
            "rating": round(random.uniform(3.5, 5.0), 1),
            "reviews": random.randint(10, 999),
            "category": random.choice(categories),
            "status": random.choice(statuses),
            "desc": f"Placeholder listing #{i} generated for the '{query_text}' search. "
                    "Replace with real data once a backend is wired up.",
            "address": "Address not available (placeholder)",
            "maps_url": f"https://maps.google.com/?q={base_name.replace(' ', '+')}+{i}",
            "hours": "Hours not available",
            "phone_num": (
                f"(0{random.randint(2, 9)}) {random.randint(100, 999)} {random.randint(1000, 9999)}"
                if has_phone else ""
            ),
            "site": f"example{i}.ph",
            "email_addr": f"contact{i}@example.ph" if has_email else "",
        })
    return businesses