"""
core/scraper_engine.py

Real Google-Maps lead scraping engine, wired into the "New Search" flow on
the Search Leads page. Runs over Playwright connected to a real Chrome via
CDP (no headless browser download needed) and drives it from a QThread so
the GUI never blocks.

Multiple queries (e.g. "dentist in Laguna" + "dentist in Taguig") run as
CONCURRENT tabs (bounded by max_concurrent_tabs), not one after another --
see run_all_queries(). Every query's results are merged and de-duplicated
into a single dataset, which is what makes a multi-query search "compile"
into one combined result set / one downloadable file.

ScraperWorker itself still has no concept of credits or a token budget --
every lead it finds is free to view once Chrome is already running. Cost
enforcement happens one layer up: SearchLeadsPage._start_real_search()
calls the server's /searches/start first (skipped only for admin
accounts) to check the account can afford the current search price and
create the Search row -- but that call no longer deducts credits itself.
The actual charge happens after the scrape finishes, when results are
POSTed back to /searches/{id}/results, and only if at least one lead
came back: a search that finds nothing is free.
"""

import asyncio
import os
import platform
import re
import shutil
import socket
import subprocess
import tempfile
import time
from urllib.parse import quote

import phonenumbers
import psutil
from PySide6.QtCore import QThread, Signal

from playwright.async_api import async_playwright

# ---------------------------------------------------------------------------
# Google Maps selectors (local-pack result cards)
# ---------------------------------------------------------------------------

CARD_LINK_SELECTOR = "a.hfpxzc"
FEED_SELECTOR = "div[role='feed']"

SCRAPE_COLUMNS = [
    "name", "rating", "reviews", "category", "address", "phone", "status",
    "hours", "website", "email", "maps_url",
]

EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9.-]+")
EMAIL_NOISE_DOMAINS = (
    "wixpress.com", "sentry.io", "schema.org", "example.com", "godaddy.com",
    "gstatic.com", "googleapis.com", "cloudflare.com", "w3.org", "png", "jpg",
    "jpeg", "gif", "svg", "webp",
)
CONTACT_LINK_TEXT_CANDIDATES = ["Contact", "Contact Us", "Get in Touch", "About"]
FACEBOOK_DOMAINS = ("facebook.com", "fb.com", "m.facebook.com")


def _is_noise_email(email: str) -> bool:
    lowered = email.lower()
    return any(noise in lowered for noise in EMAIL_NOISE_DOMAINS)


def _is_facebook_url(url: str) -> bool:
    return any(d in url.lower() for d in FACEBOOK_DOMAINS)


# ---------------------------------------------------------------------------
# Chrome / environment auto-detection
# ---------------------------------------------------------------------------

def detect_chrome_executable():
    system = platform.system()

    if system == "Windows":
        candidates = [
            os.path.join(os.environ.get("PROGRAMFILES", r"C:\Program Files"),
                          "Google", "Chrome", "Application", "chrome.exe"),
            os.path.join(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
                          "Google", "Chrome", "Application", "chrome.exe"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""),
                          "Google", "Chrome", "Application", "chrome.exe"),
        ]
        for path in candidates:
            if path and os.path.isfile(path):
                return path
        found = shutil.which("chrome.exe") or shutil.which("chrome")
        return found or candidates[0]

    if system == "Darwin":
        candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            os.path.expanduser("~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        ]
        for path in candidates:
            if os.path.isfile(path):
                return path
        found = shutil.which("google-chrome") or shutil.which("chromium")
        return found or candidates[0]

    for name in ("google-chrome", "google-chrome-stable", "chromium-browser", "chromium"):
        found = shutil.which(name)
        if found:
            return found
    return "google-chrome"


def detect_default_profile_dir():
    return os.path.join(tempfile.gettempdir(), "sinulead_chrome_profile")


def find_free_port(preferred=9222, attempts=50):
    for port in range(preferred, preferred + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.2)
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return preferred


# ---------------------------------------------------------------------------
# Card parsing (identical logic to the standalone engine this was lifted
# from -- structured DOM parse, not raw text scraping)
# ---------------------------------------------------------------------------

async def parse_card(card, default_region="PH"):
    data = {
        "name": "", "rating": "", "reviews": "", "category": "",
        "address": "", "phone": "", "status": "", "hours": "",
        "website": "", "email": "", "maps_url": "",
    }

    name_el = await card.query_selector(".qBF1Pd, .fontHeadlineSmall")
    if name_el:
        data["name"] = (await name_el.inner_text()).strip()

    link_el = await card.query_selector(CARD_LINK_SELECTOR)
    if link_el:
        href = await link_el.get_attribute("href")
        if href:
            data["maps_url"] = href
    if not data["maps_url"]:
        # The result card's link anchor isn't reliably a *descendant* of
        # the card element -- on the current Google Maps markup it's the
        # ancestor that wraps the whole card, so the query_selector above
        # (which only looks downward) was silently finding nothing on
        # every single row. Walk both directions and match on the URL
        # shape itself ("/maps/place/...") instead of a class name, since
        # Google rotates those obfuscated class names often and a shape
        # match survives that churn.
        try:
            href = await card.evaluate(
                """el => {
                    const a = el.closest('a[href*="/maps/place/"]')
                        || el.querySelector('a[href*="/maps/place/"]')
                        || el.closest('a[href]')
                        || el.querySelector('a[href]');
                    return a ? a.href : null;
                }"""
            )
        except Exception:
            href = None
        if href:
            data["maps_url"] = href

    website_el = (
        await card.query_selector('a[data-value="Website"]')
        or await card.query_selector('a[aria-label^="Website:"]')
        or await card.query_selector('a[data-item-id="authority"]')
    )
    if website_el:
        website_href = await website_el.get_attribute("href")
        if website_href:
            data["website"] = website_href

    rating_el = await card.query_selector("span.MW4etd")
    reviews_el = await card.query_selector("span.UY7F9")
    if rating_el:
        data["rating"] = (await rating_el.inner_text()).strip()
    if reviews_el:
        data["reviews"] = re.sub(r"[()]", "", (await reviews_el.inner_text())).strip()

    info_rows = await card.query_selector_all("div.W4Efsd")
    # Google renders a small "open/directions" icon-font glyph directly
    # inside some info rows (mostly the Plus-Code-style address rows).
    # inner_text() scoops that glyph up as real text (e.g. "↗ 179 Manila
    # S Rd"), so it was riding along into the address field. Strip any
    # leading icon/arrow/pictograph characters -- arrows, dingbats, misc
    # symbols, and the private-use-area codepoints most icon fonts use --
    # off each row before it goes anywhere near the parsing below.
    icon_glyph_pattern = re.compile(
        r"^[\u2190-\u21FF\u2300-\u27BF\u2B00-\u2BFF\uE000-\uF8FF]+\s*"
    )
    seen_text = set()
    full_text_parts = []
    for row in info_rows:
        t = (await row.inner_text()).strip()
        t = icon_glyph_pattern.sub("", t).strip()
        if t and t not in seen_text:
            seen_text.add(t)
            full_text_parts.append(t)
    combined = " · ".join(full_text_parts)
    combined = re.sub(r"\s*·\s*", " · ", combined)

    # Google glues status/phone/address together in a single info row using
    # a few different separator glyphs (middle dot, dot operator, bullet) --
    # splitting on "·" alone left rows like "Open ⋅ (0951) 199 5275 ⋅ 31
    # Manila South Road" as one segment, so the "Open" and the phone's
    # remainder both got dumped into the address field, duplicating it.
    raw_segments = [s.strip() for s in re.split(r"[·⋅•\n]", combined) if s.strip()]
    rating_review_pattern = re.compile(r"^(\d\.\d)\s*(?:\((\d+)\))?$|^No reviews$", re.IGNORECASE)

    # Fallback for rating/reviews: span.MW4etd / span.UY7F9 are Google's
    # current obfuscated class names for the rating badge and review-count
    # badge -- they've drifted before and will again, and when UY7F9 (or
    # occasionally MW4etd) stops matching, data["rating"]/data["reviews"]
    # come out empty even though the same numbers are sitting in plain
    # text on one of the W4Efsd info rows just parsed above (e.g.
    # "4.5 (23)"). Recover them from there instead of leaving reviews
    # silently stuck at 0.
    if not data["rating"] or not data["reviews"]:
        for seg in raw_segments:
            m = rating_review_pattern.match(seg)
            if not m:
                continue
            if not data["rating"] and m.group(1):
                data["rating"] = m.group(1)
            if not data["reviews"] and m.group(2):
                data["reviews"] = m.group(2)
            break
    # Covers "Open", "Closed", "Open now", "Opens 9 AM", "Closes 10:30 PM",
    # "Open 24 hours" -- without the trailing bit, leftovers like a bare
    # "now" or "9 AM" used to fall through and get glued onto the address.
    status_prefix_pattern = re.compile(
        r"^(Open|Closed|Opens|Closes)\b"
        r"(?:\s+(?:now|24\s*hours|\d{1,2}(?::\d{2})?\s*[AP]M))?"
        r"[:,\-]?\s*",
        re.IGNORECASE,
    )
    # phonenumbers' matcher misses plenty of real Google-Maps-formatted PH
    # numbers (e.g. "0928 688 1776", grouped 4-3-4 with no parens) since its
    # default leniency wants a stricter shape. This permissive fallback
    # just looks for a run of digits/spaces/dashes/parens that starts with
    # a 0 and totals a plausible PH digit count, so those don't end up
    # stuck inside the address instead.
    ph_phone_fallback_pattern = re.compile(r"\(?0\d[\d\s\-()]{5,}\d")
    lone_digit_pattern = re.compile(r"^\d{1,2}$")

    segments = []
    seen_seg = set()
    for seg in raw_segments:
        if rating_review_pattern.match(seg):
            continue
        if data["rating"] and seg == data["rating"]:
            continue
        if seg in seen_seg:
            continue
        seen_seg.add(seg)
        segments.append(seg)

    address_parts = []
    for seg in segments:
        status_match = status_prefix_pattern.match(seg)
        if status_match:
            data["status"] = "Open" if seg.lower().startswith("open") else status_match.group(1)
            # Strip the status word (and any "now"/time that rode along
            # with it) so it doesn't get treated as address text below.
            seg = seg[status_match.end():].strip(" ·")
            if not seg:
                continue

        phone_match = next(iter(phonenumbers.PhoneNumberMatcher(seg, default_region)), None)
        fallback_match = None if phone_match else ph_phone_fallback_pattern.search(seg)
        if phone_match:
            data["phone"] = phonenumbers.format_number(
                phone_match.number, phonenumbers.PhoneNumberFormat.NATIONAL
            )
            remainder = (seg[:phone_match.start] + seg[phone_match.end:]).strip(" ·")
            if remainder:
                address_parts.append(remainder)
        elif fallback_match and 6 <= len(re.sub(r"\D", "", fallback_match.group())) <= 11:
            if not data["phone"]:
                data["phone"] = fallback_match.group().strip()
            remainder = (seg[:fallback_match.start()] + seg[fallback_match.end():]).strip(" ·")
            if remainder:
                address_parts.append(remainder)
        elif re.search(r"\d{1,2}(:\d{2})?\s*[AP]M\b", seg, re.IGNORECASE):
            data["hours"] = seg
        elif not data["category"] and len(seg) < 40 and "," not in seg and not re.search(r"\d{3,}", seg):
            data["category"] = seg
        elif lone_digit_pattern.match(seg):
            # Stray 1-2 digit fragment (badge/photo-count leakage etc.) --
            # never a real standalone address component, so drop it rather
            # than let it show up as garbage in the address column.
            continue
        else:
            address_parts.append(seg)

    # Belt-and-braces de-dup: keep exact-order-of-first-appearance parts,
    # but also collapse near-duplicates that only differ by a leftover
    # status word/whitespace/punctuation (case-insensitive).
    deduped_parts = []
    seen_norm = set()
    for part in address_parts:
        norm = re.sub(r"\s+", " ", status_prefix_pattern.sub("", part)).strip(" ,.").lower()
        if norm and norm in seen_norm:
            continue
        if norm:
            seen_norm.add(norm)
        deduped_parts.append(part)

    address = " ".join(deduped_parts)
    address = icon_glyph_pattern.sub("", address).strip()
    data["address"] = address

    if not data["maps_url"] and data["name"]:
        # Last resort so the column isn't just empty when even the
        # href-shape match above comes up dry -- a plain Maps search
        # link still gets the user to the right listing.
        query_bits = " ".join(b for b in (data["name"], data["address"]) if b)
        data["maps_url"] = "https://www.google.com/maps/search/" + quote(query_bits)

    return data


def most_complete(values):
    non_empty = [v for v in values if str(v).strip()]
    return max(non_empty, key=len) if non_empty else ""


def dedup_records(all_results: list) -> list:
    """De-dupe merged multi-query results by (name, phone), keeping the
    most complete value for every field -- this is the 'compile multiple
    queries into one result set' step."""
    seen = {}
    order = []
    for rec in all_results:
        name = str(rec.get("name", "")).strip()
        if not name:
            continue
        key = (name, str(rec.get("phone", "")).strip())
        if key not in seen:
            seen[key] = {k: rec.get(k, "") for k in SCRAPE_COLUMNS}
            order.append(key)
        else:
            merged = seen[key]
            for col in SCRAPE_COLUMNS:
                merged[col] = most_complete([merged.get(col, ""), rec.get(col, "")])
    return [seen[k] for k in order]


# ---------------------------------------------------------------------------
# Email enrichment (optional second pass -- visits each lead's own website)
# ---------------------------------------------------------------------------

async def get_email_from_website(context, website_url, timeout_seconds=15):
    if not website_url:
        return ""
    if _is_facebook_url(website_url):
        return await _get_email_from_facebook(context, website_url, timeout_seconds)

    page = await context.new_page()
    try:
        await page.goto(website_url, wait_until="domcontentloaded", timeout=timeout_seconds * 1000)
        try:
            await page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass

        mailto = await page.query_selector('a[href^="mailto:"]')
        if mailto:
            href = await mailto.get_attribute("href") or ""
            email = href.replace("mailto:", "").split("?")[0].strip()
            if email and not _is_noise_email(email):
                return email

        text = await page.inner_text("body")
        matches = [m for m in EMAIL_RE.findall(text) if not _is_noise_email(m)]
        if matches:
            return matches[0]

        for label in CONTACT_LINK_TEXT_CANDIDATES:
            contact_link = await page.query_selector(f"a:has-text('{label}')")
            if contact_link:
                href = await contact_link.get_attribute("href")
                if not href:
                    continue
                try:
                    await page.goto(href, wait_until="domcontentloaded", timeout=timeout_seconds * 1000)
                    try:
                        await page.wait_for_load_state("networkidle", timeout=5000)
                    except Exception:
                        pass
                except Exception:
                    continue
                mailto = await page.query_selector('a[href^="mailto:"]')
                if mailto:
                    href2 = await mailto.get_attribute("href") or ""
                    email = href2.replace("mailto:", "").split("?")[0].strip()
                    if email and not _is_noise_email(email):
                        return email
                text = await page.inner_text("body")
                matches = [m for m in EMAIL_RE.findall(text) if not _is_noise_email(m)]
                if matches:
                    return matches[0]
                break

        return ""
    except Exception:
        return ""
    finally:
        try:
            await asyncio.wait_for(page.close(), timeout=5)
        except Exception:
            pass


async def _get_email_from_facebook(context, page_url, timeout_seconds=15):
    base = page_url.split("?")[0].rstrip("/")
    candidates = [
        base + "/about_contact_and_basic_info",
        base + "/about_profile_transparency",
        page_url,
    ]
    page = await context.new_page()
    try:
        for url in candidates:
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=timeout_seconds * 1000)
            except Exception:
                continue
            await page.wait_for_timeout(3000)
            text = await page.inner_text("body")
            matches = [m for m in EMAIL_RE.findall(text) if not _is_noise_email(m)]
            if matches:
                return matches[0]
        return ""
    except Exception:
        return ""
    finally:
        try:
            await asyncio.wait_for(page.close(), timeout=5)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Worker thread
# ---------------------------------------------------------------------------

class ScraperWorker(QThread):
    log = Signal(str)
    card_found = Signal(dict)
    query_progress = Signal(int, int, str)     # completed, total, last query
    enrich_progress = Signal(int, int, str)    # completed, total, last name
    finished_ok = Signal(list)                 # deduped, merged list[dict]
    failed = Signal(str)

    def __init__(self, config: dict):
        super().__init__()
        self.cfg = dict(config)
        self._stop_requested = False
        self._chrome_process = None

    def request_stop(self):
        self._stop_requested = True

    # -- Chrome lifecycle -----------------------------------------------

    def _kill_chrome_on_port(self, port):
        marker = f"--remote-debugging-port={port}"
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                cmdline = proc.info["cmdline"] or []
                if any(marker in arg for arg in cmdline):
                    parent = psutil.Process(proc.info["pid"])
                    children = parent.children(recursive=True)
                    for child in children:
                        child.terminate()
                    parent.terminate()
                    gone, alive = psutil.wait_procs([parent, *children], timeout=3)
                    for p in alive:
                        p.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

    def _launch_chrome(self):
        port = self.cfg["chrome_debug_port"]
        self.log.emit("Closing any stale Chrome debug sessions…")
        self._kill_chrome_on_port(port)
        time.sleep(1)

        free_port = find_free_port(preferred=port)
        if free_port != port:
            self.log.emit(f"Port {port} was busy — auto-selected free port {free_port} instead.")
            self.cfg["chrome_debug_port"] = free_port
            port = free_port

        user_data_dir = self.cfg["user_data_dir"]
        os.makedirs(user_data_dir, exist_ok=True)
        lock_file = os.path.join(user_data_dir, "SingletonLock")
        if os.path.exists(lock_file):
            os.remove(lock_file)

        args = [
            self.cfg["chrome_executable"],
            f"--remote-debugging-port={port}",
            f"--user-data-dir={user_data_dir}",
            "--disable-blink-features=AutomationControlled",
            "--disable-features=IsolateOrigins,site-per-process",
            "--lang=en-US,en",
        ]
        if self.cfg.get("headless"):
            args.insert(1, "--headless=new")
            args.append("--window-size=1920,1080")
            args.append(
                "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            )

        self.log.emit(
            f"Launching Chrome ({'headless' if self.cfg.get('headless') else 'visible'}) on port {port}…"
        )
        self._chrome_process = subprocess.Popen(args)
        time.sleep(2)

    def _close_chrome(self):
        self.log.emit("Closing Chrome…")
        self._kill_chrome_on_port(self.cfg["chrome_debug_port"])
        if self._chrome_process is not None:
            try:
                self._chrome_process.wait(timeout=3)
            except Exception:
                pass

    # -- Single-query scrape ---------------------------------------------

    async def _scrape_query_inner(self, page, query):
        url = f"https://www.google.com/maps/search/{query.replace(' ', '+')}"
        await page.goto(url, wait_until="domcontentloaded")

        try:
            await page.wait_for_selector(FEED_SELECTOR, timeout=15000)
        except Exception:
            self.log.emit(f"  no results feed loaded for: {query}")
            return []

        feed = await page.query_selector(FEED_SELECTOR)

        prev_count = 0
        stale_rounds = 0
        reached_end = False
        max_scrolls = self.cfg["max_scrolls_per_query"]
        wait_ms = self.cfg["scroll_wait_ms"]
        stale_threshold = self.cfg["stale_rounds_threshold"]

        for scroll_i in range(max_scrolls):
            if self._stop_requested:
                self.log.emit(f"  [{query}] stop requested — halting scroll early")
                break
            try:
                if feed:
                    await feed.evaluate("el => el.scrollBy(0, el.scrollHeight)")
            except Exception:
                feed = await page.query_selector(FEED_SELECTOR)
                if feed:
                    await feed.evaluate("el => el.scrollBy(0, el.scrollHeight)")

            await page.wait_for_timeout(wait_ms)

            end_marker = await page.query_selector("text=You've reached the end of the list.")
            if end_marker:
                reached_end = True
                self.log.emit(f"  [{query}] reached end of list after {scroll_i + 1} scrolls")
                break

            current_count = len(await page.query_selector_all(CARD_LINK_SELECTOR))
            if current_count <= prev_count:
                stale_rounds += 1
                if stale_rounds >= stale_threshold:
                    break
            else:
                stale_rounds = 0
            prev_count = current_count

        if not reached_end and not self._stop_requested:
            self.log.emit(f"  [{query}] stopped at scroll limit ({max_scrolls}) — list may not be exhausted")

        cards = await page.query_selector_all("div[role='article']")
        query_results = []
        region = self.cfg.get("phone_default_region", "PH")
        for card in cards:
            result = await parse_card(card, region)
            if result["name"]:
                result["source_query"] = query
                query_results.append(result)

        self.log.emit(f"  {query}: {len(query_results)} cards")
        return query_results

    async def scrape_query(self, page, query, semaphore):
        timeout = self.cfg["query_timeout_seconds"]
        async with semaphore:
            if self._stop_requested:
                return []
            try:
                return await asyncio.wait_for(self._scrape_query_inner(page, query), timeout=timeout)
            except asyncio.TimeoutError:
                self.log.emit(f"  [{query}] TIMED OUT after {timeout}s — salvaging loaded cards")
                try:
                    cards = await page.query_selector_all("div[role='article']")
                    results = []
                    region = self.cfg.get("phone_default_region", "PH")
                    for card in cards:
                        result = await parse_card(card, region)
                        if result["name"]:
                            result["source_query"] = query
                            results.append(result)
                    self.log.emit(f"  [{query}] salvaged {len(results)} cards after timeout")
                    return results
                except Exception as e:
                    self.log.emit(f"  [{query}] salvage attempt also failed: {e}")
                    return []

    async def _run_one(self, page, query, semaphore):
        result = await self.scrape_query(page, query, semaphore)
        return query, result

    async def run_all_queries(self, context, queries):
        """All queries are launched at once, each in its own tab, and run
        concurrently under a semaphore capped at max_concurrent_tabs -- this
        is the 'simultaneous parallel execution' for multi-query searches
        like ['dentist in Laguna', 'dentist in Taguig']."""
        semaphore = asyncio.Semaphore(self.cfg["max_concurrent_tabs"])
        pages = [await context.new_page() for _ in queries]
        tasks = [asyncio.ensure_future(self._run_one(p, q, semaphore)) for p, q in zip(pages, queries)]

        all_results = []
        completed = 0
        total = len(tasks)
        for fut in asyncio.as_completed(tasks):
            try:
                query, r = await fut
            except Exception as e:
                completed += 1
                self.log.emit(f"  FAILED with unhandled error: {e!r}")
                self.query_progress.emit(completed, total, "(error)")
                continue
            completed += 1
            self.query_progress.emit(completed, total, query)
            for card in r:
                self.card_found.emit(card)
            all_results.extend(r)

        if self.cfg.get("close_after_run", True):
            for page in pages:
                try:
                    await page.close()
                except Exception:
                    pass

        return all_results

    # -- Email enrichment --------------------------------------------------

    async def enrich_with_emails(self, context, results):
        max_concurrent = max(1, self.cfg.get("email_max_concurrent_tabs", 5))
        timeout_seconds = self.cfg.get("website_timeout_seconds", 15)
        semaphore = asyncio.Semaphore(max_concurrent)

        total = len(results)
        completed = 0
        with_website = sum(1 for c in results if c.get("website"))

        async def _one(card):
            nonlocal completed
            async with semaphore:
                if self._stop_requested:
                    return
                try:
                    website = card.get("website", "")
                    if website:
                        # get_email_from_website() bounds its own goto()/
                        # networkidle calls, but not every await inside it
                        # (inner_text, get_attribute, page.close()) -- a
                        # hung tab or dead CDP connection can otherwise
                        # block this one task forever, which stalls
                        # asyncio.gather() for the whole batch. Mirrors
                        # the hard ceiling _scrape_query_inner() already
                        # gets via asyncio.wait_for() in run_query().
                        card["email"] = await asyncio.wait_for(
                            get_email_from_website(context, website, timeout_seconds),
                            timeout=timeout_seconds + 10,
                        )
                        status = "email found" if card["email"] else "no email on site"
                        self.log.emit(f"  [{card.get('name', '?')}] {status} — {website}")
                    else:
                        self.log.emit(f"  [{card.get('name', '?')}] no website link — skipped")
                except asyncio.TimeoutError:
                    self.log.emit(f"  [{card.get('name', '?')}] enrichment timed out — skipped")
                except Exception as e:
                    self.log.emit(f"  enrichment failed for {card.get('name', '?')}: {e}")
                finally:
                    completed += 1
                    self.enrich_progress.emit(completed, total, card.get("name", ""))

        self.log.emit(f"Enriching {total} leads with website/email lookups (max {max_concurrent} concurrent)…")
        await asyncio.gather(*(_one(c) for c in results))
        found = sum(1 for c in results if c.get("email"))
        self.log.emit(
            f"Email enrichment done — {with_website}/{total} leads had a website link, "
            f"found emails for {found}/{with_website if with_website else total} of those."
        )
        return results

    async def _async_main(self):
        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp(f"http://localhost:{self.cfg['chrome_debug_port']}")
            context = browser.contexts[0] if browser.contexts else await browser.new_context()
            await context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            )
            queries = self.cfg["queries"]
            self.log.emit(
                f"Searching {len(queries)} quer{'y' if len(queries) == 1 else 'ies'} in parallel "
                f"(max {self.cfg['max_concurrent_tabs']} tabs at a time)…"
            )
            all_results = await self.run_all_queries(context, queries)

            self.log.emit(f"\nTotal raw cards across all queries: {len(all_results)}")
            merged = dedup_records(all_results)
            self.log.emit(f"Unique leads after merging + dedup: {len(merged)}")

            if self.cfg.get("enable_email_enrichment", False) and merged and not self._stop_requested:
                merged = await self.enrich_with_emails(context, merged)

            return merged

    # -- Thread entry point ------------------------------------------------

    def run(self):
        try:
            self._launch_chrome()
            merged = asyncio.run(self._async_main())
            self.finished_ok.emit(merged)
        except Exception as e:
            self.failed.emit(str(e))
        finally:
            if self.cfg.get("close_after_run", True):
                self._close_chrome()
            else:
                self.log.emit("Leaving Chrome (and tabs) open — 'Close after run' is unchecked.")