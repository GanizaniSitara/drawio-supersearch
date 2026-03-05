# DrawIO Diagram Server

A standalone static site generator that turns a folder of `.drawio` files into a **Confluence-like browsable diagram portal**. No database, no indexing step — point it at a directory and get a fully navigable site with interactive diagrams.

Built for demoing DrawIO adoption to your org: "this is what our architecture docs look like in DrawIO."

## Quick Start

```bash
# Live dev server
python serve.py /path/to/diagrams

# Generate static site
python serve.py /path/to/diagrams --generate-static -o ./site

# Custom port
python serve.py /path/to/diagrams --port 9000
```

The generated site can be served by anything: nginx, Apache, S3, GitHub Pages, `python -m http.server`.

## How It Works

```
your-diagrams/
├── TEAM-A/
│   ├── system-context.drawio
│   ├── deployment.drawio
│   └── network.drawio
├── TEAM-B/
│   ├── data-pipeline.drawio
│   └── auth-flow.drawio
└── standalone.drawio
```

Subdirectories become **spaces** (like Confluence spaces). Files in the root go into a "_root" space.

The generator produces:

| Page | Description |
|------|-------------|
| `index.html` | Space overview with diagram counts |
| `space_TEAM-A.html` | Grid of diagrams in that space with thumbnails |
| `view_TEAM-A_system-context.html` | Interactive viewer with sidebar, metadata, page properties |
| `all.html` | All diagrams across all spaces |
| `search.html` | Client-side full-text search |

## Confluence-Style UI

Every viewer page includes:

- **Top nav bar** — Atlassian-blue header with spaces/all/search navigation
- **Sidebar page tree** — lists all diagrams in the current space, highlights current page, filterable
- **Page title** — large Confluence-style heading
- **Metadata bar** — space label, modification timestamp, page/tab count
- **Page tabs** — when a `.drawio` has multiple tabs (diagram pages)
- **Interactive canvas** — live rendering via [diagrams.net viewer](https://www.diagrams.net/) (zoom, pan, multi-page support)
- **Page properties panel** — space, modified date, pages, download link
- **Clickthrough** — click a shape label that matches another diagram name to navigate there
- **Keyboard navigation** — left/right arrows to browse prev/next diagram

## Confluence Enrichment

For C4 Level 1 (System Context) diagrams, optionally pull application descriptions from Confluence.

### Usage

```bash
# Using settings.ini from the parent project
python serve.py /path/to/diagrams --enrich

# Explicit Confluence credentials (Data Center / Server)
python serve.py /path/to/diagrams \
  --confluence-url https://confluence.example.com \
  --confluence-user svc_account \
  --confluence-pass secret

# Confluence Cloud (API token)
python serve.py /path/to/diagrams \
  --confluence-url https://yoursite.atlassian.net \
  --confluence-token your-api-token

# Static site with enrichment
python serve.py /path/to/diagrams --generate-static -o ./site --enrich
```

### How Detection Works

A diagram is identified as C4 L1 if:

1. **Filename matches** patterns like `c4-context`, `system-architecture`, `landscape`, `application-architecture`, `high-level-architecture` → confidence 0.8-0.9
2. **Content heuristic** — if extracted shape text contains 3+ application keywords (`service`, `api`, `database`, `platform`, etc.) → confidence 0.6

### How Enrichment Works

1. Extract application/system names from `.drawio` shapes (vertex boxes, C4-styled shapes)
2. For each application name, search Confluence via CQL: `type=page AND title~"App Name"`
3. Fetch the matching page body and synthesize a 1-3 sentence "about" blurb
4. Cache results (same app in multiple diagrams only fetches once)
5. Render an **"Applications in this Diagram"** panel on the viewer page

Each application entry shows:
- Application name
- **C4** badge (if the shape uses C4 notation)
- About text (from Confluence or from the shape's inline description)
- "View in Confluence →" link

### Standalone Detection

Test detection without Confluence:

```bash
# Just detect — show which diagrams look like C4 and what apps are in them
python enrich.py /path/to/diagrams --detect-only

# Full enrichment test
python enrich.py /path/to/diagrams \
  --confluence-url https://confluence.example.com \
  --username admin --password secret
```

## CLI Reference

```
usage: serve.py [-h] [--port PORT] [--host HOST] [--generate-static]
                [--output OUTPUT] [--enrich] [--confluence-url URL]
                [--confluence-user USER] [--confluence-pass PASS]
                [--confluence-token TOKEN]
                diagrams_dir

positional arguments:
  diagrams_dir          Directory containing .drawio files (scanned recursively)

options:
  --port PORT           Server port (default: 8080)
  --host HOST           Server host (default: 0.0.0.0)
  --generate-static     Generate static HTML site instead of running a server
  --output, -o OUTPUT   Output directory for static generation (default: ./drawio-site)
  --enrich              Enrich C4 diagrams with Confluence page descriptions
  --confluence-url URL  Confluence base URL (overrides settings.ini)
  --confluence-user     Confluence username (overrides settings.ini)
  --confluence-pass     Confluence password (overrides settings.ini)
  --confluence-token    Confluence API token for Cloud
```

## Architecture

```
drawio-server/
├── serve.py       # Main server + static site generator
│                  #   - scan_diagrams(): recursive .drawio discovery
│                  #   - extract_metadata(): text + tab names from XML
│                  #   - generate_*_page(): HTML template functions
│                  #   - generate_static_site(): full site builder
│                  #   - run_server(): live dev server (http.server)
├── enrich.py      # Confluence enrichment module
│                  #   - is_c4_l1_diagram(): C4 detection heuristics
│                  #   - extract_application_names(): shape parsing
│                  #   - ConfluenceClient: REST API client
│                  #   - enrich_diagrams(): main enrichment pipeline
└── README.md
```

No external dependencies required. Optional: `requests` (for Confluence enrichment), `lxml` (faster XML parsing).

## PNG Thumbnails

If `.png` files exist alongside `.drawio` files (same name, same directory), they'll be used as thumbnails in the space and all-diagrams views. The parent project's extractor downloads these from Confluence automatically.

## Tips for Demos

1. **Organize by team** — put each team's diagrams in a subdirectory (becomes a space)
2. **Name diagrams clearly** — names become page titles and are searchable
3. **Use multiple tabs** — multi-tab `.drawio` files show as page tabs in the viewer
4. **Include PNGs** — export PNGs alongside drawio files for thumbnail previews
5. **Enable enrichment** — `--enrich` pulls real descriptions from Confluence, making the demo feel like a live system
