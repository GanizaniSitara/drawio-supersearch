"""
Confluence DrawIO Extractor

Downloads all DrawIO diagrams from Confluence Data Center/Server.
Saves .drawio files, .png renders, and metadata JSON.
"""

import os
import re
import json
import time
import requests
from urllib3.exceptions import InsecureRequestWarning
from .config import Settings

# Suppress SSL warnings for internal Confluence instances
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)


class ConfluenceExtractor:
    """Extract DrawIO diagrams from Confluence."""

    def __init__(self, settings=None):
        """Initialize extractor with settings."""
        if settings is None:
            settings = Settings.get()
        self.settings = settings

        self.confluence_url = settings['confluence_url'].rstrip('/')
        self.auth = (settings['confluence_username'], settings['confluence_password'])
        self.content_dir = settings['content_directory']
        self.rate_limit = settings['rate_limit']
        self.batch_size = settings['batch_size']
        self.skip_personal = settings['skip_personal_spaces']

        self._last_request_time = 0

    def _rate_limited_request(self, url, stream=False):
        """Make a rate-limited request."""
        # Enforce rate limit
        elapsed = time.time() - self._last_request_time
        min_interval = 1.0 / self.rate_limit
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)

        self._last_request_time = time.time()
        return requests.get(url, auth=self.auth, stream=stream, verify=False)

    def _ensure_directories(self, space_key):
        """Create output directories for a space."""
        dirs = {
            'diagrams': os.path.join(self.content_dir, 'diagrams', space_key),
            'images': os.path.join(self.content_dir, 'images', space_key),
            'metadata': os.path.join(self.content_dir, 'metadata', space_key),
        }
        for path in dirs.values():
            os.makedirs(path, exist_ok=True)
        return dirs

    def get_all_spaces(self):
        """Get list of all Confluence spaces."""
        spaces = []
        start = 0
        limit = 100

        while True:
            url = f"{self.confluence_url}/rest/api/space?start={start}&limit={limit}"
            response = self._rate_limited_request(url)

            if response.status_code != 200:
                raise Exception(f"Failed to get spaces: {response.status_code}")

            data = response.json()
            results = data.get('results', [])

            if not results:
                break

            for space in results:
                key = space.get('key', '')
                # Skip personal spaces if configured
                if self.skip_personal and key.startswith('~'):
                    continue
                spaces.append({
                    'key': key,
                    'name': space.get('name', key)
                })

            start += len(results)

        return spaces

    def get_pages_with_drawio(self, space_key):
        """Get all pages in a space that contain DrawIO diagrams."""
        pages = []
        start = 0

        while True:
            # CQL query for pages with drawio macro
            cql = f'space="{space_key}" and macro=drawio and type=page'
            url = (
                f"{self.confluence_url}/rest/api/content/search"
                f"?cql={requests.utils.quote(cql)}"
                f"&start={start}"
                f"&expand=children.attachment,body.storage,children.attachment.version"
            )

            response = self._rate_limited_request(url)

            if response.status_code != 200:
                print(f"Warning: Failed to search {space_key}: {response.status_code}")
                break

            results = response.json().get('results', [])

            if not results:
                break

            pages.extend(results)
            start += len(results)

        return pages

    def get_page_attachments(self, page_id):
        """Get all attachments for a page."""
        attachments = []
        start = 0

        while True:
            url = (
                f"{self.confluence_url}/rest/api/content/{page_id}/child/attachment"
                f"?start={start}&expand=version"
            )

            response = self._rate_limited_request(url)

            if response.status_code != 200:
                break

            results = response.json().get('results', [])

            if not results:
                break

            attachments.extend(results)
            start += len(results)

        return attachments

    def download_diagram(self, page, attachments, space_key, dirs, dry_run=False):
        """
        Download DrawIO diagrams from a page.

        Returns:
            int: Number of diagrams downloaded
        """
        downloaded = 0
        page_body = page.get('body', {}).get('storage', {}).get('value', '')

        # Find all drawio macros in the page
        regex = r'<ac:structured-macro ac:name="drawio".*?>.*?</ac:structured-macro>'
        drawio_macros = re.finditer(regex, page_body, re.MULTILINE | re.DOTALL)

        for match in drawio_macros:
            macro_content = match.group()

            # Extract diagram name
            name_match = re.search(
                r'<ac:parameter ac:name="diagramName">(.*?)</ac:parameter>',
                macro_content
            )
            if not name_match:
                continue

            diagram_name = name_match.group(1)

            # Extract diagram width (optional)
            width_match = re.search(
                r'<ac:parameter ac:name="diagramWidth">(.*?)</ac:parameter>',
                macro_content
            )
            diagram_width = width_match.group(1) if width_match else ''

            # Find attachment URLs
            drawio_file = diagram_name
            png_file = f"{diagram_name}.png"

            drawio_attachment = next(
                (a for a in attachments if a.get('title') == drawio_file),
                None
            )
            png_attachment = next(
                (a for a in attachments if a.get('title') == png_file),
                None
            )

            if dry_run:
                print(f"  Would download: {diagram_name}")
                downloaded += 1
                continue

            # Download PNG render
            if png_attachment:
                png_url = self.confluence_url + png_attachment['_links']['download']
                response = self._rate_limited_request(png_url, stream=True)

                if response.status_code == 200:
                    # Clean filename (remove tabs and other problematic chars)
                    safe_name = diagram_name.replace('\t', '').replace('/', '_')
                    png_path = os.path.join(dirs['images'], f"{safe_name}.png")

                    with open(png_path, 'wb') as f:
                        for chunk in response.iter_content(chunk_size=8192):
                            f.write(chunk)

                # Save metadata
                metadata = dict(png_attachment)
                metadata.setdefault('space', {})['key'] = space_key
                metadata['diagramWidth'] = diagram_width

                meta_path = os.path.join(dirs['metadata'], f"{safe_name}.png.json")
                with open(meta_path, 'w', encoding='utf-8') as f:
                    json.dump(metadata, f, indent=2)

            # Download .drawio file
            if drawio_attachment:
                drawio_url = self.confluence_url + drawio_attachment['_links']['download']
                response = self._rate_limited_request(drawio_url, stream=True)

                if response.status_code == 200:
                    safe_name = diagram_name.replace('\t', '').replace('/', '_')
                    drawio_path = os.path.join(dirs['diagrams'], f"{safe_name}.drawio")

                    with open(drawio_path, 'wb') as f:
                        for chunk in response.iter_content(chunk_size=8192):
                            f.write(chunk)

                    downloaded += 1

        return downloaded

    def extract_space(self, space_key, progress_callback=None, dry_run=False):
        """
        Extract all DrawIO diagrams from a space.

        Args:
            space_key: Confluence space key
            progress_callback: Optional callback(page_num, total_pages, page_title)
            dry_run: If True, don't actually download

        Returns:
            int: Number of diagrams extracted
        """
        dirs = self._ensure_directories(space_key)

        pages = self.get_pages_with_drawio(space_key)
        total_diagrams = 0

        for idx, page in enumerate(pages):
            if progress_callback:
                progress_callback(idx + 1, len(pages), page.get('title', ''))

            attachments = self.get_page_attachments(page['id'])
            count = self.download_diagram(page, attachments, space_key, dirs, dry_run)
            total_diagrams += count

        return total_diagrams

    def extract_all(self, spaces=None, progress_callback=None, dry_run=False):
        """
        Extract diagrams from all (or specified) spaces.

        Args:
            spaces: List of space keys, or None for all spaces
            progress_callback: Optional callback(space_idx, total_spaces, space_key, diagrams_so_far)
            dry_run: If True, don't actually download

        Returns:
            int: Total diagrams extracted
        """
        if spaces is None:
            space_list = self.get_all_spaces()
            spaces = [s['key'] for s in space_list]

        total_diagrams = 0

        for idx, space_key in enumerate(spaces):
            if progress_callback:
                progress_callback(idx + 1, len(spaces), space_key, total_diagrams)

            try:
                count = self.extract_space(space_key, dry_run=dry_run)
                total_diagrams += count
            except Exception as e:
                print(f"Error extracting {space_key}: {e}")

        return total_diagrams


def main():
    """CLI entry point for extraction."""
    import argparse

    parser = argparse.ArgumentParser(description='Extract DrawIO diagrams from Confluence')
    parser.add_argument('--spaces', help='Comma-separated space keys (default: all)')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be extracted')
    parser.add_argument('--config', help='Path to settings.ini file')

    args = parser.parse_args()

    # Load settings
    if args.config:
        Settings.reload(args.config)

    spaces = None
    if args.spaces:
        spaces = [s.strip() for s in args.spaces.split(',')]

    extractor = ConfluenceExtractor()

    def progress(space_idx, total_spaces, space_key, diagrams_so_far):
        pct = (space_idx / total_spaces) * 100
        print(f"\r[{pct:5.1f}%] Space {space_idx}/{total_spaces}: {space_key:<20} | Total: {diagrams_so_far}",
              end='', flush=True)

    print("Starting extraction...")
    total = extractor.extract_all(spaces=spaces, progress_callback=progress, dry_run=args.dry_run)
    print(f"\n\nExtracted {total} diagrams")


if __name__ == '__main__':
    main()
