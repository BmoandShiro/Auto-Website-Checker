#!/usr/bin/env python3
"""Auto Website Checker with device-emulated QA checks."""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Dict, List, Set, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from playwright.sync_api import Browser, BrowserContext, Error as PlaywrightError, Page, TimeoutError as PlaywrightTimeoutError, sync_playwright


USER_AGENT = "Mozilla/5.0 (compatible; AutoWebsiteChecker/1.0)"
TIMEOUT_SECONDS = 30
MAX_LINKS_PER_CHECK = 30
FAST_LOAD_MS_THRESHOLD = 2500
MAX_PAGES_TO_AUDIT = 5
REQUEST_THROTTLE_SECONDS = 0.5
PARALLEL_CHECKS = True
PARALLEL_MAX_WORKERS = 12
MANUAL_MEDIA_VERIFY_HINT = (
    "If this automated check fails, open the site in a normal browser and confirm manually "
    "(CDNs, lazy-load, and embeds often confuse automated probes)."
)
PHONE_PATTERN = re.compile(r"\+?\d[\d\-\(\)\s]{7,}\d")
LAST_REQUEST_TS = 0.0
QA_ROW_OPTIONS = [
    ("speed_snappy", "Fast website/page load speed (Does it feel fast/snappy?)"),
    ("nav_responsive", "Navigation bar functionality - responsive menu bar"),
    ("links_buttons", "Working links & buttons"),
    ("phone_in_header", "Phone Number Present in Head (NOT Only Book Online)"),
    ("footer_links", "Footer functionality - working links"),
    ("spelling_grammar", "Correct spelling & grammar, no typos"),
    ("images_quality", "Images are compressed, high resolution, not blurry or pixelated"),
    ("videos_load", "Videos load correctly"),
    ("social_links", "Social media links out to correct pages"),
    ("business_name", "Using correct business name"),
    ("rise_compat", "Rise Plugin Compatible (Wordpress)"),
]


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
    links_ok_urls: List[str]
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
    global MAX_PAGES_TO_AUDIT, REQUEST_THROTTLE_SECONDS
    global PARALLEL_CHECKS, PARALLEL_MAX_WORKERS
    if not settings:
        return
    TIMEOUT_SECONDS = int(settings.get("timeout_seconds", TIMEOUT_SECONDS))
    MAX_LINKS_PER_CHECK = int(settings.get("max_links_per_check", MAX_LINKS_PER_CHECK))
    FAST_LOAD_MS_THRESHOLD = int(settings.get("fast_load_ms_threshold", FAST_LOAD_MS_THRESHOLD))
    MAX_PAGES_TO_AUDIT = int(settings.get("max_pages_to_audit", MAX_PAGES_TO_AUDIT))
    REQUEST_THROTTLE_SECONDS = float(settings.get("request_throttle_seconds", REQUEST_THROTTLE_SECONDS))
    PARALLEL_CHECKS = bool(settings.get("parallel_checks", PARALLEL_CHECKS))
    PARALLEL_MAX_WORKERS = max(2, min(32, int(settings.get("parallel_max_workers", PARALLEL_MAX_WORKERS))))


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


def check_link_set(urls: List[str], max_to_check: int | None = None) -> Tuple[bool, str, List[str], List[str]]:
    if max_to_check is None:
        max_to_check = MAX_LINKS_PER_CHECK
    if not urls:
        return False, "No links found", [], []
    batch = urls[:max_to_check]
    status_by_link: Dict[str, int] = {}

    if PARALLEL_CHECKS and len(batch) > 1:
        workers = min(PARALLEL_MAX_WORKERS, len(batch))

        def _one(link: str) -> Tuple[str, int]:
            try:
                return link, fetch_status(link)
            except Exception:
                return link, 0

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_one, link) for link in batch]
            for fut in as_completed(futures):
                link, status = fut.result()
                status_by_link[link] = status
        for link in batch:
            status_by_link.setdefault(link, 0)
    else:
        for link in batch:
            status_by_link[link] = fetch_status(link)

    tested = 0
    failed: List[Tuple[str, int]] = []
    succeeded: List[str] = []
    for link in batch:
        status = status_by_link.get(link, 0)
        tested += 1
        if status == 0 or status >= 400:
            failed.append((link, status))
        else:
            succeeded.append(f"{link} ({status})")
    if failed:
        preview = ", ".join(f"{u} ({s})" for u, s in failed[:5])
        failure_urls = [f"{u} ({s})" for u, s in failed]
        return False, f"{len(failed)}/{tested} failed: {preview}", failure_urls, succeeded
    return True, f"Checked {tested} links, all OK", [], succeeded


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

    def path_depth(u: str) -> int:
        path = urlparse(u).path.strip("/")
        if not path:
            return 0
        return len([p for p in path.split("/") if p])

    def top_section(u: str) -> str:
        path = urlparse(u).path.strip("/")
        if not path:
            return ""
        return path.split("/")[0].lower()

    # Prioritize top-level/header nav links first.
    nav_href_matches = re.findall(r"<nav[\s\S]*?</nav>", html, flags=re.IGNORECASE)
    nav_hrefs: List[str] = []
    for block in nav_href_matches:
        nav_hrefs.extend(re.findall(r'href\s*=\s*["\']([^"\']+)["\']', block, flags=re.IGNORECASE))
    nav_candidates = normalize_links(seed_url, nav_hrefs)

    all_hrefs = re.findall(r'href\s*=\s*["\']([^"\']+)["\']', html, flags=re.IGNORECASE)
    all_candidates = normalize_links(seed_url, all_hrefs)

    # 1) Add nav links with section diversity first:
    # pick one per top-level section before taking additional nested links.
    nav_candidates = [u for u in nav_candidates if get_hostname(u) == seed_host]
    by_section: Dict[str, List[str]] = {}
    for candidate in nav_candidates:
        by_section.setdefault(top_section(candidate), []).append(candidate)

    # First pass: one URL per section.
    for section in by_section.keys():
        if len(pages) >= max_pages:
            break
        # Prefer shallower link within each section but keep source-order tie-break.
        first = min(by_section[section], key=lambda u: path_depth(u))
        if first not in pages:
            pages.append(first)

    # Second pass: remaining nav URLs (still shallow-first) to fill leftover slots.
    for section in by_section.keys():
        remaining = [u for u in by_section[section] if u != min(by_section[section], key=lambda x: path_depth(x))]
        for candidate in remaining:
            if len(pages) >= max_pages:
                break
            if candidate not in pages:
                pages.append(candidate)
        if len(pages) >= max_pages:
            break

    # 2) Fill remaining slots with other homepage links, still preferring shallower pages.
    all_candidates = sorted(all_candidates, key=lambda u: (path_depth(u), u))
    for candidate in all_candidates:
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


def fetch_html(url: str) -> str:
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
        return resp.read().decode("utf-8", errors="replace")


def check_social_links(url: str, html: str) -> Tuple[bool, str]:
    social_domains = ("facebook.com", "instagram.com", "linkedin.com", "x.com", "twitter.com", "youtube.com", "tiktok.com")
    hrefs = re.findall(r'href\s*=\s*["\']([^"\']+)["\']', html, flags=re.IGNORECASE)
    links = normalize_links(url, hrefs)
    socials = [l for l in links if any(domain in l.lower() for domain in social_domains)]
    if not socials:
        return (
            False,
            "No outbound links to major social sites were found in the audited page HTML "
            "(Facebook, Instagram, LinkedIn, X/Twitter, YouTube, TikTok).",
        )
    ok, _note, failures, _ok_urls = check_link_set(socials, max_to_check=20)
    if ok:
        return (
            True,
            f"Quick link check: {len(socials)} social URL(s) sampled and each returned a normal HTTP response. "
            "This does not prove they are the correct official accounts—only that the URLs respond.",
        )
    fail_preview = "; ".join(failures[:5])
    return (
        False,
        f"Quick link check: some social URLs did not return a clear success status. "
        f"Examples: {fail_preview}. "
        "Redirects, login walls, or bot blocking can cause false alarms—open each link in a browser to confirm.",
    )


def _social_platform(url: str) -> str:
    lower = url.lower()
    if "facebook.com" in lower:
        return "facebook"
    if "instagram.com" in lower:
        return "instagram"
    if "linkedin.com" in lower:
        return "linkedin"
    if "x.com" in lower or "twitter.com" in lower:
        return "x/twitter"
    if "youtube.com" in lower:
        return "youtube"
    if "tiktok.com" in lower:
        return "tiktok"
    return "other"


def _social_account_key(link: str) -> str:
    parsed = urlparse(link)
    path = parsed.path.strip("/").lower()
    if not path:
        return parsed.netloc.lower()
    first_segment = path.split("/")[0]
    query = parsed.query.lower()
    if first_segment == "profile.php" and query:
        return f"{first_segment}?{query}"
    return first_segment


def get_social_link_inventory(url: str, html: str) -> Tuple[List[Dict[str, str]], List[str]]:
    hrefs = re.findall(r'href\s*=\s*["\']([^"\']+)["\']', html, flags=re.IGNORECASE)
    links = normalize_links(url, hrefs)
    social_domains = ("facebook.com", "instagram.com", "linkedin.com", "x.com", "twitter.com", "youtube.com", "tiktok.com")
    socials = [l for l in links if any(domain in l.lower() for domain in social_domains)]
    inventory: List[Dict[str, str]] = []
    platform_accounts: Dict[str, set] = {}
    for link in socials:
        platform = _social_platform(link)
        account_key = _social_account_key(link)
        platform_accounts.setdefault(platform, set()).add(account_key)
        inventory.append({"platform": platform, "url": link, "account_key": account_key})
    conflicts = [p for p, accounts in platform_accounts.items() if len(accounts) > 1]
    return inventory, conflicts


def check_social_links_with_business_hint(url: str, html: str, expected_business_name: str) -> Tuple[bool, str]:
    ok, note = check_social_links(url, html)
    if not expected_business_name.strip():
        return (
            ok,
            f"{note} "
            "Optional: enter the expected business name on the main screen—"
            "the tool can then flag whether those words appear inside social URLs "
            "(hint only; not proof the profile is official).",
        )
    tokens = [t.lower() for t in re.findall(r"[a-zA-Z0-9]+", expected_business_name) if len(t) >= 4]
    hrefs = re.findall(r'href\s*=\s*["\']([^"\']+)["\']', html, flags=re.IGNORECASE)
    links = normalize_links(url, hrefs)
    social_domains = ("facebook.com", "instagram.com", "linkedin.com", "x.com", "twitter.com", "youtube.com", "tiktok.com")
    socials = [l for l in links if any(domain in l.lower() for domain in social_domains)]
    if not socials:
        return False, f"{note} (No social URLs to compare against the business name.)"
    if not tokens:
        return (
            ok,
            f"{note} The expected name has no words long enough (4+ letters) to match inside URLs automatically.",
        )
    token_matches = 0
    for link in socials:
        lower = link.lower()
        if any(token in lower for token in tokens):
            token_matches += 1
    hint = (
        f"Name hint: words from your expected business name showed up in {token_matches} of {len(socials)} "
        "social link(s). Many brands use handles that do not include the full business name—verify in the browser."
    )
    return ok, f"{note} {hint}"


def check_noindex_discouraged(url: str, html: str) -> Tuple[bool, str]:
    lower = html.lower()
    if 'name="robots"' in lower and "noindex" in lower:
        return True, "Meta robots contains noindex"
    robots_url = urljoin(url, "/robots.txt")
    try:
        req = Request(robots_url, headers={"User-Agent": USER_AGENT})
        with urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            robots = resp.read().decode("utf-8", errors="replace").lower()
        if "disallow: /" in robots:
            return True, "robots.txt contains Disallow: /"
    except Exception:
        pass
    return False, "No noindex/meta robots block found"


def extract_visible_text(html: str) -> str:
    # Remove script/style/HTML tags with lightweight regex cleanup.
    text = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.IGNORECASE)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _load_custom_spell_words(spell: Any, dictionary_path: str) -> None:
    if not dictionary_path or not os.path.isfile(dictionary_path):
        return
    try:
        with open(dictionary_path, encoding="utf-8") as f:
            for line in f:
                w = line.strip().lower()
                if w and not w.startswith("#"):
                    spell.word_frequency.add(w)
    except OSError:
        pass


def check_spelling_grammar(
    page_url_html: List[Tuple[str, str]], custom_dictionary_path: str = ""
) -> Tuple[bool, str, List[Dict[str, Any]]]:
    try:
        from spellchecker import SpellChecker  # type: ignore
    except Exception:
        return False, "Spell checker package unavailable (install pyspellchecker)", []

    try:
        spell = SpellChecker()
    except Exception as exc:
        return False, f"Spell checker dictionary unavailable: {exc}", []

    _load_custom_spell_words(spell, custom_dictionary_path)

    occurrences: List[Tuple[str, str, str]] = []
    for page_url, html in page_url_html:
        if not html:
            continue
        text = extract_visible_text(html)
        for m in re.finditer(r"\b[a-zA-Z]{4,}\b", text):
            if len(occurrences) >= 8000:
                break
            w = m.group(0).lower()
            snip = text[max(0, m.start() - 35) : min(len(text), m.end() + 35)].strip()
            snip = re.sub(r"\s+", " ", snip)
            occurrences.append((w, page_url, snip))
        if len(occurrences) >= 8000:
            break

    if len(occurrences) < 40:
        return False, "Insufficient textual content for automated check", []

    tokens = [w for w, _, _ in occurrences]
    unique_tokens = list(dict.fromkeys(tokens))
    sample = unique_tokens[:3000]
    unknown_set: Set[str] = set(spell.unknown(sample))
    error_rate = (len(unknown_set) / max(1, len(set(sample)))) * 100
    passed = error_rate <= 3.0

    by_word: Dict[str, Dict[str, Any]] = {}
    for w, page_url, snip in occurrences:
        if w not in unknown_set:
            continue
        if w not in by_word:
            by_word[w] = {"word": w, "pages": [], "snippets": []}
        entry = by_word[w]
        if page_url not in entry["pages"]:
            entry["pages"].append(page_url)
        if len(entry["snippets"]) < 4 and snip and snip not in entry["snippets"]:
            entry["snippets"].append(snip)

    issues = sorted(by_word.values(), key=lambda x: str(x["word"]))[:200]
    return passed, f"Spelling heuristic unknown-word rate={error_rate:.1f}% (threshold<=3.0%)", issues


def _analyze_one_image(img_url: str) -> Dict[str, Any] | None:
    """
    Blur-only heuristic (pass/fail):
      Grayscale → FIND_EDGES → variance of edge map. High variance ≈ lots of detail/sharp edges;
      low variance ≈ smooth or blurry imagery. Threshold 60 is coarse; low-res but sharp images
      are not penalized (no pixel-dimension checks).
    """
    try:
        from PIL import Image, ImageFilter, ImageStat  # type: ignore
        from io import BytesIO

        req = Request(img_url, headers={"User-Agent": USER_AGENT})
        with urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            data = resp.read()
        img = Image.open(BytesIO(data)).convert("L")
        w, h = img.size
        edges = img.filter(ImageFilter.FIND_EDGES)
        var = ImageStat.Stat(edges).var[0]
        bl = var < 60.0
        line = f"{img_url} (w={w}, h={h}, edge_var={var:.1f})"
        return {"line": line, "blurry": bl, "bad": bl}
    except Exception:
        return None


def check_image_quality(
    url: str, page_url_html: List[Tuple[str, str]], max_samples: int = 20
) -> Tuple[bool, str, List[str], List[str]]:
    try:
        from PIL import Image  # type: ignore  # noqa: F401
    except Exception:
        return False, "Pillow unavailable (install pillow)", [], []

    # Per-page img lists, then shuffle page order and round-robin pick URLs so samples
    # spread across different pages (better chance to catch issues sitewide).
    page_lists: List[List[str]] = []
    for _pu, html in page_url_html:
        if not html:
            continue
        srcs = re.findall(r'<img[^>]+src\s*=\s*["\']([^"\']+)["\']', html, flags=re.IGNORECASE)
        normalized = normalize_links(url, srcs)
        if normalized:
            page_lists.append(normalized)
    if not page_lists:
        return False, "No image URLs found", [], []

    order = list(range(len(page_lists)))
    random.shuffle(order)
    shuffled = [page_lists[i] for i in order]

    image_urls: List[str] = []
    seen: set[str] = set()
    round_idx = 0
    while len(image_urls) < max_samples:
        added = False
        for pl in shuffled:
            if round_idx < len(pl) and pl[round_idx] not in seen:
                seen.add(pl[round_idx])
                image_urls.append(pl[round_idx])
                added = True
                if len(image_urls) >= max_samples:
                    break
        if not added:
            break
        round_idx += 1
    if not image_urls:
        return False, "No image URLs found", [], []

    if PARALLEL_CHECKS and len(image_urls) > 1:
        workers = min(PARALLEL_MAX_WORKERS, len(image_urls))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            raw_results = list(pool.map(_analyze_one_image, image_urls))
    else:
        raw_results = [_analyze_one_image(u) for u in image_urls]

    checked = 0
    blurry = 0
    bad_urls: List[str] = []
    ok_urls: List[str] = []
    for item in raw_results:
        if not item:
            continue
        checked += 1
        if item["blurry"]:
            blurry += 1
        if item["bad"]:
            bad_urls.append(str(item["line"]))
        else:
            ok_urls.append(str(item["line"]))

    if checked == 0:
        return False, f"Could not analyze images. {MANUAL_MEDIA_VERIFY_HINT}", [], []
    passed = (blurry / checked) <= 0.4
    note = (
        f"Checked {checked} image(s) sampled across {len(page_lists)} page(s) (shuffled round-robin); "
        f"blurry (edge-variance heuristic)={blurry}/{checked}"
    )
    if not passed:
        note = f"{note}. {MANUAL_MEDIA_VERIFY_HINT}"
    return passed, note, bad_urls, ok_urls


def check_videos_load(url: str, pages_html: List[str]) -> Tuple[bool, str, List[str], List[str]]:
    srcs: List[str] = []
    for html in pages_html:
        srcs.extend(re.findall(r"<video[^>]+src\s*=\s*['\"]([^'\"]+)['\"]", html, flags=re.IGNORECASE))
        srcs.extend(re.findall(r"<source[^>]+src\s*=\s*['\"]([^'\"]+)['\"]", html, flags=re.IGNORECASE))
        iframe_srcs = re.findall(r"<iframe[^>]+src\s*=\s*['\"]([^'\"]+)['\"]", html, flags=re.IGNORECASE)
        # Keep likely video embeds; exclude maps and generic widgets.
        for iframe_src in iframe_srcs:
            lower = iframe_src.lower()
            if any(x in lower for x in ("youtube.com", "youtu.be", "vimeo.com", "wistia.com", "loom.com", "player.")):
                srcs.append(iframe_src)
    video_urls = normalize_links(url, srcs)[:20]
    if not video_urls:
        return True, "No video sources found on checked pages", [], []

    known_embed_domains = ("youtube.com", "youtu.be", "vimeo.com", "wistia.com", "loom.com")

    def _check_one_video(video_url: str) -> Tuple[str, str]:
        status = fetch_status(video_url)
        if status == 0:
            return "fail", f"{video_url} (no response)"
        if status >= 400:
            if status in (403, 405) and any(d in video_url.lower() for d in known_embed_domains):
                return "ok", f"{video_url} ({status}, embed-allowed)"
            return "fail", f"{video_url} ({status})"
        return "ok", f"{video_url} ({status})"

    if PARALLEL_CHECKS and len(video_urls) > 1:
        workers = min(PARALLEL_MAX_WORKERS, len(video_urls))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            rows = list(pool.map(_check_one_video, video_urls))
    else:
        rows = [_check_one_video(u) for u in video_urls]

    tested = len(rows)
    failures: List[str] = []
    ok_urls: List[str] = []
    for kind, line in rows:
        if kind == "ok":
            ok_urls.append(line)
        else:
            failures.append(line)
    if failures:
        note = (
            f"{len(failures)}/{tested} video source checks failed. "
            f"Examples: {', '.join(failures[:5])}. {MANUAL_MEDIA_VERIFY_HINT}"
        )
        return False, note, failures, ok_urls
    return True, f"Checked {tested} video source URL(s), all reachable/allowed", [], ok_urls


def check_business_name(pages_html: List[str], expected_business_name: str) -> Tuple[bool, str]:
    expected = expected_business_name.strip()
    if not expected:
        return False, "No expected business name provided"
    needle = expected.lower()
    hits = 0
    for html in pages_html:
        text = extract_visible_text(html).lower()
        if needle in text:
            hits += 1
    passed = hits >= 1
    return passed, f"Exact name match found on {hits}/{len(pages_html)} checked page(s)"


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
            links_ok_urls=[],
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

    links_ok, links_note, links_failed, links_ok_urls = check_link_set(all_links)
    footer_ok, footer_note, footer_failed, _footer_ok_urls = check_link_set(footer_links)

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
        links_ok_urls=links_ok_urls,
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


def run_device_audits(
    urls: List[str], on_audit_complete=None
) -> Tuple[Dict[str, MultiPageDeviceAudit], Dict[str, List[DeviceAudit]]]:
    configure_playwright_env_for_bundle()
    by_profile: Dict[str, List[DeviceAudit]] = {p.name: [] for p in DEVICE_PROFILES}

    def synthesize_browser_unavailable(reason: str) -> Tuple[Dict[str, MultiPageDeviceAudit], Dict[str, List[DeviceAudit]]]:
        for profile in DEVICE_PROFILES:
            for url in urls:
                by_profile[profile.name].append(
                    DeviceAudit(
                        name=profile.name,
                        page_url=url,
                        load_ms=TIMEOUT_SECONDS * 1000,
                        nav_ok=False,
                        links_ok=False,
                        links_note=f"Manual (browser unavailable: {reason})",
                        links_failed=[f"{url} (browser unavailable)"],
                        links_ok_urls=[],
                        phone_ok=False,
                        phone_note=f"Manual (browser unavailable: {reason})",
                        footer_ok=False,
                        footer_note=f"Manual (browser unavailable: {reason})",
                        footer_failed=[f"{url} (browser unavailable)"],
                    )
                )
                if on_audit_complete:
                    on_audit_complete()
        merged_local: Dict[str, MultiPageDeviceAudit] = {}
        for profile in DEVICE_PROFILES:
            merged_local[profile.name] = combine_device_audits(profile.name, by_profile[profile.name])
        return merged_local, by_profile

    with sync_playwright() as p:
        try:
            browser: Browser = p.chromium.launch(headless=True)
        except Exception as first_exc:
            # First-run fallback: install Chromium for current user, then retry once.
            try:
                install_playwright_chromium(timeout_s=300)
                browser = p.chromium.launch(headless=True)
            except Exception:
                return synthesize_browser_unavailable(str(first_exc))
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
    return merged, by_profile


def is_chromium_available() -> bool:
    configure_playwright_env_for_bundle()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            browser.close()
        return True
    except Exception:
        return False


def install_playwright_chromium(timeout_s: int = 600) -> None:
    """
    Install Playwright Chromium using bundled driver when possible.
    Works better in frozen/packaged apps than `python -m playwright`.
    """
    configure_playwright_env_for_bundle()
    try:
        from playwright._impl._driver import compute_driver_executable  # type: ignore

        driver_exe, cli_path = compute_driver_executable()
        subprocess.run([driver_exe, cli_path, "install", "chromium"], check=True, timeout=timeout_s)
        return
    except Exception as first_exc:
        if getattr(sys, "frozen", False):
            raise RuntimeError(
                "Could not install Chromium from the packaged Playwright driver. "
                "Try rebuilding the app with browsers bundled, or reinstall the application."
            ) from first_exc
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True, timeout=timeout_s)


def _frozen_playwright_user_browsers_dir() -> str:
    """Writable location for Chromium when the .app bundle has no bundled browsers."""
    if sys.platform == "darwin":
        base = os.path.join(os.path.expanduser("~"), "Library", "Application Support", "AutoWebsiteChecker")
    elif sys.platform == "win32":
        base = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "AutoWebsiteChecker")
    else:
        base = os.path.join(os.path.expanduser("~"), ".local", "share", "AutoWebsiteChecker")
    return os.path.join(base, "ms-playwright")


def configure_playwright_env_for_bundle() -> None:
    """
    If running from a packaged app, force Playwright to use bundled browsers
    from an app-local `ms-playwright` directory.
    """
    # Playwright treats "0" as "default cache"; do not let it block bundle detection in frozen apps.
    raw_pw = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
    if raw_pw.strip() and raw_pw.strip() != "0":
        return
    candidates: List[str] = []
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        candidates.append(os.path.join(exe_dir, "ms-playwright"))
        # macOS .app: executable is in Contents/MacOS, resources in Contents/Resources
        candidates.append(os.path.abspath(os.path.join(exe_dir, "..", "Resources", "ms-playwright")))
    else:
        candidates.append(os.path.join(os.path.dirname(__file__), "ms-playwright"))
    for path in candidates:
        if os.path.isdir(path):
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = path
            return
    if getattr(sys, "frozen", False):
        user_path = _frozen_playwright_user_browsers_dir()
        os.makedirs(user_path, exist_ok=True)
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = user_path


def build_results(
    url: str,
    max_pages: int = MAX_PAGES_TO_AUDIT,
    on_row=None,
    on_status=None,
    on_social_links=None,
    on_pages_checked=None,
    on_spelling_issues=None,
    on_row_details=None,
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

    enabled_rows = ((settings or {}).get("enabled_rows") or {})

    def row_enabled(row_key: str) -> bool:
        if not enabled_rows:
            return True
        return bool(enabled_rows.get(row_key, True))

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
    if on_pages_checked:
        on_pages_checked(pages)
    selected_rows = sum(1 for key, _ in QA_ROW_OPTIONS if row_enabled(key))
    progress_steps = 1 + (len(pages) * len(DEVICE_PROFILES)) + selected_rows
    init_progress(progress_steps)
    step_progress()
    emit_status(f"Auditing {len(pages)} page(s) across desktop/mobile/tablet...")
    audits, raw_audits = run_device_audits(pages, on_audit_complete=step_progress)

    desktop = audits["desktop"]
    mobile = audits["mobile"]
    tablet = audits["tablet"]
    browser_unavailable = "browser unavailable" in desktop.links_note.lower()
    is_wp, wp_note = detect_wordpress(url)

    def _fetch_pair(page_url: str) -> Tuple[str, str]:
        try:
            return page_url, fetch_html(page_url)
        except Exception:
            return page_url, ""

    if PARALLEL_CHECKS and len(pages) > 1:
        fw = min(PARALLEL_MAX_WORKERS, len(pages))
        with ThreadPoolExecutor(max_workers=fw) as pool:
            pairs = list(pool.map(_fetch_pair, pages))
    else:
        pairs = [_fetch_pair(p) for p in pages]

    page_url_html = [(u, h) for u, h in pairs if h]
    pages_html = [h for _, h in page_url_html]
    combined_social_html = "\n".join(pages_html) if pages_html else ""

    spell_dict_path = str((settings or {}).get("custom_spell_dictionary_path", "")).strip()
    if not spell_dict_path:
        spell_dict_path = os.path.join(os.path.expanduser("~"), ".auto_website_checker", "custom_spell_words.txt")
    expected_business_name = str((settings or {}).get("expected_business_name", "")).strip()
    social_ok, social_note = (False, "Skipped by config")
    if row_enabled("social_links"):
        social_ok, social_note = (
            check_social_links_with_business_hint(url, combined_social_html, expected_business_name)
            if combined_social_html
            else (False, "Unable to fetch page HTML")
        )
    social_inventory, social_conflicts = (
        get_social_link_inventory(url, combined_social_html) if combined_social_html else ([], [])
    )
    if on_social_links:
        on_social_links(social_inventory, social_conflicts)
    spelling_issues: List[Any] = []
    spell_ok, spell_note = False, "Skipped by config"
    if row_enabled("spelling_grammar"):
        spell_ok, spell_note, spelling_issues = check_spelling_grammar(page_url_html, spell_dict_path)
        if on_spelling_issues:
            on_spelling_issues(spelling_issues)
    img_ok, img_note, image_bad, image_ok = (False, "Skipped by config", [], [])
    if row_enabled("images_quality"):
        img_ok, img_note, image_bad, image_ok = check_image_quality(url, page_url_html)
    video_ok, video_note, video_bad, video_ok_urls = (False, "Skipped by config", [], [])
    if row_enabled("videos_load"):
        video_ok, video_note, video_bad, video_ok_urls = check_videos_load(url, pages_html)
    name_ok, name_note = (False, "Skipped by config")
    if row_enabled("business_name"):
        name_ok, name_note = check_business_name(pages_html, expected_business_name)

    rows: List[CheckResult] = []

    if row_enabled("speed_snappy"):
        emit_row(
        CheckResult(
            component="Fast website/page load speed (Does it feel fast/snappy?)",
            yes_no=("TBD" if browser_unavailable else yn(
                desktop.avg_load_ms <= FAST_LOAD_MS_THRESHOLD
                and mobile.avg_load_ms <= FAST_LOAD_MS_THRESHOLD
                and tablet.avg_load_ms <= FAST_LOAD_MS_THRESHOLD
            )),
            desktop=("Manual" if browser_unavailable else pf(desktop.avg_load_ms <= FAST_LOAD_MS_THRESHOLD)),
            mobile=("Manual" if browser_unavailable else pf(mobile.avg_load_ms <= FAST_LOAD_MS_THRESHOLD)),
            tablet=("Manual" if browser_unavailable else pf(tablet.avg_load_ms <= FAST_LOAD_MS_THRESHOLD)),
            notes=(
                f"Avg load ms across {len(pages)} page(s): "
                f"desktop={desktop.avg_load_ms}, mobile={mobile.avg_load_ms}, tablet={tablet.avg_load_ms}"
            ),
        ),
        rows,
        )
        step_progress()
    if row_enabled("nav_responsive"):
        emit_row(
        CheckResult(
            component="Navigation bar functionality - responsive menu bar",
            yes_no=("TBD" if browser_unavailable else yn(desktop.nav_ok and mobile.nav_ok and tablet.nav_ok)),
            desktop=("Manual" if browser_unavailable else pf(desktop.nav_ok)),
            mobile=("Manual" if browser_unavailable else pf(mobile.nav_ok)),
            tablet=("Manual" if browser_unavailable else pf(tablet.nav_ok)),
            notes=f"D:{desktop.nav_note} | M:{mobile.nav_note} | T:{tablet.nav_note}",
        ),
        rows,
        )
        step_progress()
    if row_enabled("links_buttons"):
        emit_row(
        CheckResult(
            component="Working links & buttons",
            yes_no=("TBD" if browser_unavailable else yn(desktop.links_ok and mobile.links_ok and tablet.links_ok)),
            desktop=("Manual" if browser_unavailable else pf(desktop.links_ok)),
            mobile=("Manual" if browser_unavailable else pf(mobile.links_ok)),
            tablet=("Manual" if browser_unavailable else pf(tablet.links_ok)),
            notes=f"D:{desktop.links_note} | M:{mobile.links_note} | T:{tablet.links_note}",
        ),
        rows,
        )
        step_progress()
    if row_enabled("phone_in_header"):
        emit_row(
        CheckResult(
            component="Phone Number Present in Head (NOT Only Book Online)",
            yes_no=("TBD" if browser_unavailable else yn(desktop.phone_ok and mobile.phone_ok and tablet.phone_ok)),
            desktop=("Manual" if browser_unavailable else pf(desktop.phone_ok)),
            mobile=("Manual" if browser_unavailable else pf(mobile.phone_ok)),
            tablet=("Manual" if browser_unavailable else pf(tablet.phone_ok)),
            notes=f"D:{desktop.phone_note} | M:{mobile.phone_note} | T:{tablet.phone_note}",
        ),
        rows,
        )
        step_progress()
    if row_enabled("footer_links"):
        emit_row(
        CheckResult(
            component="Footer functionality - working links",
            yes_no=("TBD" if browser_unavailable else yn(desktop.footer_ok and mobile.footer_ok and tablet.footer_ok)),
            desktop=("Manual" if browser_unavailable else pf(desktop.footer_ok)),
            mobile=("Manual" if browser_unavailable else pf(mobile.footer_ok)),
            tablet=("Manual" if browser_unavailable else pf(tablet.footer_ok)),
            notes=f"D:{desktop.footer_note} | M:{mobile.footer_note} | T:{tablet.footer_note}",
        ),
        rows,
        )
        step_progress()
    if row_enabled("spelling_grammar"):
        emit_row(
        CheckResult(
            component="Correct spelling & grammar, no typos",
            yes_no=yn(spell_ok),
            desktop=pf(spell_ok),
            mobile=pf(spell_ok),
            tablet=pf(spell_ok),
            notes=spell_note,
        ),
        rows,
        )
        step_progress()
    if row_enabled("images_quality"):
        emit_row(
        CheckResult(
            component="Images are compressed, high resolution, not blurry or pixelated",
            yes_no=yn(img_ok),
            desktop=pf(img_ok),
            mobile=pf(img_ok),
            tablet=pf(img_ok),
            notes=f"Heuristic image-quality check. {img_note}",
        ),
        rows,
        )
        step_progress()
    if row_enabled("videos_load"):
        emit_row(
        CheckResult(
            component="Videos load correctly",
            yes_no=yn(video_ok),
            desktop=pf(video_ok),
            mobile=pf(video_ok),
            tablet=pf(video_ok),
            notes=f"Source-reachability check. {video_note}",
        ),
        rows,
        )
        step_progress()
    if row_enabled("social_links"):
        social_na = (
            not social_inventory
            and bool(combined_social_html)
            and "Unable to fetch" not in social_note
        )
        emit_row(
            CheckResult(
                component="Social media links out to correct pages",
                yes_no=("N/A" if social_na else yn(social_ok)),
                desktop=("N/A" if social_na else pf(social_ok)),
                mobile=("N/A" if social_na else pf(social_ok)),
                tablet=("N/A" if social_na else pf(social_ok)),
                notes=(
                    f"{social_note}"
                    + (
                        f" (Conflict warning: more than one handle found for the same platform type: "
                        f"{', '.join(social_conflicts)}.)"
                        if social_conflicts
                        else ""
                    )
                ),
            ),
            rows,
        )
        step_progress()
    if row_enabled("business_name"):
        emit_row(
        CheckResult(
            component="Using correct business name",
            yes_no=yn(name_ok),
            desktop=pf(name_ok),
            mobile=pf(name_ok),
            tablet=pf(name_ok),
            notes=name_note,
        ),
        rows,
        )
        step_progress()
    if row_enabled("rise_compat"):
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
    emit_status("Finalizing results...")
    if on_row_details:

        def _spell_detail_line(it: Any) -> str:
            if not isinstance(it, dict):
                return str(it)
            w = str(it.get("word", ""))
            pg = it.get("pages") or []
            sn = it.get("snippets") or []
            pages_s = ", ".join(str(p) for p in pg[:5])
            if sn:
                return f"{w} — pages: {pages_s} — e.g. «{sn[0]}»"
            return f"{w} — pages: {pages_s}"

        working_links_bad: List[str] = []
        working_links_ok: List[str] = []
        slow_pages: List[str] = []
        for device, audits_for_device in raw_audits.items():
            for a in audits_for_device:
                for bad in a.links_failed[:20]:
                    working_links_bad.append(f"{device}: {a.page_url} -> {bad}")
                for good in a.links_ok_urls[:20]:
                    working_links_ok.append(f"{device}: {a.page_url} -> {good}")
                if a.load_ms > FAST_LOAD_MS_THRESHOLD:
                    slow_pages.append(f"{device}: {a.page_url} ({a.load_ms}ms)")
        details = {
            "Fast website/page load speed (Does it feel fast/snappy?)": {
                "problematic": slow_pages,
                "ok": [f"Threshold={FAST_LOAD_MS_THRESHOLD}ms"],
            },
            "Working links & buttons": {
                "problematic": working_links_bad,
                "ok": working_links_ok,
            },
            "Social media links out to correct pages": {
                "problematic": [f"conflict: {c}" for c in social_conflicts],
                "ok": [f"[{i['platform']}] {i['url']}" for i in social_inventory],
            },
            "Pages checked": {
                "problematic": [],
                "ok": pages,
            },
            "Correct spelling & grammar, no typos": {
                "problematic": [_spell_detail_line(it) for it in spelling_issues[:200]],
                "ok": [],
            },
            "Images are compressed, high resolution, not blurry or pixelated": {
                "problematic": image_bad[:100],
                "ok": image_ok[:100],
            },
            "Videos load correctly": {
                "problematic": video_bad[:100],
                "ok": video_ok_urls[:100],
            },
        }
        on_row_details(details)
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
