"""
Lucidchart Screenshot Extractor

Uses Playwright to screenshot Lucidchart diagrams embedded in Confluence pages.
Creates PNG images compatible with DrawIO SuperSearch browser.

This is a "hydration" script - it captures Lucidchart diagrams as static images
to demonstrate the DrawIO SuperSearch experience before actual migration.

Requirements:
    pip install playwright
    playwright install chromium

Usage:
    python -m extractor.lucidchart_screenshotter --spaces SPACE1,SPACE2
    python -m extractor.lucidchart_screenshotter --test  # First 5 pages only
    python -m extractor.lucidchart_screenshotter --dry-run
"""

import os
import re
import json
import time
import hashlib
import argparse
import sys
import logging

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# Check for playwright before importing
try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    sync_playwright = None
    PlaywrightTimeout = Exception

import requests
from urllib3.exceptions import InsecureRequestWarning
from .config import Settings


def check_playwright_installed():
    """Check if Playwright is installed and provide install instructions if not."""
    if not PLAYWRIGHT_AVAILABLE:
        print("=" * 60)
        print("ERROR: Playwright is not installed")
        print("=" * 60)
        print()
        print("This script requires Playwright for browser automation.")
        print("To install, run:")
        print()
        print("    pip install playwright")
        print("    playwright install chromium")
        print()
        print("Note: 'playwright install chromium' downloads the browser")
        print("      (about 150MB, only needed once)")
        print("=" * 60)
        sys.exit(1)

# Suppress SSL warnings
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)


class LucidchartScreenshotter:
    """Screenshot Lucidchart diagrams from Confluence using Playwright."""

    def __init__(self, settings=None):
        """Initialize with settings."""
        if settings is None:
            settings = Settings.get()
        self.settings = settings

        self.confluence_url = settings['confluence_url'].rstrip('/')
        self.auth = (settings['confluence_username'], settings['confluence_password'])
        self.content_dir = settings['content_directory']
        self.rate_limit = settings['rate_limit']
        self.skip_personal = settings['skip_personal_spaces']

        self._last_request_time = 0
        self._browser = None
        self._context = None
        self._page = None

    def _rate_limited_request(self, url):
        """Make a rate-limited API request."""
        elapsed = time.time() - self._last_request_time
        min_interval = 1.0 / self.rate_limit
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)

        self._last_request_time = time.time()
        return requests.get(url, auth=self.auth, verify=False)

    def _load_stopwords(self):
        """Load stopwords from file if it exists."""
        stopwords_path = os.path.join(os.path.dirname(__file__), 'stopwords.txt')
        if os.path.exists(stopwords_path):
            with open(stopwords_path, 'r', encoding='utf-8') as f:
                return set(line.strip().lower() for line in f if line.strip() and not line.startswith('#'))
        # Default common stopwords
        return {
            'a', 'an', 'and', 'are', 'as', 'at', 'be', 'by', 'for', 'from',
            'has', 'he', 'in', 'is', 'it', 'its', 'of', 'on', 'or', 'that',
            'the', 'to', 'was', 'were', 'will', 'with', 'this', 'but', 'they',
            'have', 'had', 'what', 'when', 'where', 'who', 'which', 'why', 'how',
            'all', 'each', 'every', 'both', 'few', 'more', 'most', 'other',
            'some', 'such', 'no', 'nor', 'not', 'only', 'own', 'same', 'so',
            'than', 'too', 'very', 'can', 'just', 'should', 'now', 'also',
            'your', 'our', 'their', 'my', 'his', 'her', 'we', 'you', 'i', 'me',
            'nbsp', 'amp', 'quot', 'lt', 'gt', 'br', 'div', 'span', 'class', 'id',
            'href', 'src', 'style', 'http', 'https', 'www', 'com', 'org', 'net',
            'page', 'content', 'confluence', 'wiki', 'display', 'spaces',
        }

    def _extract_text_from_html(self, html):
        """Extract plain text from HTML, filtering stopwords for search indexing."""
        if not html:
            return ''

        # Remove script and style elements
        html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)

        # Remove HTML tags
        text = re.sub(r'<[^>]+>', ' ', html)

        # Decode HTML entities
        text = text.replace('&nbsp;', ' ')
        text = text.replace('&amp;', '&')
        text = text.replace('&lt;', '<')
        text = text.replace('&gt;', '>')
        text = text.replace('&quot;', '"')

        # Normalize whitespace
        text = re.sub(r'\s+', ' ', text).strip()

        # Filter stopwords for better search indexing
        stopwords = self._load_stopwords()
        words = text.split()
        filtered_words = [w for w in words if w.lower() not in stopwords and len(w) > 2]

        return ' '.join(filtered_words)

    def _extract_lucidchart_names(self, storage_xml):
        """
        Extract Lucidchart diagram names from Confluence storage format.

        Lucidchart macros in storage format look like:
        <ac:structured-macro ac:name="lucidchart" ...>
          <ac:parameter ac:name="documentName">My Diagram Name</ac:parameter>
          ...
        </ac:structured-macro>

        Returns list of diagram names found (in order of appearance).
        """
        if not storage_xml:
            return []

        names = []

        # Find all lucidchart macros and their documentName parameters
        # Pattern matches: <ac:parameter ac:name="documentName">NAME</ac:parameter>
        # Only within lucidchart macro context
        macro_pattern = r'<ac:structured-macro[^>]*ac:name="lucidchart"[^>]*>(.*?)</ac:structured-macro>'
        param_pattern = r'<ac:parameter\s+ac:name="documentName"[^>]*>([^<]+)</ac:parameter>'

        for macro_match in re.finditer(macro_pattern, storage_xml, re.DOTALL | re.IGNORECASE):
            macro_content = macro_match.group(1)
            param_match = re.search(param_pattern, macro_content, re.IGNORECASE)
            if param_match:
                name = param_match.group(1).strip()
                if name:
                    names.append(name)
            else:
                names.append(None)  # No name found for this macro

        return names

    def _ensure_directories(self, space_key):
        """Create output directories for a space."""
        dirs = {
            'images': os.path.join(self.content_dir, 'images', space_key),
            'metadata': os.path.join(self.content_dir, 'metadata', space_key),
        }
        for path in dirs.values():
            os.makedirs(path, exist_ok=True)
        return dirs

    def get_pages_with_lucidchart(self, space_key=None, limit=None):
        """
        Find all pages containing Lucidchart macros.

        Args:
            space_key: Optional space to filter by
            limit: Optional max number of pages (for testing)

        Returns:
            List of page dicts with id, title, space info
        """
        pages = []
        start = 0

        # Build CQL query
        # Note: Use 'macro' not 'macroName' for Confluence search API
        if space_key:
            cql = f'space="{space_key}" and macro=lucidchart and type=page'
        else:
            cql = 'macro=lucidchart and type=page'

        while True:
            url = (
                f"{self.confluence_url}/rest/api/content/search"
                f"?cql={requests.utils.quote(cql)}"
                f"&start={start}"
                f"&expand=space,body.view,body.storage,_links"
            )

            response = self._rate_limited_request(url)

            if response.status_code != 200:
                print(f"Warning: Search failed: {response.status_code}")
                break

            data = response.json()
            results = data.get('results', [])

            if not results:
                break

            for page in results:
                # Skip personal spaces if configured
                space = page.get('space', {}).get('key', '')
                if self.skip_personal and space.startswith('~'):
                    continue

                # Extract body text for index hydration
                body_html = page.get('body', {}).get('view', {}).get('value', '')
                body_text = self._extract_text_from_html(body_html) if body_html else ''

                # Extract diagram names from storage format (Lucidchart macro parameters)
                storage_xml = page.get('body', {}).get('storage', {}).get('value', '')
                diagram_names = self._extract_lucidchart_names(storage_xml)

                pages.append({
                    'id': page['id'],
                    'title': page.get('title', 'Untitled'),
                    'space_key': space,
                    '_links': page.get('_links', {}),
                    'body_text': body_text,
                    'diagram_names': diagram_names,
                })

                # Check limit
                if limit and len(pages) >= limit:
                    return pages

            start += len(results)

            # Confluence pagination
            if len(results) < 25:
                break

        return pages

    def _init_browser(self, playwright, headless=True):
        """Initialize browser with Confluence authentication."""
        logger.info(f"Launching browser (headless={headless})...")
        self._browser = playwright.chromium.launch(
            headless=headless,
            args=['--disable-web-security']  # May help with iframe access
        )

        self._context = self._browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            ignore_https_errors=True,
            http_credentials={
                'username': self.auth[0],
                'password': self.auth[1]
            }
        )

        self._page = self._context.new_page()

        # First, authenticate by visiting Confluence
        print("Authenticating to Confluence...")
        login_url = f"{self.confluence_url}/login.action"
        self._page.goto(login_url, wait_until='networkidle', timeout=30000)

        # Check if we need to log in
        if 'login' in self._page.url.lower():
            # Try to fill login form
            try:
                self._page.fill('#os_username', self.auth[0], timeout=5000)
                self._page.fill('#os_password', self.auth[1], timeout=5000)
                self._page.click('#loginButton', timeout=5000)
                self._page.wait_for_load_state('networkidle', timeout=15000)
                print("Logged in successfully")
            except Exception as e:
                print(f"Login form not found or already logged in: {e}")

    def _close_browser(self):
        """Clean up browser resources."""
        if self._browser:
            self._browser.close()
            self._browser = None
            self._context = None
            self._page = None

    def _dump_page_structure(self, page_title, dirs):
        """Dump page HTML structure for debugging Lucidchart selectors."""
        try:
            # Get all iframes
            iframes = self._page.query_selector_all('iframe')
            logger.debug(f"Found {len(iframes)} iframes on page")
            for i, iframe in enumerate(iframes):
                src = iframe.get_attribute('src') or '(no src)'
                classes = iframe.get_attribute('class') or '(no class)'
                logger.debug(f"  iframe[{i}]: src={src[:100]}... class={classes}")

            # Get all elements with 'lucid' in class/id/data attributes
            lucid_elements = self._page.query_selector_all('[class*="lucid"], [id*="lucid"], [data-macro-name*="lucid"]')
            logger.debug(f"Found {len(lucid_elements)} elements with 'lucid' in attributes")
            for i, el in enumerate(lucid_elements):
                tag = el.evaluate('el => el.tagName')
                classes = el.get_attribute('class') or ''
                el_id = el.get_attribute('id') or ''
                logger.debug(f"  lucid[{i}]: <{tag}> class={classes[:50]} id={el_id}")

            # Get all macro containers
            macro_elements = self._page.query_selector_all('[data-macro-name]')
            logger.debug(f"Found {len(macro_elements)} macro elements")
            for i, el in enumerate(macro_elements):
                macro_name = el.get_attribute('data-macro-name') or ''
                tag = el.evaluate('el => el.tagName')
                logger.debug(f"  macro[{i}]: <{tag}> data-macro-name={macro_name}")

            # Save full HTML for deep analysis (only in debug mode)
            if logger.level <= logging.DEBUG:
                safe_title = re.sub(r'[^\w\s-]', '', page_title).strip()[:30]
                debug_path = os.path.join(dirs['metadata'], f"_debug_{safe_title}.html")
                html = self._page.content()
                with open(debug_path, 'w', encoding='utf-8') as f:
                    f.write(html)
                logger.debug(f"Saved page HTML to {debug_path}")

        except Exception as e:
            logger.warning(f"Error dumping page structure: {e}")

    def screenshot_page_diagrams(self, page_info, dirs, dry_run=False):
        """
        Navigate to a page and screenshot all Lucidchart diagrams.

        Args:
            page_info: Dict with page id, title, space_key
            dirs: Output directories dict
            dry_run: If True, don't actually screenshot

        Returns:
            int: Number of diagrams captured
        """
        page_id = page_info['id']
        page_title = page_info['title']
        space_key = page_info['space_key']
        page_link = page_info.get('_links', {}).get('webui', '')
        body_text = page_info.get('body_text', '')
        diagram_names = page_info.get('diagram_names', [])

        # Navigate to page
        page_url = f"{self.confluence_url}/pages/viewpage.action?pageId={page_id}"
        logger.info(f"Loading page: {page_url}")

        if dry_run:
            logger.info(f"[DRY RUN] Would screenshot: {page_title}")
            return 1  # Assume at least one diagram

        try:
            self._page.goto(page_url, wait_until='networkidle', timeout=30000)
            logger.debug(f"Page loaded, URL now: {self._page.url}")
        except PlaywrightTimeout:
            logger.warning(f"Timeout loading page: {page_title}")
            return 0

        # Wait for dynamic content to load
        logger.debug("Waiting 3s for iframes/dynamic content...")
        time.sleep(3)

        # Dump page structure for debugging
        self._dump_page_structure(page_title, dirs)

        # Find Lucidchart iframes/embeds
        diagrams_captured = 0

        # Comprehensive list of selectors to try
        # Lucidchart embeds can be iframes, divs, or img tags depending on Confluence version
        selectors = [
            # Direct iframe selectors
            'iframe[src*="lucid"]',
            'iframe[src*="lucidchart"]',
            'iframe[src*="app.lucid"]',
            # Confluence macro containers
            '[data-macro-name="lucidchart"]',
            '.lucidchart-macro',
            # Embedded image fallbacks (static renders)
            'img[data-macro-name="lucidchart"]',
            '.confluence-embedded-image[alt*="lucid" i]',
            # Generic embed containers
            '.embedded-macro[data-macro-name="lucidchart"]',
            '.wysiwyg-macro[data-macro-name="lucidchart"]',
            # Wrapper divs
            '.lucidchart-wrapper',
            '.lucid-embed',
            'div[data-lucid-document-id]',
        ]

        logger.info(f"Trying {len(selectors)} selectors...")

        for selector in selectors:
            try:
                elements = self._page.query_selector_all(selector)
                if elements:
                    logger.info(f"  Selector '{selector}' matched {len(elements)} element(s)")

                for idx, element in enumerate(elements):
                    # Log element details
                    try:
                        tag = element.evaluate('el => el.tagName')
                        box = element.bounding_box()
                        logger.debug(f"    Element {idx}: <{tag}> box={box}")
                    except:
                        pass

                    # Generate unique name for this diagram
                    # First try to use diagram name from Lucidchart macro (documentName parameter)
                    # Use diagrams_captured as index since we track actual captures, not element index
                    macro_name = None
                    if diagrams_captured < len(diagram_names) and diagram_names[diagrams_captured]:
                        macro_name = diagram_names[diagrams_captured]

                    if macro_name:
                        # Use the Lucidchart document name from the macro
                        safe_name = re.sub(r'[^\w\s-]', '', macro_name).strip()[:80]
                        diagram_name = safe_name if safe_name else re.sub(r'[^\w\s-]', '', page_title).strip()[:50]
                    else:
                        # Fallback to page title
                        safe_title = re.sub(r'[^\w\s-]', '', page_title).strip()[:50]
                        diagram_name = f"{safe_title}_{idx+1}" if idx > 0 else safe_title

                    # Screenshot the element
                    png_path = os.path.join(dirs['images'], f"{diagram_name}.png")

                    try:
                        # Get bounding box
                        box = element.bounding_box()
                        if box:
                            logger.debug(f"    Bounding box: {box['width']}x{box['height']} at ({box['x']}, {box['y']})")

                            if box['width'] > 50 and box['height'] > 50:
                                # Scroll element into view first
                                element.scroll_into_view_if_needed()
                                time.sleep(0.5)  # Brief pause after scroll

                                # Screenshot element directly
                                element.screenshot(path=png_path)
                                logger.info(f"    CAPTURED: {diagram_name} ({box['width']}x{box['height']})")

                                # Save metadata
                                metadata = {
                                    'title': f"{diagram_name}.png",
                                    'space': {'key': space_key},
                                    'page_id': page_id,
                                    'page_title': page_title,
                                    'page_link': page_link,
                                    'body_text': body_text,
                                    'source': 'lucidchart',
                                    'selector_used': selector,
                                    'dimensions': {'width': box['width'], 'height': box['height']},
                                    '_expandable': {
                                        'container': f"/rest/api/content/{page_id}"
                                    }
                                }
                                meta_path = os.path.join(dirs['metadata'], f"{diagram_name}.png.json")
                                with open(meta_path, 'w', encoding='utf-8') as f:
                                    json.dump(metadata, f, indent=2)

                                diagrams_captured += 1
                            else:
                                logger.debug(f"    Skipped: too small ({box['width']}x{box['height']})")
                        else:
                            logger.debug(f"    Skipped: no bounding box (element may be hidden)")

                    except Exception as e:
                        logger.warning(f"    Error capturing {diagram_name}: {e}")

            except Exception as e:
                logger.debug(f"  Selector '{selector}' failed: {e}")

        # If no specific elements found, try screenshotting the main content area
        if diagrams_captured == 0:
            logger.info("No specific Lucidchart elements found, trying fullpage content capture...")

            content_selectors = [
                '#main-content',
                '.wiki-content',
                '#content-body',
                '#content',
                'article',
            ]

            for content_sel in content_selectors:
                try:
                    content_area = self._page.query_selector(content_sel)
                    if content_area:
                        box = content_area.bounding_box()
                        if box and box['width'] > 100 and box['height'] > 100:
                            safe_title = re.sub(r'[^\w\s-]', '', page_title).strip()[:50]
                            diagram_name = f"{safe_title}_fullpage"
                            png_path = os.path.join(dirs['images'], f"{diagram_name}.png")

                            content_area.screenshot(path=png_path)
                            logger.info(f"    CAPTURED fullpage via {content_sel}: {diagram_name}")

                            metadata = {
                                'title': f"{diagram_name}.png",
                                'space': {'key': space_key},
                                'page_id': page_id,
                                'page_title': page_title,
                                'page_link': page_link,
                                'body_text': body_text,
                                'source': 'lucidchart-fullpage',
                                'selector_used': content_sel,
                                '_expandable': {
                                    'container': f"/rest/api/content/{page_id}"
                                }
                            }
                            meta_path = os.path.join(dirs['metadata'], f"{diagram_name}.png.json")
                            with open(meta_path, 'w', encoding='utf-8') as f:
                                json.dump(metadata, f, indent=2)

                            diagrams_captured = 1
                            break  # Got one, stop trying

                except Exception as e:
                    logger.debug(f"  Content selector '{content_sel}' failed: {e}")

        if diagrams_captured == 0:
            logger.warning(f"  NO DIAGRAMS CAPTURED for page: {page_title}")

        return diagrams_captured

    def extract_space(self, space_key, limit=None, dry_run=False):
        """
        Extract all Lucidchart diagrams from a space.

        Args:
            space_key: Confluence space key
            limit: Max pages to process (for testing)
            dry_run: If True, don't actually capture

        Returns:
            int: Number of diagrams captured
        """
        dirs = self._ensure_directories(space_key)
        pages = self.get_pages_with_lucidchart(space_key, limit=limit)

        print(f"\n  Found {len(pages)} pages with Lucidchart in {space_key}")

        total_diagrams = 0
        for idx, page in enumerate(pages):
            print(f"  [{idx+1}/{len(pages)}] {page['title'][:50]}...")
            count = self.screenshot_page_diagrams(page, dirs, dry_run)
            total_diagrams += count

        return total_diagrams

    def extract_all(self, spaces=None, limit=None, dry_run=False, headless=True):
        """
        Extract Lucidchart diagrams from all (or specified) spaces.

        Args:
            spaces: List of space keys, or None for all
            limit: Max pages per space (for testing)
            dry_run: If True, don't capture
            headless: Run browser in headless mode

        Returns:
            int: Total diagrams captured
        """
        with sync_playwright() as playwright:
            self._init_browser(playwright, headless=headless)

            try:
                if spaces:
                    # Process specified spaces
                    total = 0
                    for space_key in spaces:
                        print(f"\nProcessing space: {space_key}")
                        total += self.extract_space(space_key, limit=limit, dry_run=dry_run)
                    return total
                else:
                    # Find all pages with Lucidchart across all spaces
                    pages = self.get_pages_with_lucidchart(limit=limit)
                    print(f"\nFound {len(pages)} pages with Lucidchart")

                    # Group by space
                    spaces_found = {}
                    for page in pages:
                        sk = page['space_key']
                        if sk not in spaces_found:
                            spaces_found[sk] = []
                        spaces_found[sk].append(page)

                    total = 0
                    for space_key, space_pages in spaces_found.items():
                        dirs = self._ensure_directories(space_key)
                        print(f"\nProcessing space: {space_key} ({len(space_pages)} pages)")

                        for idx, page in enumerate(space_pages):
                            print(f"  [{idx+1}/{len(space_pages)}] {page['title'][:50]}...")
                            count = self.screenshot_page_diagrams(page, dirs, dry_run)
                            total += count

                    return total

            finally:
                self._close_browser()


def main():
    """CLI entry point."""
    check_playwright_installed()

    parser = argparse.ArgumentParser(
        description='Screenshot Lucidchart diagrams from Confluence'
    )
    parser.add_argument('--spaces', help='Comma-separated space keys')
    parser.add_argument('--test', action='store_true',
                        help='Test mode: only process first 5 pages')
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would be captured without actually doing it')
    parser.add_argument('--config', help='Path to settings.ini')
    parser.add_argument('--debug', action='store_true',
                        help='Enable debug logging (verbose output, saves HTML)')
    parser.add_argument('--headless', action='store_true', default=True,
                        help='Run browser in headless mode (default: True)')
    parser.add_argument('--no-headless', action='store_false', dest='headless',
                        help='Show browser window (useful for debugging)')

    args = parser.parse_args()

    # Set logging level
    if args.debug:
        logger.setLevel(logging.DEBUG)
        logging.getLogger().setLevel(logging.DEBUG)
        logger.debug("Debug logging enabled")

    if args.config:
        Settings.reload(args.config)

    spaces = None
    if args.spaces:
        spaces = [s.strip() for s in args.spaces.split(',')]

    limit = 5 if args.test else None

    screenshotter = LucidchartScreenshotter()

    print("=" * 60)
    print("LUCIDCHART SCREENSHOT EXTRACTOR")
    print("=" * 60)
    print(f"Confluence: {screenshotter.confluence_url}")
    print(f"Output: {screenshotter.content_dir}")
    print(f"Spaces: {spaces or 'all'}")
    print(f"Test mode: {args.test}")
    print(f"Dry run: {args.dry_run}")
    print("=" * 60)

    total = screenshotter.extract_all(
        spaces=spaces,
        limit=limit,
        dry_run=args.dry_run,
        headless=args.headless
    )

    print("\n" + "=" * 60)
    print(f"COMPLETE: Captured {total} diagrams")
    print("=" * 60)


if __name__ == '__main__':
    main()
