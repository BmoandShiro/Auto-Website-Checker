#!/usr/bin/env python3
"""Auto Website Checker with device-emulated QA checks."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass
from typing import Dict, List, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright


USER_AGENT = "Mozilla/5.0 (compatible; AutoWebsiteChecker/1.0)"
TIMEOUT_SECONDS = 15
MAX_LINKS_PER_CHECK = 30
FAST_LOAD_MS_THRESHOLD = 2500
MAX_PAGES_TO_AUDIT = 5
PHONE_PATTERN = re.compile(r"\+?\d[\d\-\(\)\s]{7,}\d")


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
    load_ms: int
    nav_ok: bool
    links_ok: bool
    links_note: str
    phone_ok: bool
    footer_ok: bool
    footer_note: str


@dataclass
class MultiPageDeviceAudit:
    name: str
    pages_checked: int
    avg_load_ms: int
    nav_ok: bool
    links_ok: bool
    links_note: str
    phone_ok: bool
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


def check_link_set(urls: List[str], max_to_check: int = MAX_LINKS_PER_CHECK) -> Tuple[bool, str]:
    if not urls:
        return False, "No links found"
    tested = 0
    failed: List[Tuple[str, int]] = []
    for link in urls[:max_to_check]:
        status = fetch_status(link)
        tested += 1
        if status == 0 or status >= 400:
            failed.append((link, status))
    if failed:
        preview = ", ".join(f"{u} ({s})" for u, s in failed[:3])
        return False, f"{len(failed)}/{tested} failed: {preview}"
    return True, f"Checked {tested} links, all OK"


def fetch_pagespeed_result(url: str, strategy: str) -> Dict[str, object]:
    endpoint = (
        "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
        f"?url={url}&strategy={strategy}&category=PERFORMANCE"
    )
    req = Request(endpoint, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
        payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    return payload


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


def discover_internal_pages(seed_url: str, max_pages: int = MAX_PAGES_TO_AUDIT) -> List[str]:
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


def audit_device(context: BrowserContext, url: str, profile_name: str) -> DeviceAudit:
    page: Page = context.new_page()
    page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_SECONDS * 1000)
    page.wait_for_load_state("load", timeout=TIMEOUT_SECONDS * 1000)

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

    links_ok, links_note = check_link_set(all_links)
    footer_ok, footer_note = check_link_set(footer_links)

    header_text = page.locator("header").first.inner_text() if page.locator("header").count() > 0 else ""
    phone_ok = bool(PHONE_PATTERN.search(header_text))

    page.close()
    return DeviceAudit(
        name=profile_name,
        load_ms=int(load_ms),
        nav_ok=nav_ok,
        links_ok=links_ok,
        links_note=links_note,
        phone_ok=phone_ok,
        footer_ok=footer_ok,
        footer_note=footer_note,
    )


def combine_device_audits(profile_name: str, audits: List[DeviceAudit]) -> MultiPageDeviceAudit:
    if not audits:
        return MultiPageDeviceAudit(
            name=profile_name,
            pages_checked=0,
            avg_load_ms=0,
            nav_ok=False,
            links_ok=False,
            links_note="No pages audited",
            phone_ok=False,
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

    return MultiPageDeviceAudit(
        name=profile_name,
        pages_checked=pages_checked,
        avg_load_ms=avg_load_ms,
        nav_ok=nav_ok,
        links_ok=links_ok,
        links_note=f"{pages_checked} pages checked; link-check failures on {failed_links} page(s)",
        phone_ok=phone_ok,
        footer_ok=footer_ok,
        footer_note=f"{pages_checked} pages checked; footer-check failures on {failed_footer} page(s)",
    )


def run_device_audits(urls: List[str]) -> Dict[str, MultiPageDeviceAudit]:
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
                finally:
                    context.close()
        finally:
            browser.close()
    merged: Dict[str, MultiPageDeviceAudit] = {}
    for profile in DEVICE_PROFILES:
        merged[profile.name] = combine_device_audits(profile.name, by_profile[profile.name])
    return merged


def build_results(url: str, max_pages: int = MAX_PAGES_TO_AUDIT) -> List[CheckResult]:
    pages = discover_internal_pages(url, max_pages=max_pages)
    audits = run_device_audits(pages)

    desktop = audits["desktop"]
    mobile = audits["mobile"]
    tablet = audits["tablet"]
    is_wp, wp_note = detect_wordpress(url)

    desktop_cwv_ok, desktop_cwv_note = False, "Unavailable"
    mobile_cwv_ok, mobile_cwv_note = False, "Unavailable"
    try:
        desktop_payload = fetch_pagespeed_result(url, "desktop")
        desktop_ok, desktop_note = extract_core_web_vitals_pass(desktop_payload)
        desktop_cwv_ok, desktop_cwv_note = desktop_ok, desktop_note
    except (HTTPError, URLError, ValueError, KeyError) as exc:
        desktop_cwv_note = f"PSI error: {exc}"
    try:
        mobile_payload = fetch_pagespeed_result(url, "mobile")
        mobile_ok, mobile_note = extract_core_web_vitals_pass(mobile_payload)
        mobile_cwv_ok, mobile_cwv_note = mobile_ok, mobile_note
    except (HTTPError, URLError, ValueError, KeyError) as exc:
        mobile_cwv_note = f"PSI error: {exc}"

    return [
        CheckResult(
            component="If Inheriting an Exiting Website: Is it a passable design?",
            yes_no="TBD",
            desktop="Manual",
            mobile="Manual",
            tablet="Manual",
            notes="Manual visual/brand quality review required per device.",
        ),
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
        CheckResult(
            component="Navigation bar functionality - responsive menu bar",
            yes_no=yn(desktop.nav_ok and mobile.nav_ok and tablet.nav_ok),
            desktop=pf(desktop.nav_ok),
            mobile=pf(mobile.nav_ok),
            tablet=pf(tablet.nav_ok),
            notes="Device-emulated check for nav and responsive menu hints.",
        ),
        CheckResult(
            component="Working links & buttons",
            yes_no=yn(desktop.links_ok and mobile.links_ok and tablet.links_ok),
            desktop=pf(desktop.links_ok),
            mobile=pf(mobile.links_ok),
            tablet=pf(tablet.links_ok),
            notes=f"D:{desktop.links_note} | M:{mobile.links_note} | T:{tablet.links_note}",
        ),
        CheckResult(
            component="Phone Number Present in Head (NOT Only Book Online)",
            yes_no=yn(desktop.phone_ok and mobile.phone_ok and tablet.phone_ok),
            desktop=pf(desktop.phone_ok),
            mobile=pf(mobile.phone_ok),
            tablet=pf(tablet.phone_ok),
            notes=f"Device-emulated header text phone regex across {len(pages)} page(s).",
        ),
        CheckResult(
            component="Footer functionality - working links",
            yes_no=yn(desktop.footer_ok and mobile.footer_ok and tablet.footer_ok),
            desktop=pf(desktop.footer_ok),
            mobile=pf(mobile.footer_ok),
            tablet=pf(tablet.footer_ok),
            notes=f"D:{desktop.footer_note} | M:{mobile.footer_note} | T:{tablet.footer_note}",
        ),
        CheckResult(
            component="Rise Plugin Compatible (Wordpress)",
            yes_no=yn(is_wp),
            desktop=pf(is_wp),
            mobile=pf(is_wp),
            tablet=pf(is_wp),
            notes=wp_note,
        ),
        CheckResult(
            component="Core Web Vitals",
            yes_no=yn(desktop_cwv_ok and mobile_cwv_ok),
            desktop=pf(desktop_cwv_ok),
            mobile=pf(mobile_cwv_ok),
            tablet=pf(mobile_cwv_ok),
            notes=f"Desktop {desktop_cwv_note}; Mobile/Tablet {mobile_cwv_note}; pages_audited={len(pages)}",
        ),
    ]


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
