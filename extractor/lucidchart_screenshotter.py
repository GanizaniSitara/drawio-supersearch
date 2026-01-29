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

# Check for OCR dependencies
try:
    import pytesseract
    from PIL import Image
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False
    pytesseract = None
    Image = None

import requests
from urllib3.exceptions import InsecureRequestWarning

# Handle imports for both module and script execution
try:
    from .config import Settings
except ImportError:
    # Running as script - add parent directory to path
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from extractor.config import Settings


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

    def get_all_spaces(self):
        """
        Get list of all global space keys from Confluence.

        Returns:
            List of space key strings
        """
        spaces = []
        start = 0

        print("Fetching list of all spaces...")

        while True:
            url = (
                f"{self.confluence_url}/rest/api/space"
                f"?type=global"
                f"&start={start}"
                f"&limit=100"
            )

            response = self._rate_limited_request(url)

            if response.status_code != 200:
                print(f"Warning: Failed to fetch spaces: {response.status_code}")
                break

            data = response.json()
            results = data.get('results', [])

            if not results:
                break

            for space in results:
                space_key = space.get('key', '')
                # Skip personal spaces if configured
                if self.skip_personal and space_key.startswith('~'):
                    continue
                spaces.append(space_key)

            start += len(results)

            # Print progress
            if start % 500 == 0:
                print(f"  Found {len(spaces)} spaces so far...", flush=True)

            # Check if more results
            if len(results) < 100:
                break

        print(f"  Found {len(spaces)} global spaces total")
        return spaces

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

        # Progress indicator for "all spaces" mode (no space_key)
        if not space_key:
            print("Searching for Lucidchart pages across all spaces...")
            import sys

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
                # Print response body for debugging
                try:
                    error_detail = response.text[:500]
                    print(f"  Response: {error_detail}")
                except:
                    pass
                break

            data = response.json()
            results = data.get('results', [])

            if not results:
                break

            # Progress output for "all spaces" mode
            if not space_key:
                total_size = data.get('totalSize', data.get('size', '?'))
                print(f"  Fetched batch {start//25 + 1}: {len(pages) + len(results)} pages so far (total: {total_size})", flush=True)

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

    def _try_maximize_lucidchart(self, element):
        """
        Try to maximize a Lucidchart diagram view before screenshotting.

        Looks for and clicks maximize/fullscreen buttons on Lucidchart embeds.
        Returns True if maximize was successful, False otherwise.
        """
        # Selectors for maximize/fullscreen buttons in Lucidchart embeds
        maximize_selectors = [
            # Lucidchart viewer buttons
            'button[aria-label*="maximize" i]',
            'button[aria-label*="fullscreen" i]',
            'button[aria-label*="expand" i]',
            'button[title*="maximize" i]',
            'button[title*="fullscreen" i]',
            'button[title*="expand" i]',
            # Icon-based buttons
            '.maximize-button',
            '.fullscreen-button',
            '.expand-button',
            '[data-testid="maximize-button"]',
            '[data-testid="fullscreen-button"]',
            # SVG icons commonly used for maximize
            'button:has(svg[data-icon="expand"])',
            'button:has(svg[data-icon="maximize"])',
            # Lucidchart specific
            '.lucid-toolbar button[aria-label*="full" i]',
            '.lucidchart-toolbar button[aria-label*="full" i]',
            # Generic expand icons
            '[class*="expand"]',
            '[class*="maximize"]',
            '[class*="fullscreen"]',
        ]

        try:
            # First, hover over the element to reveal toolbar buttons
            element.hover()
            time.sleep(0.5)  # Wait for toolbar to appear

            # Try to find maximize button within the element or nearby
            for selector in maximize_selectors:
                try:
                    # Try within the element first
                    max_btn = element.query_selector(selector)
                    if max_btn and max_btn.is_visible():
                        logger.info(f"    Found maximize button: {selector}")
                        max_btn.click()
                        time.sleep(1.5)  # Wait for animation
                        return True
                except Exception:
                    pass

                # Try in the page context (for floating toolbars)
                try:
                    max_btn = self._page.query_selector(selector)
                    if max_btn and max_btn.is_visible():
                        logger.info(f"    Found maximize button (page level): {selector}")
                        max_btn.click()
                        time.sleep(1.5)  # Wait for animation
                        return True
                except Exception:
                    pass

            # Try iframe-specific approach if element is/contains an iframe
            try:
                iframe = element if element.evaluate('el => el.tagName') == 'IFRAME' else element.query_selector('iframe')
                if iframe:
                    frame = iframe.content_frame()
                    if frame:
                        for selector in maximize_selectors:
                            try:
                                max_btn = frame.query_selector(selector)
                                if max_btn and max_btn.is_visible():
                                    logger.info(f"    Found maximize button in iframe: {selector}")
                                    max_btn.click()
                                    time.sleep(1.5)
                                    return True
                            except Exception:
                                pass
            except Exception as e:
                logger.debug(f"    Could not access iframe content: {e}")

            logger.debug("    No maximize button found")
            return False

        except Exception as e:
            logger.debug(f"    Error trying to maximize: {e}")
            return False

    def _restore_from_maximize(self):
        """
        Try to restore/exit from maximized view after screenshot.

        Looks for close/minimize/exit fullscreen buttons or uses Escape key.
        """
        restore_selectors = [
            'button[aria-label*="close" i]',
            'button[aria-label*="minimize" i]',
            'button[aria-label*="exit" i]',
            'button[aria-label*="restore" i]',
            'button[title*="close" i]',
            'button[title*="exit" i]',
            '.close-button',
            '.minimize-button',
            '[data-testid="close-button"]',
            '[data-testid="exit-fullscreen"]',
        ]

        try:
            # Try clicking restore/close buttons
            for selector in restore_selectors:
                try:
                    btn = self._page.query_selector(selector)
                    if btn and btn.is_visible():
                        btn.click()
                        time.sleep(0.5)
                        return True
                except Exception:
                    pass

            # Fallback: press Escape key to exit fullscreen
            self._page.keyboard.press('Escape')
            time.sleep(0.5)
            return True

        except Exception as e:
            logger.debug(f"    Error restoring from maximize: {e}")
            return False

    def _extract_text_with_ocr(self, image_path):
        """
        Extract text from an image using OCR.

        Args:
            image_path: Path to the image file

        Returns:
            str: Extracted text, or empty string if OCR fails/unavailable
        """
        if not OCR_AVAILABLE:
            logger.debug("    OCR not available (pytesseract not installed)")
            return ''

        try:
            # Open the image
            image = Image.open(image_path)

            # Run OCR with Tesseract
            # Use config for better accuracy on diagrams
            custom_config = r'--oem 3 --psm 6'
            text = pytesseract.image_to_string(image, config=custom_config)

            # Clean up the extracted text
            # Remove excessive whitespace while preserving some structure
            lines = [line.strip() for line in text.split('\n') if line.strip()]
            cleaned_text = ' '.join(lines)

            # Filter out very short results (likely noise)
            if len(cleaned_text) < 10:
                logger.debug(f"    OCR result too short ({len(cleaned_text)} chars), discarding")
                return ''

            logger.info(f"    OCR extracted {len(cleaned_text)} characters")
            return cleaned_text

        except Exception as e:
            logger.warning(f"    OCR failed: {e}")
            return ''

    def _dump_page_structure(self, page_title):
        """Log page HTML structure for debugging Lucidchart selectors (console only, no files)."""
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

        # Dump page structure for debugging (only in debug mode)
        if logger.level <= logging.DEBUG:
            self._dump_page_structure(page_title)

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

                                # Try to maximize the Lucidchart view before screenshot
                                was_maximized = self._try_maximize_lucidchart(element)
                                if was_maximized:
                                    logger.info(f"    Maximized view for better screenshot")
                                    time.sleep(1)  # Wait for maximize animation

                                # Screenshot element directly (or page if maximized)
                                if was_maximized:
                                    self._page.screenshot(path=png_path, full_page=False)
                                else:
                                    element.screenshot(path=png_path)
                                logger.info(f"    CAPTURED: {diagram_name} ({box['width']}x{box['height']})")

                                # Restore from maximized view if we maximized
                                if was_maximized:
                                    self._restore_from_maximize()

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
                                    'was_maximized': was_maximized,
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

    def _get_completed_spaces(self):
        """Scan metadata directory to find spaces that have already been processed."""
        metadata_dir = os.path.join(self.content_dir, 'metadata')
        if not os.path.exists(metadata_dir):
            return set()
        completed = set()
        for entry in os.listdir(metadata_dir):
            entry_path = os.path.join(metadata_dir, entry)
            if os.path.isdir(entry_path) and os.listdir(entry_path):
                completed.add(entry)
        return completed

    def extract_all(self, spaces=None, limit=None, dry_run=False, headless=True, resume=False):
        """
        Extract Lucidchart diagrams from all (or specified) spaces.

        Args:
            spaces: List of space keys, or None for all
            limit: Max pages per space (for testing)
            dry_run: If True, don't capture
            headless: Run browser in headless mode
            resume: If True, skip spaces that already have metadata

        Returns:
            int: Total diagrams captured
        """
        with sync_playwright() as playwright:
            self._init_browser(playwright, headless=headless)

            try:
                completed_spaces = self._get_completed_spaces() if resume else set()
                if completed_spaces:
                    print(f"\nResume mode: {len(completed_spaces)} spaces already completed, will be skipped")

                if spaces:
                    # Process specified spaces
                    total = 0
                    for idx, space_key in enumerate(spaces):
                        if space_key in completed_spaces:
                            print(f"\n[Space {idx+1}/{len(spaces)}] Skipping (already completed): {space_key}")
                            continue
                        print(f"\n[Space {idx+1}/{len(spaces)}] Processing: {space_key}")
                        total += self.extract_space(space_key, limit=limit, dry_run=dry_run)
                    return total
                else:
                    # Get all spaces first, then process each one
                    # This is more reliable than loading all pages across all spaces at once
                    all_spaces = self.get_all_spaces()

                    if not all_spaces:
                        print("No spaces found or accessible.")
                        return 0

                    print(f"\nWill check {len(all_spaces)} spaces for Lucidchart content...")

                    total = 0
                    spaces_with_content = 0

                    for idx, space_key in enumerate(all_spaces):
                        if space_key in completed_spaces:
                            print(f"\n[Space {idx+1}/{len(all_spaces)}] Skipping (already completed): {space_key}")
                            continue

                        print(f"\n[Space {idx+1}/{len(all_spaces)}] Checking: {space_key}")

                        # Get pages with Lucidchart in this space
                        pages = self.get_pages_with_lucidchart(space_key, limit=limit)

                        if not pages:
                            print(f"  No Lucidchart content found")
                            continue

                        spaces_with_content += 1
                        dirs = self._ensure_directories(space_key)
                        print(f"  Found {len(pages)} pages with Lucidchart")

                        for page_idx, page in enumerate(pages):
                            print(f"  [{page_idx+1}/{len(pages)}] {page['title'][:50]}...")
                            count = self.screenshot_page_diagrams(page, dirs, dry_run)
                            total += count

                    print(f"\n  Summary: Found Lucidchart content in {spaces_with_content} of {len(all_spaces)} spaces")
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
    parser.add_argument('--resume', action='store_true',
                        help='Resume from checkpoint: skip spaces that already have metadata')

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
    print(f"Resume: {args.resume}")
    print(f"Maximize: enabled (attempts to maximize charts before capture)")
    print("=" * 60)

    total = screenshotter.extract_all(
        spaces=spaces,
        limit=limit,
        dry_run=args.dry_run,
        headless=args.headless,
        resume=args.resume
    )

    print("\n" + "=" * 60)
    print(f"COMPLETE: Captured {total} diagrams")
    print("=" * 60)


if __name__ == '__main__':
    main()
