# DrawIO SuperSearch

**Fast full-text search across ALL your Confluence DrawIO diagrams**

See every diagram in your Confluence instance at a glance. Search inside diagram content. Browse visually with thumbnails. No more hunting through page hierarchies.

See live demo here https://drawio-supersearch.onrender.com/

## Why SuperSearch?

| Without SuperSearch | With SuperSearch |
|---------------------|------------------|
| Navigate Confluence page by page | See ALL diagrams in one grid |
| Can't search diagram content | Full-text search inside diagrams |
| Slow page loads | Instant local browsing |
| Need Confluence access | Works offline |

## Features

- **Browse by Confluence space** - Visual thumbnail grid of all diagrams
- **Full-text search** - Search across diagram names, page titles, and text content inside diagrams
- **PNG preview** - Quick visual preview without opening the diagram
- **Download .drawio files** - Get the editable source files
- **Edit integration** - Launch draw.io Desktop or web editor
- **SQLite + Whoosh** - Fast local database and search index

## Quick Start

```bash
# 1. Clone repository
git clone https://github.com/yourorg/drawio-supersearch.git
cd drawio-supersearch

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure
cp settings.ini.example settings.ini
# Edit settings.ini with your Confluence URL and credentials

# 4. Extract diagrams from Confluence (this may take a while)
python scripts/extract.py

# 5. Build search index
python scripts/index.py

# 6. Start browser
python scripts/serve.py

# 7. Open http://localhost:5000
```

## Configuration

Edit `settings.ini`:

```ini
[Confluence]
url = https://confluence.yourcompany.com
username = your_username
password = your_password
spaces =  # Leave empty for all spaces, or comma-separated: ADO,API,ALERTS

[Local]
content_directory = ./data/content
database_path = ./data/diagrams.db
index_directory = ./data/whoosh_index

[Browser]
host = 127.0.0.1
port = 5000
debug = false

[Extractor]
rate_limit = 5
batch_size = 50
skip_personal_spaces = true
```

## CLI Commands

### Extract Diagrams

```bash
# Extract all spaces
python scripts/extract.py

# Extract specific spaces
python scripts/extract.py --spaces ADO,API,ALERTS

# Dry run (show what would be extracted)
python scripts/extract.py --dry-run
```

### Build Search Index

```bash
# Build index
python scripts/index.py

# Rebuild (clear and rebuild)
python scripts/index.py --rebuild
```

### Start Web Server

```bash
# Development mode
python scripts/serve.py

# Custom port
python scripts/serve.py --port 8080

# Production mode with Gunicorn
gunicorn -w 4 -b 0.0.0.0:5000 browser.app:app
```

## Directory Structure

After extraction:

```
drawio-supersearch/
├── data/
│   ├── content/
│   │   ├── diagrams/       # .drawio files by space
│   │   │   ├── SPACEKEY1/
│   │   │   └── SPACEKEY2/
│   │   ├── images/         # .png renders by space
│   │   │   └── ...
│   │   └── metadata/       # .json metadata by space
│   │       └── ...
│   ├── diagrams.db         # SQLite database
│   └── whoosh_index/       # Full-text search index
├── browser/                # Flask web application
├── extractor/              # Confluence extraction module
├── scripts/                # CLI commands
├── settings.ini            # Your configuration
└── requirements.txt
```

## Edit Integration

The diagram detail page includes two edit buttons:

- **Edit Local** - Downloads the .drawio file. Open with [draw.io Desktop](https://github.com/jgraph/drawio-desktop/releases)
- **Edit in Browser** - Opens [app.diagrams.net](https://app.diagrams.net) where you can import the file

Note: Changes made are local only. To update Confluence, you would need to re-upload the modified diagram.

## Docker Deployment

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
EXPOSE 5000
CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:5000", "browser.app:app"]
```

```bash
docker build -t drawio-supersearch .
docker run -v /path/to/data:/app/data -p 5000:5000 drawio-supersearch
```

## Scheduled Updates

For keeping diagrams in sync with Confluence:

```bash
# crontab entry - run at 2 AM daily
0 2 * * * cd /path/to/drawio-supersearch && python scripts/extract.py && python scripts/index.py
```

## Lucidchart Screenshot Tool (Migration Preview)

Planning to migrate from Lucidchart to DrawIO? Use the Lucidchart screenshotter to capture your existing Lucidchart diagrams as PNG images, letting you preview the SuperSearch experience with your real data before migration.

### Setup

```bash
# Playwright is optional - only needed for Lucidchart screenshots
pip install playwright
playwright install chromium
```

### Usage

```bash
# Screenshot Lucidchart diagrams from specific spaces
python -m extractor.lucidchart_screenshotter --spaces MYSPACE,OTHERSPACE

# Test mode - only process first 5 pages
python -m extractor.lucidchart_screenshotter --test

# Debug mode - verbose logging, saves page HTML for selector tuning
python -m extractor.lucidchart_screenshotter --debug

# Show browser window (useful for debugging auth/rendering issues)
python -m extractor.lucidchart_screenshotter --debug --no-headless

# Dry run - see what would be captured without actually doing it
python -m extractor.lucidchart_screenshotter --dry-run
```

### How It Works

1. Searches Confluence for pages containing Lucidchart macros (`macroName:lucidchart`)
2. Opens each page in a headless browser (Playwright/Chromium)
3. Waits for Lucidchart embeds to load
4. Screenshots the diagram elements
5. Saves to `content/images/<SPACE>/` with metadata

The output is compatible with SuperSearch - just rebuild the index and browse your Lucidchart diagrams alongside any existing DrawIO diagrams.

### Debugging

Lucidchart embeds can be tricky (iframes, dynamic loading, zoom issues). Use debug mode to:

- See which CSS selectors are matching
- Inspect the raw page HTML (saved to `metadata/_debug_*.html`)
- Watch the browser work with `--no-headless`

If diagrams aren't being captured correctly, the debug HTML files will show the actual DOM structure so you can tune the selectors.

## Security Notes

- Store Confluence credentials securely (consider environment variables for production)
- Run on internal network only (diagrams may contain sensitive architecture)
- Consider adding authentication (basic auth, LDAP, SSO) for production use

## Tech Stack

- **Python 3.8+**
- **Flask** - Web framework
- **SQLite** - Metadata storage
- **Whoosh** - Full-text search
- **lxml** - XML parsing (optional)

## Requirements

- Confluence Data Center/Server with REST API access
- draw.io for Confluence plugin installed
- Python 3.8 or higher
- Network access to Confluence server

## License

MIT License
