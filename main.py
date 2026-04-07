#!/usr/bin/env python3
"""Auto Website Checker with device-emulated QA checks."""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
import sys
import time
from dataclasses import dataclass
from typing import Dict, List, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from playwright.sync_api import Browser, BrowserContext, Error as PlaywrightError, Page, TimeoutError as PlaywrightTimeoutError, sync_playwright


USER_AGENT = "Mozilla/5.0 (compatible; AutoWebsiteChecker/1.0)"
TIMEOUT_SECONDS = 30
MAX_LINKS_PER_CHECK = 30
FAST_LOAD_MS_THRESHOLD = 2500
MAX_PAGES_TO_AUDIT = 5
PSI_COOLDOWN_SECONDS = 3
REQUEST_THROTTLE_SECONDS = 0.5
PREFER_CRUX_FIRST = True
ENABLE_CORE_WEB_VITALS = False
PHONE_PATTERN = re.compile(r"\+?\d[\d\-\(\)\s]{7,}\d")
LAST_REQUEST_TS = 0.0


@dataclass
class CheckResult:
    component: str
    yes_no: str
    desktop: str
    mobile: str
    tablet: str
    notes: str = ""


@dataclass
class DeviceAudit:
    name: str
    page_url: str
    load_ms: int
    nav_ok: bool
    links_ok: bool
    links_note: str
    links_failed: List[str]
    phone_ok: bool
    phone_note: str
    footer_ok: bool
    footer_note: str
    footer_failed: List[str]


@dataclass
class MultiPageDeviceAudit:
    name: str
    pages_checked: int
    avg_load_ms: int
    nav_ok: bool
    nav_note: str
    links_ok: bool
    links_note: str
    phone_ok: bool
    phone_note: str
    footer_ok: bool
    footer_note: str


@dataclass
class DeviceProfile:
    name: str
    width: int
    height: int
    is_mobile: bool
    has_touch: bool


DEVICE_PROFILES = [
    DeviceProfile(name="desktop", width=1366, height=768, is_mobile=False, has_touch=False),
    DeviceProfile(name="mobile", width=390, height=844, is_mobile=True, has_touch=True),
    DeviceProfile(name="tablet", width=820, height=1180, is_mobile=True, has_touch=True),
]


def apply_runtime_settings(settings: Dict[str, object] | None) -> None:
    global TIMEOUT_SECONDS, MAX_LINKS_PER_CHECK, FAST_LOAD_MS_THRESHOLD
    global MAX_PAGES_TO_AUDIT, PSI_COOLDOWN_SECONDS, REQUEST_THROTTLE_SECONDS
    global PREFER_CRUX_FIRST, ENABLE_CORE_WEB_VITALS
    if not settings:
        return
    TIMEOUT_SECONDS = int(settings.get("timeout_seconds", TIMEOUT_SECONDS))
    MAX_LINKS_PER_CHECK = int(settings.get("max_links_per_check", MAX_LINKS_PER_CHECK))
    FAST_LOAD_MS_THRESHOLD = int(settings.get("fast_load_ms_threshold", FAST_LOAD_MS_THRESHOLD))
    MAX_PAGES_TO_AUDIT = int(settings.get("max_pages_to_audit", MAX_PAGES_TO_AUDIT))
    PSI_COOLDOWN_SECONDS = float(settings.get("psi_cooldown_seconds", PSI_COOLDOWN_SECONDS))
    REQUEST_THROTTLE_SECONDS = float(settings.get("request_throttle_seconds", REQUEST_THROTTLE_SECONDS))
    PREFER_CRUX_FIRST = bool(settings.get("prefer_crux_first", PREFER_CRUX_FIRST))
    ENABLE_CORE_WEB_VITALS = bool(settings.get("enable_core_web_vitals", ENABLE_CORE_WEB_VITALS))


def throttle_requests() -> None:
    global LAST_REQUEST_TS
    now = time.time()
    elapsed = now - LAST_REQUEST_TS
    if elapsed < REQUEST_THROTTLE_SECONDS:
        time.sleep(REQUEST_THROTTLE_SECONDS - elapsed)
    LAST_REQUEST_TS = time.time()


def fetch_status(url: str) -> int:
    req = Request(url, headers={"User-Agent": USER_AGENT}, method="HEAD")
    try:
        with urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            return resp.status
    except HTTPError as exc:
        return exc.code
    except Exception:
        req = Request(url, headers={"User-Agent": USER_AGENT}, method="GET")
        try:
            with urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
                return resp.status
        except HTTPError as exc:
            return exc.code
        except Exception:
            return 0


def normalize_links(base_url: str, links: List[str]) -> List[str]:
    normalized: List[str] = []
    for href in links:
        href = href.strip()
        if not href:
            continue
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        full = urljoin(base_url, href)
        parsed = urlparse(full)
        if parsed.scheme not in ("http", "https"):
            continue
        normalized.append(full)

    seen = set()
    deduped = []
    for link in normalized:
        if link not in seen:
            seen.add(link)
            deduped.append(link)
    return deduped


def check_link_set(urls: List[str], max_to_check: int | None = None) -> Tuple[bool, str, List[str]]:
    if max_to_check is None:
        max_to_check = MAX_LINKS_PER_CHECK
    if not urls:
        return False, "No links found", []
    tested = 0
    failed: List[Tuple[str, int]] = []
    for link in urls[:max_to_check]:
        status = fetch_status(link)
        tested += 1
        if status == 0 or status >= 400:
            failed.append((link, status))
    if failed:
        preview = ", ".join(f"{u} ({s})" for u, s in failed[:5])
        failure_urls = [f"{u} ({s})" for u, s in failed]
        return False, f"{len(failed)}/{tested} failed: {preview}", failure_urls
    return True, f"Checked {tested} links, all OK", []


def fetch_pagespeed_result(url: str, strategy: str, api_key: str = "") -> Dict[str, object]:
    endpoint = (
        "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
        f"?url={url}&strategy={strategy}&category=PERFORMANCE"
    )
    if api_key:
        endpoint += f"&key={api_key}"
    throttle_requests()
    req = Request(endpoint, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
        payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    return payload


def fetch_pagespeed_with_retry(
    url: str, strategy: str, retries: int = 3, base_delay_s: float = 1.5, api_key: str = ""
) -> Dict[str, object]:
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return fetch_pagespeed_result(url, strategy, api_key=api_key)
        except HTTPError as exc:
            last_exc = exc
            if exc.code == 429 and attempt < retries:
                jitter = random.uniform(0.0, 0.75)
                time.sleep((base_delay_s * (2**attempt)) + jitter)
                continue
            raise
        except (URLError, ValueError, KeyError) as exc:
            last_exc = exc
            if attempt < retries:
                jitter = random.uniform(0.0, 0.75)
                time.sleep((base_delay_s * (2**attempt)) + jitter)
                continue
            raise
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("Unknown PageSpeed retry failure")


def fetch_crux_result(url: str, form_factor: str, api_key: str) -> Dict[str, object]:
    endpoint = f"https://chromeuxreport.googleapis.com/v1/records:queryRecord?key={api_key}"
    payload = json.dumps({"url": url, "formFactor": form_factor}).encode("utf-8")
    throttle_requests()
    req = Request(
        endpoint,
        data=payload,
        headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def extract_crux_pass(payload: Dict[str, object]) -> Tuple[bool, str]:
    record = payload.get("record", {})
    metrics = record.get("metrics", {}) if isinstance(record, dict) else {}
    if not isinstance(metrics, dict):
        return False, "CrUX record has no metrics"

    lcp = metrics.get("largest_contentful_paint", {}).get("percentiles", {}).get("p75")
    inp = metrics.get("interaction_to_next_paint", {}).get("percentiles", {}).get("p75")
    cls = metrics.get("cumulative_layout_shift", {}).get("percentiles", {}).get("p75")

    if lcp is None or inp is None or cls is None:
        return False, "CrUX missing LCP/INP/CLS p75"

    cls_value = float(cls) / 1000.0
    passed = int(lcp) <= 2500 and int(inp) <= 200 and cls_value <= 0.1
    note = f"crux_p75 lcp={int(lcp)}ms inp={int(inp)}ms cls={cls_value:.3f}"
    return passed, note


def extract_core_web_vitals_pass(payload: Dict[str, object]) -> Tuple[bool, str]:
    loading = payload.get("loadingExperience", {})
    if isinstance(loading, dict):
        overall = str(loading.get("overall_category", "UNKNOWN")).upper()
        if overall and overall != "UNKNOWN":
            return overall == "FAST", f"loadingExperience={overall}"

    origin_loading = payload.get("originLoadingExperience", {})
    if isinstance(origin_loading, dict):
        overall = str(origin_loading.get("overall_category", "UNKNOWN")).upper()
        if overall and overall != "UNKNOWN":
            return overall == "FAST", f"originLoadingExperience={overall}"

    lighthouse = payload.get("lighthouseResult", {})
    if isinstance(lighthouse, dict):
        categories = lighthouse.get("categories", {})
        if isinstance(categories, dict):
            performance = categories.get("performance", {})
            if isinstance(performance, dict) and isinstance(performance.get("score"), (int, float)):
                score = float(performance["score"]) * 100
                return score >= 75, f"lighthouse_performance={score:.0f}"
    return False, "No CWV field returned"


def yn(value: bool) -> str:
    return "Yes" if value else "No"


def pf(value: bool) -> str:
    return "Pass" if value else "Fail"


def get_hostname(url: str) -> str:
    return urlparse(url).netloc.lower()


def discover_internal_pages(seed_url: str, max_pages: int | None = None) -> List[str]:
    if max_pages is None:
        max_pages = MAX_PAGES_TO_AUDIT
    pages = [seed_url]
    seed_host = get_hostname(seed_url)
    try:
        req = Request(seed_url, headers={"User-Agent": USER_AGENT})
        with urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception:
        return pages

    hrefs = re.findall(r'href\s*=\s*["\']([^"\']+)["\']', html, flags=re.IGNORECASE)
    candidates = normalize_links(seed_url, hrefs)
    for candidate in candidates:
        if len(pages) >= max_pages:
            break
        if get_hostname(candidate) != seed_host:
            continue
        if candidate not in pages:
            pages.append(candidate)
    return pages


def detect_wordpress(url: str) -> Tuple[bool, str]:
    markers = ["/wp-content/", "/wp-includes/", "wp-json", "wordpress", "wp-"]
    try:
        req = Request(url, headers={"User-Agent": USER_AGENT})
        with urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            html = resp.read().decode("utf-8", errors="replace").lower()
        if any(marker in html for marker in markers):
            return True, "WordPress markers found in page source"
        # Quick probe for common WP endpoint.
        wp_json = urljoin(url, "/wp-json/")
        status = fetch_status(wp_json)
        if 200 <= status < 400:
            return True, f"wp-json reachable ({status})"
        return False, "No WordPress markers found"
    except Exception:
        return False, "Unable to confirm WordPress markers"


def detect_phone_in_header(page: Page) -> Tuple[bool, str]:
    has_phone, note = page.evaluate(
        """
        () => {
          const selectors = [
            "header",
            "[role='banner']",
            ".header",
            "#header",
            ".site-header",
            ".top-bar",
            "nav"
          ];
          const containers = [];
          for (const s of selectors) {
            for (const el of document.querySelectorAll(s)) containers.push(el);
          }
          const unique = [...new Set(containers)];
          const phonePattern = /\\+?\\d[\\d\\-\\(\\)\\s]{7,}\\d/;

          // Prefer top-of-page containers, not footer.
          const inHeader = unique.filter((el) => {
            if (el.closest("footer")) return false;
            const rect = el.getBoundingClientRect();
            return rect.top < (window.innerHeight * 0.5);
          });

          for (const el of inHeader) {
            const text = (el.innerText || "").trim();
            if (phonePattern.test(text)) {
              return [true, `header text match in ${el.tagName.toLowerCase()}`];
            }
            const tel = el.querySelector("a[href^='tel:']");
            if (tel) {
              return [true, `tel link found in ${el.tagName.toLowerCase()}`];
            }
          }

          // Fallback: a visible tel link above fold not in footer.
          const telLinks = [...document.querySelectorAll("a[href^='tel:']")].filter((el) => {
            if (el.closest("footer")) return false;
            const rect = el.getBoundingClientRect();
            return rect.top < (window.innerHeight * 0.6);
          });
          if (telLinks.length > 0) {
            return [true, "tel link found near top of page"];
          }
          return [false, "No phone detected in header/top-nav area"];
        }
        """
    )
    return bool(has_phone), str(note)


def audit_device(context: BrowserContext, url: str, profile_name: str) -> DeviceAudit:
    page: Page = context.new_page()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_SECONDS * 1000)
        page.wait_for_load_state("load", timeout=TIMEOUT_SECONDS * 1000)
    except (PlaywrightTimeoutError, PlaywrightError):
        page.close()
        return DeviceAudit(
            name=profile_name,
            page_url=url,
            load_ms=TIMEOUT_SECONDS * 1000,
            nav_ok=False,
            links_ok=False,
            links_note=f"Page timeout/error after {TIMEOUT_SECONDS}s",
            links_failed=[f"{url} (timeout/error)"],
            phone_ok=False,
            phone_note=f"Page timeout/error after {TIMEOUT_SECONDS}s",
            footer_ok=False,
            footer_note=f"Page timeout/error after {TIMEOUT_SECONDS}s",
            footer_failed=[f"{url} (timeout/error)"],
        )

    load_ms = page.evaluate(
        """
        () => {
          const nav = performance.getEntriesByType('navigation')[0];
          if (nav && nav.loadEventEnd && nav.startTime !== undefined) {
            return Math.round(nav.loadEventEnd - nav.startTime);
          }
          return Math.round(performance.now());
        }
        """
    )

    nav_count = page.locator("nav").count()
    responsive_count = page.locator(
        "[class*='hamburger'], [class*='menu-toggle'], [class*='mobile-menu'], "
        "[class*='navbar-toggler'], [id*='hamburger'], [id*='menu-toggle'], "
        "button[aria-label*='menu' i], button[aria-controls*='menu' i]"
    ).count()
    viewport_count = page.locator("meta[name='viewport']").count()
    nav_ok = nav_count > 0 and (responsive_count > 0 or viewport_count > 0)

    all_links = normalize_links(url, page.eval_on_selector_all("a[href]", "els => els.map(e => e.getAttribute('href') || '')"))
    footer_links = normalize_links(
        url, page.eval_on_selector_all("footer a[href]", "els => els.map(e => e.getAttribute('href') || '')")
    )

    links_ok, links_note, links_failed = check_link_set(all_links)
    footer_ok, footer_note, footer_failed = check_link_set(footer_links)

    phone_ok, phone_note = detect_phone_in_header(page)

    page.close()
    return DeviceAudit(
        name=profile_name,
        page_url=url,
        load_ms=int(load_ms),
        nav_ok=nav_ok,
        links_ok=links_ok,
        links_note=links_note,
        links_failed=links_failed,
        phone_ok=phone_ok,
        phone_note=phone_note,
        footer_ok=footer_ok,
        footer_note=footer_note,
        footer_failed=footer_failed,
    )


def combine_device_audits(profile_name: str, audits: List[DeviceAudit]) -> MultiPageDeviceAudit:
    if not audits:
        return MultiPageDeviceAudit(
            name=profile_name,
            pages_checked=0,
            avg_load_ms=0,
            nav_ok=False,
            nav_note="No pages audited",
            links_ok=False,
            links_note="No pages audited",
            phone_ok=False,
            phone_note="No pages audited",
            footer_ok=False,
            footer_note="No pages audited",
        )

    pages_checked = len(audits)
    avg_load_ms = int(sum(a.load_ms for a in audits) / pages_checked)
    nav_ok = all(a.nav_ok for a in audits)
    links_ok = all(a.links_ok for a in audits)
    phone_ok = all(a.phone_ok for a in audits)
    footer_ok = all(a.footer_ok for a in audits)

    failed_links = sum(0 if a.links_ok else 1 for a in audits)
    failed_footer = sum(0 if a.footer_ok else 1 for a in audits)
    failed_nav_pages = [a.page_url for a in audits if not a.nav_ok]
    failed_phone_pages = [f"{a.page_url} ({a.phone_note})" for a in audits if not a.phone_ok]

    failed_link_entries: List[str] = []
    for a in audits:
        for entry in a.links_failed[:5]:
            failed_link_entries.append(f"{a.page_url} -> {entry}")

    failed_footer_entries: List[str] = []
    for a in audits:
        for entry in a.footer_failed[:5]:
            failed_footer_entries.append(f"{a.page_url} -> {entry}")

    return MultiPageDeviceAudit(
        name=profile_name,
        pages_checked=pages_checked,
        avg_load_ms=avg_load_ms,
        nav_ok=nav_ok,
        nav_note=(
            "All pages passed nav check"
            if nav_ok
            else f"Nav failed on page(s): {', '.join(failed_nav_pages[:5])}"
        ),
        links_ok=links_ok,
        links_note=(
            f"{pages_checked} pages checked; link-check failures on {failed_links} page(s)"
            if links_ok
            else f"{pages_checked} pages checked; failures: {' | '.join(failed_link_entries[:8])}"
        ),
        phone_ok=phone_ok,
        phone_note=(
            "Phone found in header/top-nav on all pages"
            if phone_ok
            else f"Phone missing on: {' | '.join(failed_phone_pages[:8])}"
        ),
        footer_ok=footer_ok,
        footer_note=(
            f"{pages_checked} pages checked; footer-check failures on {failed_footer} page(s)"
            if footer_ok
            else f"{pages_checked} pages checked; footer failures: {' | '.join(failed_footer_entries[:8])}"
        ),
    )


def run_device_audits(urls: List[str], on_audit_complete=None) -> Dict[str, MultiPageDeviceAudit]:
    audits: Dict[str, DeviceAudit] = {}
    by_profile: Dict[str, List[DeviceAudit]] = {p.name: [] for p in DEVICE_PROFILES}
    with sync_playwright() as p:
        browser: Browser = p.chromium.launch(headless=True)
        try:
            for profile in DEVICE_PROFILES:
                context = browser.new_context(
                    viewport={"width": profile.width, "height": profile.height},
                    is_mobile=profile.is_mobile,
                    has_touch=profile.has_touch,
                    user_agent=USER_AGENT,
                )
                try:
                    for url in urls:
                        by_profile[profile.name].append(audit_device(context, url, profile.name))
                        if on_audit_complete:
                            on_audit_complete()
                finally:
                    context.close()
        finally:
            browser.close()
    merged: Dict[str, MultiPageDeviceAudit] = {}
    for profile in DEVICE_PROFILES:
        merged[profile.name] = combine_device_audits(profile.name, by_profile[profile.name])
    return merged


def build_results(
    url: str,
    max_pages: int = MAX_PAGES_TO_AUDIT,
    on_row=None,
    on_status=None,
    on_progress=None,
    settings: Dict[str, object] | None = None,
) -> List[CheckResult]:
    apply_runtime_settings(settings)
    if settings and "max_pages_to_audit" in settings:
        max_pages = int(settings["max_pages_to_audit"])

    def emit_status(message: str) -> None:
        if on_status:
            on_status(message)

    def emit_row(row: CheckResult, out: List[CheckResult]) -> None:
        out.append(row)
        if on_row:
            on_row(row)

    progress_total = 1
    progress_done = 0

    def init_progress(total: int) -> None:
        nonlocal progress_total, progress_done
        progress_total = max(1, total)
        progress_done = 0
        if on_progress:
            on_progress(0, progress_total)

    def step_progress() -> None:
        nonlocal progress_done
        progress_done += 1
        if on_progress:
            on_progress(min(progress_done, progress_total), progress_total)

    emit_status("Discovering internal pages...")
    pages = discover_internal_pages(url, max_pages=max_pages)
    cwv_steps = 2 if ENABLE_CORE_WEB_VITALS else 0
    total_steps = 1 + (len(pages) * len(DEVICE_PROFILES)) + cwv_steps + 8
    init_progress(total_steps)
    step_progress()
    emit_status(f"Auditing {len(pages)} page(s) across desktop/mobile/tablet...")
    audits = run_device_audits(pages, on_audit_complete=step_progress)

    desktop = audits["desktop"]
    mobile = audits["mobile"]
    tablet = audits["tablet"]
    is_wp, wp_note = detect_wordpress(url)

    desktop_cwv_ok, desktop_cwv_note = False, "Unavailable"
    mobile_cwv_ok, mobile_cwv_note = False, "Unavailable"
    psi_key = os.getenv("PSI_API_KEY", "").strip()
    crux_key = os.getenv("CRUX_API_KEY", "").strip() or psi_key

    def evaluate_cwv(strategy: str, form_factor: str) -> Tuple[bool, str]:
        psi_error = ""
        crux_error = ""
        if PREFER_CRUX_FIRST and crux_key:
            try:
                crux_payload = fetch_crux_result(url, form_factor, crux_key)
                ok, note = extract_crux_pass(crux_payload)
                return ok, f"CrUX {note}"
            except (HTTPError, URLError, ValueError, KeyError) as exc:
                crux_error = f"CrUX error: {exc}"

        try:
            psi_payload = fetch_pagespeed_with_retry(url, strategy, api_key=psi_key)
            ok, note = extract_core_web_vitals_pass(psi_payload)
            if crux_error:
                return ok, f"PSI {note}; fallback_from_{crux_error}"
            return ok, f"PSI {note}"
        except (HTTPError, URLError, ValueError, KeyError) as exc:
            psi_error = f"PSI error: {exc}"

        if (not PREFER_CRUX_FIRST) and crux_key:
            try:
                crux_payload = fetch_crux_result(url, form_factor, crux_key)
                ok, note = extract_crux_pass(crux_payload)
                return ok, f"{psi_error}; fallback=CrUX {note}"
            except (HTTPError, URLError, ValueError, KeyError) as exc:
                return False, f"{psi_error}; CrUX error: {exc}"

        if crux_error:
            return False, f"{crux_error}; {psi_error}"
        return False, psi_error or "CWV unavailable"

    if ENABLE_CORE_WEB_VITALS:
        emit_status("Checking Core Web Vitals (desktop)...")
        desktop_cwv_ok, desktop_cwv_note = evaluate_cwv("desktop", "DESKTOP")
        step_progress()

        # Cool down between PSI calls to reduce API throttling.
        time.sleep(PSI_COOLDOWN_SECONDS)

        emit_status("Checking Core Web Vitals (mobile/tablet)...")
        mobile_cwv_ok, mobile_cwv_note = evaluate_cwv("mobile", "PHONE")
        step_progress()
    else:
        desktop_cwv_ok, desktop_cwv_note = False, "Skipped (Core Web Vitals disabled in Settings)"
        mobile_cwv_ok, mobile_cwv_note = False, "Skipped (Core Web Vitals disabled in Settings)"

    rows: List[CheckResult] = []
    emit_row(
        CheckResult(
            component="If Inheriting an Exiting Website: Is it a passable design?",
            yes_no="TBD",
            desktop="Manual",
            mobile="Manual",
            tablet="Manual",
            notes="Manual visual/brand quality review required per device.",
        ),
        rows,
    )
    step_progress()
    emit_row(
        CheckResult(
            component="Fast website/page load speed (Does it feel fast/snappy?)",
            yes_no=yn(
                desktop.avg_load_ms <= FAST_LOAD_MS_THRESHOLD
                and mobile.avg_load_ms <= FAST_LOAD_MS_THRESHOLD
                and tablet.avg_load_ms <= FAST_LOAD_MS_THRESHOLD
            ),
            desktop=pf(desktop.avg_load_ms <= FAST_LOAD_MS_THRESHOLD),
            mobile=pf(mobile.avg_load_ms <= FAST_LOAD_MS_THRESHOLD),
            tablet=pf(tablet.avg_load_ms <= FAST_LOAD_MS_THRESHOLD),
            notes=(
                f"Avg load ms across {len(pages)} page(s): "
                f"desktop={desktop.avg_load_ms}, mobile={mobile.avg_load_ms}, tablet={tablet.avg_load_ms}"
            ),
        ),
        rows,
    )
    step_progress()
    emit_row(
        CheckResult(
            component="Navigation bar functionality - responsive menu bar",
            yes_no=yn(desktop.nav_ok and mobile.nav_ok and tablet.nav_ok),
            desktop=pf(desktop.nav_ok),
            mobile=pf(mobile.nav_ok),
            tablet=pf(tablet.nav_ok),
            notes=f"D:{desktop.nav_note} | M:{mobile.nav_note} | T:{tablet.nav_note}",
        ),
        rows,
    )
    step_progress()
    emit_row(
        CheckResult(
            component="Working links & buttons",
            yes_no=yn(desktop.links_ok and mobile.links_ok and tablet.links_ok),
            desktop=pf(desktop.links_ok),
            mobile=pf(mobile.links_ok),
            tablet=pf(tablet.links_ok),
            notes=f"D:{desktop.links_note} | M:{mobile.links_note} | T:{tablet.links_note}",
        ),
        rows,
    )
    step_progress()
    emit_row(
        CheckResult(
            component="Phone Number Present in Head (NOT Only Book Online)",
            yes_no=yn(desktop.phone_ok and mobile.phone_ok and tablet.phone_ok),
            desktop=pf(desktop.phone_ok),
            mobile=pf(mobile.phone_ok),
            tablet=pf(tablet.phone_ok),
            notes=f"D:{desktop.phone_note} | M:{mobile.phone_note} | T:{tablet.phone_note}",
        ),
        rows,
    )
    step_progress()
    emit_row(
        CheckResult(
            component="Footer functionality - working links",
            yes_no=yn(desktop.footer_ok and mobile.footer_ok and tablet.footer_ok),
            desktop=pf(desktop.footer_ok),
            mobile=pf(mobile.footer_ok),
            tablet=pf(tablet.footer_ok),
            notes=f"D:{desktop.footer_note} | M:{mobile.footer_note} | T:{tablet.footer_note}",
        ),
        rows,
    )
    step_progress()
    emit_row(
        CheckResult(
            component="Rise Plugin Compatible (Wordpress)",
            yes_no=yn(is_wp),
            desktop=pf(is_wp),
            mobile=pf(is_wp),
            tablet=pf(is_wp),
            notes=wp_note,
        ),
        rows,
    )
    step_progress()
    emit_row(
        CheckResult(
            component="Core Web Vitals",
            yes_no=yn(desktop_cwv_ok and mobile_cwv_ok),
            desktop=pf(desktop_cwv_ok),
            mobile=pf(mobile_cwv_ok),
            tablet=pf(mobile_cwv_ok),
            notes=f"Desktop {desktop_cwv_note}; Mobile/Tablet {mobile_cwv_note}; pages_audited={len(pages)}",
        ),
        rows,
    )
    step_progress()
    emit_status("Finalizing results...")
    return rows


def write_csv(results: List[CheckResult], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["QA Component", "Y/N", "Desktop Pass/Fail", "Mobile Pass/Fail", "Tablet Pass/Fail", "Notes"])
        for row in results:
            writer.writerow([row.component, row.yes_no, row.desktop, row.mobile, row.tablet, row.notes])


def main() -> int:
    parser = argparse.ArgumentParser(description="Automate website UX QA checks")
    parser.add_argument("url", help="Website URL, e.g. https://example.com")
    parser.add_argument("--out", default="qa_results.csv", help="Output CSV file")
    parser.add_argument("--max-pages", type=int, default=MAX_PAGES_TO_AUDIT, help="Max internal pages to audit")
    args = parser.parse_args()

    if not args.url.startswith(("http://", "https://")):
        print("Error: URL must start with http:// or https://", file=sys.stderr)
        return 2

    try:
        results = build_results(args.url, max_pages=max(1, args.max_pages))
        write_csv(results, args.out)
    except Exception as exc:
        print(f"Failed: {exc}", file=sys.stderr)
        return 1

    print(f"Wrote results to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
