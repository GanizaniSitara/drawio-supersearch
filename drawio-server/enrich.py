"""
Confluence Enricher for DrawIO Diagram Server

Detects C4 Level 1-like diagrams (application/system context), extracts
the application/system names from shapes, searches Confluence for matching
pages, and synthesizes short "about" blurbs for each application.

This is the bridge between your org's Confluence knowledge base and the
static diagram viewer — giving every architecture box a one-liner
description pulled from the real documentation.

Usage (standalone):
    python enrich.py /path/to/diagrams --confluence-url https://confluence.example.com

Usage (from serve.py):
    from enrich import enrich_diagrams
    enrichments = enrich_diagrams(diagrams, confluence_url=..., auth=...)
"""

import os
import re
import sys
import json
import time
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Add parent for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

try:
    from lxml import etree
except ImportError:
    import xml.etree.ElementTree as etree

try:
    from extractor.drawio_tools import decode_diagram_data
except ImportError:
    decode_diagram_data = None

# C4 L1 detection heuristics
C4_L1_INDICATORS = {
    # Shape style patterns that suggest C4 context-level boxes
    "c4_system", "c4_person", "c4_container", "c4_component",
    "rounded=1", "whiteSpace=wrap",
}

C4_NAME_PATTERNS = [
    # Filenames/titles that suggest C4 context diagrams
    r"c4.*context",
    r"c4.*l1",
    r"c4.*level.?1",
    r"system.?context",
    r"landscape",
    r"architecture.?overview",
    r"application.?architecture",
    r"system.?architecture",
    r"high.?level.?architecture",
    r"tech.?landscape",
]

# Keywords in shape text that suggest this is a system/application box
# (not a person, not a relationship label, not a boundary)
APPLICATION_KEYWORDS = {
    "system", "service", "application", "platform", "api", "app",
    "database", "db", "queue", "cache", "gateway", "proxy",
    "microservice", "backend", "frontend", "portal", "engine",
    "server", "cluster", "registry", "broker", "store",
}

# Exclude these — they're typically labels, boundaries, or decoration
EXCLUDE_PATTERNS = [
    r"^\[.*\]$",          # [External System] style labels
    r"^<",                # HTML fragments
    r"^https?://",        # URLs
    r"^\d+$",             # Pure numbers
    r"^(yes|no|true|false)$",  # Boolean labels
]


def is_c4_l1_diagram(diagram):
    """
    Heuristic: does this diagram look like a C4 Level 1 (System Context)?

    Checks the diagram name, text content, and structure.
    Returns (is_c4, confidence) tuple.
    """
    name = diagram.get("name", "").lower()
    text = diagram.get("text_content", "").lower()

    # Check filename patterns
    for pattern in C4_NAME_PATTERNS:
        if re.search(pattern, name, re.IGNORECASE):
            return True, 0.9

    # Check if it has C4 style markers
    if "c4" in name:
        return True, 0.8

    # Heuristic: if classified as "application" type with enough systems
    # This catches diagrams that aren't named C4 but show app architecture
    app_keywords_found = sum(1 for kw in APPLICATION_KEYWORDS if kw in text)
    if app_keywords_found >= 3:
        # Likely an application architecture diagram
        return True, 0.6

    return False, 0.0


def extract_application_names(drawio_path):
    """
    Extract application/system names from a .drawio file.

    Looks for shapes that represent systems or applications (not labels,
    arrows, or decoration). Returns a list of cleaned application names.
    """
    try:
        tree = etree.parse(drawio_path)
        root = tree.getroot()
    except Exception:
        return []

    candidates = []

    for diagram_el in root.findall(".//diagram"):
        # Handle both compressed and uncompressed content
        cells = []

        # Uncompressed: direct mxGraphModel children
        for cell in diagram_el.findall(".//mxCell"):
            cells.append(cell)

        # Compressed: decode and parse
        content = diagram_el.text
        if content and content.strip() and decode_diagram_data:
            try:
                decoded = decode_diagram_data(content.strip())
                if decoded:
                    inner = etree.fromstring(decoded.encode("utf-8"))
                    for cell in inner.findall(".//mxCell"):
                        cells.append(cell)
            except Exception:
                pass

        for cell in cells:
            value = cell.get("value", "").strip()
            style = cell.get("style", "")

            if not value:
                continue

            # Skip edges (relationships/arrows)
            if cell.get("edge") == "1":
                continue

            # Strip HTML tags
            clean = re.sub(r"<[^>]+>", " ", value)
            clean = re.sub(r"&[a-z]+;", " ", clean)
            clean = re.sub(r"\s+", " ", clean).strip()

            if not clean or len(clean) < 2 or len(clean) > 80:
                continue

            # Skip excluded patterns
            skip = False
            for pattern in EXCLUDE_PATTERNS:
                if re.match(pattern, clean, re.IGNORECASE):
                    skip = True
                    break
            if skip:
                continue

            # Prefer shapes that look like system boxes
            is_vertex = cell.get("vertex") == "1"
            has_parent = cell.get("parent", "") not in ("", "0", "1")

            # C4-style shapes
            is_c4_shape = any(ind in style for ind in [
                "shape=mxgraph.c4", "c4.shape", "rounded=1",
            ])

            # Regular boxes with substantial text
            is_box = is_vertex and ("rounded=" in style or "rect" in style
                                     or "shape=" in style or not style)

            if is_c4_shape or (is_box and is_vertex):
                # Extract just the main name (first line if multi-line)
                lines = clean.split("\n")
                main_name = lines[0].strip()

                # Further clean: remove description suffixes like "[Software System]"
                main_name = re.sub(r"\s*\[.*?\]\s*$", "", main_name).strip()

                if main_name and len(main_name) >= 2:
                    candidates.append({
                        "name": main_name,
                        "description": " ".join(lines[1:]).strip() if len(lines) > 1 else "",
                        "is_c4": is_c4_shape,
                        "style": style[:100],
                    })

    # Deduplicate by name (case-insensitive)
    seen = set()
    unique = []
    for c in candidates:
        key = c["name"].lower()
        if key not in seen:
            seen.add(key)
            unique.append(c)

    return unique


# =============================================================================
# Confluence search & content extraction
# =============================================================================

class ConfluenceClient:
    """Minimal Confluence REST API client for content enrichment."""

    def __init__(self, base_url, username=None, password=None, token=None,
                 rate_limit=3):
        self.base_url = base_url.rstrip("/")
        self.rate_limit = rate_limit
        self._last_request = 0

        if token:
            # Cloud (API token or PAT)
            self.headers = {"Authorization": f"Bearer {token}"}
            self.auth = None
        elif username and password:
            # Data Center / Server (Basic Auth)
            self.auth = (username, password)
            self.headers = {}
        else:
            raise ValueError("Must provide either (username, password) or token")

    def _get(self, endpoint, params=None):
        """Rate-limited GET request."""
        elapsed = time.time() - self._last_request
        min_interval = 1.0 / self.rate_limit
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)

        self._last_request = time.time()
        url = f"{self.base_url}{endpoint}"

        try:
            resp = requests.get(url, auth=self.auth, headers=self.headers,
                                params=params, verify=False, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.warning(f"Confluence request failed: {url} — {e}")
            return None

    def search_pages(self, query, space_key=None, limit=5):
        """
        Search Confluence for pages matching a query.

        Returns list of {id, title, space, url, excerpt}.
        """
        cql_parts = [f'type=page AND title~"{query}"']
        if space_key:
            cql_parts.append(f'space="{space_key}"')
        cql = " AND ".join(cql_parts)

        data = self._get("/rest/api/content/search", params={
            "cql": cql,
            "limit": limit,
            "expand": "metadata.labels,space",
        })

        if not data:
            return []

        results = []
        for r in data.get("results", []):
            results.append({
                "id": r.get("id"),
                "title": r.get("title", ""),
                "space": r.get("space", {}).get("key", ""),
                "url": r.get("_links", {}).get("webui", ""),
                "type": r.get("type", "page"),
            })
        return results

    def get_page_body(self, page_id, format="view"):
        """
        Get the rendered or storage body of a Confluence page.

        format: "view" (rendered HTML), "storage" (storage format)
        Returns the body text (HTML stripped for view format).
        """
        data = self._get(f"/rest/api/content/{page_id}", params={
            "expand": f"body.{format}",
        })

        if not data:
            return ""

        body_html = data.get("body", {}).get(format, {}).get("value", "")

        # Strip HTML to get plain text
        text = re.sub(r"<[^>]+>", " ", body_html)
        text = re.sub(r"&nbsp;", " ", text)
        text = re.sub(r"&[a-z]+;", " ", text)
        text = re.sub(r"\s+", " ", text).strip()

        return text


def _synthesize_about(page_title, page_text, app_name):
    """
    Extract a short "about" blurb from a Confluence page body.

    Takes the first meaningful paragraph (1-3 sentences) that describes
    the application. Prefers text near the app name or at the top.
    """
    if not page_text:
        return ""

    # Split into sentences
    sentences = re.split(r'(?<=[.!?])\s+', page_text)
    if not sentences:
        return ""

    # Try to find sentences that mention the app name
    name_lower = app_name.lower()
    relevant = []
    for s in sentences:
        if name_lower in s.lower() or any(
            w in s.lower() for w in name_lower.split()
            if len(w) > 3
        ):
            relevant.append(s)
            if len(relevant) >= 2:
                break

    # Fall back to first 2 sentences
    if not relevant:
        relevant = sentences[:2]

    about = " ".join(relevant)

    # Trim to reasonable length
    if len(about) > 300:
        about = about[:297] + "..."

    return about


# =============================================================================
# Main enrichment pipeline
# =============================================================================

def enrich_diagrams(diagrams, confluence_url=None, username=None, password=None,
                    token=None, space_key=None):
    """
    Enrich diagrams with "about" descriptions from Confluence.

    For each diagram that looks like C4 Level 1, extracts application names
    and searches Confluence for matching pages to build about blurbs.

    Args:
        diagrams: List of diagram dicts from scan_diagrams()
        confluence_url: Confluence base URL
        username: For Data Center/Server basic auth
        password: For Data Center/Server basic auth
        token: For Cloud (PAT / API token)
        space_key: Optional — limit Confluence search to one space

    Returns:
        dict mapping diagram name -> {
            "is_c4": bool,
            "applications": [
                {"name": str, "about": str, "confluence_url": str, ...}
            ]
        }
    """
    if not REQUESTS_AVAILABLE:
        logger.warning("requests library not installed — skipping Confluence enrichment")
        return {}

    if not confluence_url:
        logger.info("No Confluence URL configured — skipping enrichment")
        return {}

    try:
        client = ConfluenceClient(
            base_url=confluence_url,
            username=username,
            password=password,
            token=token,
        )
    except ValueError as e:
        logger.warning(f"Cannot create Confluence client: {e}")
        return {}

    enrichments = {}
    apps_cache = {}  # cache: app_name_lower -> about info

    for diagram in diagrams:
        is_c4, confidence = is_c4_l1_diagram(diagram)
        if not is_c4:
            continue

        logger.info(f"C4 L1 detected: {diagram['name']} (confidence={confidence:.1f})")

        # Extract application names from the .drawio file
        apps = extract_application_names(diagram["path"])
        if not apps:
            continue

        logger.info(f"  Found {len(apps)} applications: {[a['name'] for a in apps]}")

        enriched_apps = []
        for app in apps:
            app_key = app["name"].lower()

            # Check cache first
            if app_key in apps_cache:
                enriched_apps.append(apps_cache[app_key])
                continue

            # Search Confluence for this application
            results = client.search_pages(
                app["name"],
                space_key=space_key,
                limit=3,
            )

            about = ""
            conf_url = ""
            conf_title = ""

            if results:
                # Pick the best match (exact title match preferred)
                best = None
                for r in results:
                    if r["title"].lower() == app_key:
                        best = r
                        break
                if not best:
                    best = results[0]

                # Fetch page body and synthesize about
                page_text = client.get_page_body(best["id"])
                about = _synthesize_about(best["title"], page_text, app["name"])
                conf_url = best.get("url", "")
                conf_title = best.get("title", "")

            entry = {
                "name": app["name"],
                "about": about or app.get("description", ""),
                "confluence_url": conf_url,
                "confluence_title": conf_title,
                "is_c4_shape": app.get("is_c4", False),
            }
            apps_cache[app_key] = entry
            enriched_apps.append(entry)

        if enriched_apps:
            enrichments[diagram["name"]] = {
                "is_c4": True,
                "confidence": confidence,
                "applications": enriched_apps,
            }

    logger.info(f"Enriched {len(enrichments)} diagrams with Confluence content")
    return enrichments


def enrich_from_settings(diagrams):
    """
    Enrich using settings.ini credentials (convenience wrapper).

    Falls back gracefully if settings.ini doesn't exist or Confluence
    is not configured.
    """
    try:
        from extractor.config import load_settings
        settings = load_settings()
    except Exception:
        return {}

    url = settings.get("confluence_url", "")
    if not url:
        return {}

    return enrich_diagrams(
        diagrams,
        confluence_url=url,
        username=settings.get("confluence_username", ""),
        password=settings.get("confluence_password", ""),
    )


# =============================================================================
# CLI for standalone testing
# =============================================================================

if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(
        description="Detect C4 L1 diagrams and enrich with Confluence content"
    )
    parser.add_argument("diagrams_dir", help="Directory of .drawio files")
    parser.add_argument("--confluence-url", help="Confluence base URL")
    parser.add_argument("--username", help="Confluence username")
    parser.add_argument("--password", help="Confluence password")
    parser.add_argument("--token", help="Confluence API token (Cloud)")
    parser.add_argument("--space", help="Limit search to this space key")
    parser.add_argument("--detect-only", action="store_true",
                        help="Just detect C4 diagrams, don't call Confluence")

    args = parser.parse_args()

    # Import scan_diagrams from serve.py
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from serve import scan_diagrams

    diagrams = scan_diagrams(args.diagrams_dir)
    print(f"Scanned {len(diagrams)} diagrams\n")

    # Detection pass
    for d in diagrams:
        is_c4, conf = is_c4_l1_diagram(d)
        if is_c4:
            print(f"  C4 L1: {d['name']} (confidence={conf:.1f})")
            apps = extract_application_names(d["path"])
            for a in apps:
                marker = " [C4]" if a["is_c4"] else ""
                print(f"    -> {a['name']}{marker}")
                if a["description"]:
                    print(f"       {a['description'][:80]}")

    if args.detect_only:
        sys.exit(0)

    # Enrichment pass
    if args.confluence_url:
        enrichments = enrich_diagrams(
            diagrams,
            confluence_url=args.confluence_url,
            username=args.username,
            password=args.password,
            token=args.token,
            space_key=args.space,
        )

        print(f"\nEnriched {len(enrichments)} diagrams:")
        for name, data in enrichments.items():
            print(f"\n  {name}:")
            for app in data["applications"]:
                print(f"    {app['name']}: {app['about'][:100] or '(no about)'}")
                if app["confluence_url"]:
                    print(f"      -> {app['confluence_url']}")
    else:
        # Try settings.ini
        enrichments = enrich_from_settings(diagrams)
        if enrichments:
            print(f"\nEnriched {len(enrichments)} diagrams from settings.ini")
            for name, data in enrichments.items():
                print(f"\n  {name}:")
                for app in data["applications"]:
                    print(f"    {app['name']}: {app['about'][:100] or '(no about)'}")
        else:
            print("\nNo enrichment (pass --confluence-url or configure settings.ini)")
