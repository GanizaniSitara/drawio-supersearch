"""
FastAPI Server for Diagram Conversion Browser

Provides:
- Browse converted DrawIO diagrams by space/type
- Full-text search across diagram content
- DrawIO rendering in browser (diagrams.net viewer)
- Clickthrough navigation between linked diagrams
- C4 architecture model viewer with drill-down
- Confluence page linkback
- Review queue for low-confidence conversions
"""

import os
import json
import logging
from typing import Optional

from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..config import ConversionConfig
from ..pipeline.database import ConversionDB
from ..c4.repository import C4Repository

logger = logging.getLogger(__name__)

app = FastAPI(title="Diagram Conversion Browser", version="0.1.0")

# Global state (initialized in create_app)
_config: Optional[ConversionConfig] = None
_db: Optional[ConversionDB] = None
_c4_repo: Optional[C4Repository] = None

# Templates and static files
_templates_dir = os.path.join(os.path.dirname(__file__), "templates")
_static_dir = os.path.join(os.path.dirname(__file__), "static")

templates = Jinja2Templates(directory=_templates_dir)

if os.path.exists(_static_dir):
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")


def get_db() -> ConversionDB:
    if _db is None:
        raise RuntimeError("Database not initialized")
    return _db


def get_c4_repo() -> C4Repository:
    if _c4_repo is None:
        raise RuntimeError("C4 repository not initialized")
    return _c4_repo


# ── Pages ───────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Home page: overview dashboard."""
    db = get_db()
    stats = db.get_stats()
    return templates.TemplateResponse("index.html", {
        "request": request,
        "stats": stats,
    })


@app.get("/browse", response_class=HTMLResponse)
async def browse(request: Request,
                 space: str = "",
                 type: str = "",
                 status: str = "",
                 page: int = 1):
    """Browse converted diagrams with filters."""
    db = get_db()
    per_page = 50
    offset = (page - 1) * per_page

    conversions = db.get_all_conversions(
        space_key=space,
        diagram_type=type,
        status=status,
        limit=per_page,
        offset=offset,
    )

    # Get filter options
    conn = db._connect()
    spaces = conn.execute(
        "SELECT DISTINCT space_key FROM conversions WHERE space_key != '' "
        "ORDER BY space_key"
    ).fetchall()
    types = conn.execute(
        "SELECT DISTINCT diagram_type FROM conversions ORDER BY diagram_type"
    ).fetchall()
    conn.close()

    return templates.TemplateResponse("browse.html", {
        "request": request,
        "conversions": conversions,
        "spaces": [r["space_key"] for r in spaces],
        "types": [r["diagram_type"] for r in types],
        "current_space": space,
        "current_type": type,
        "current_status": status,
        "page": page,
    })


@app.get("/diagram/{diagram_id}", response_class=HTMLResponse)
async def diagram_view(request: Request, diagram_id: int):
    """View a single converted diagram with DrawIO rendering."""
    db = get_db()
    conversion = db.get_conversion_by_id(diagram_id)
    if not conversion:
        raise HTTPException(status_code=404, detail="Diagram not found")

    return templates.TemplateResponse("diagram.html", {
        "request": request,
        "diagram": conversion,
        "confluence_url": _config.confluence_url if _config else "",
    })


@app.get("/review", response_class=HTMLResponse)
async def review_queue(request: Request):
    """Review queue for low-confidence conversions."""
    db = get_db()
    items = db.get_review_queue(limit=100)
    return templates.TemplateResponse("review.html", {
        "request": request,
        "items": items,
    })


@app.get("/c4", response_class=HTMLResponse)
async def c4_overview(request: Request):
    """C4 architecture repository overview."""
    repo = get_c4_repo()
    stats = repo.get_summary_stats()
    models = get_db().get_all_c4_models()
    return templates.TemplateResponse("c4_overview.html", {
        "request": request,
        "stats": stats,
        "models": models,
    })


@app.get("/c4/model/{model_id}", response_class=HTMLResponse)
async def c4_model_view(request: Request, model_id: int):
    """View a single C4 model."""
    db = get_db()
    model = db.get_c4_model(model_id)
    if not model:
        raise HTTPException(status_code=404, detail="C4 model not found")

    return templates.TemplateResponse("c4_model.html", {
        "request": request,
        "model": model,
    })


@app.get("/c4/systems", response_class=HTMLResponse)
async def c4_systems(request: Request):
    """System index across all C4 models."""
    repo = get_c4_repo()
    systems = repo.get_system_index()
    return templates.TemplateResponse("c4_systems.html", {
        "request": request,
        "systems": systems,
    })


@app.get("/c4/technologies", response_class=HTMLResponse)
async def c4_technologies(request: Request):
    """Technology inventory."""
    repo = get_c4_repo()
    inventory = repo.get_technology_inventory()
    return templates.TemplateResponse("c4_technologies.html", {
        "request": request,
        "inventory": inventory,
    })


@app.get("/search", response_class=HTMLResponse)
async def search(request: Request, q: str = ""):
    """Search converted diagrams."""
    db = get_db()
    results = db.search_conversions(q, limit=100) if q else []
    return templates.TemplateResponse("search.html", {
        "request": request,
        "query": q,
        "results": results,
    })


# ── API Endpoints ───────────────────────────────────────────────────

@app.get("/api/stats")
async def api_stats():
    """Pipeline statistics."""
    return get_db().get_stats()


@app.get("/api/conversions")
async def api_conversions(space: str = "", type: str = "",
                          status: str = "", limit: int = 50):
    return get_db().get_all_conversions(
        space_key=space, diagram_type=type, status=status, limit=limit
    )


@app.get("/api/c4/graph")
async def api_c4_graph():
    """C4 relationship graph data for visualization."""
    return get_c4_repo().get_relationship_graph()


@app.get("/api/c4/systems")
async def api_c4_systems():
    return get_c4_repo().get_system_index()


@app.get("/api/c4/technologies")
async def api_c4_technologies():
    return get_c4_repo().get_technology_inventory()


@app.get("/api/c4/stats")
async def api_c4_stats():
    return get_c4_repo().get_summary_stats()


@app.post("/api/review/{diagram_id}")
async def api_review(diagram_id: int, action: str = Query(...),
                     notes: str = ""):
    """Accept or reject a conversion from the review queue."""
    db = get_db()
    conversion = db.get_conversion_by_id(diagram_id)
    if not conversion:
        raise HTTPException(status_code=404, detail="Not found")

    if action not in ("accept", "reject"):
        raise HTTPException(status_code=400, detail="Action must be accept or reject")

    db.upsert_conversion(
        conversion["source_path"],
        review_status="accepted" if action == "accept" else "rejected",
        review_notes=notes,
    )
    return {"status": "ok", "action": action}


# ── File serving ────────────────────────────────────────────────────

@app.get("/image/{space_key}/{filename:path}")
async def serve_image(space_key: str, filename: str):
    """Serve original screenshot image."""
    if _config is None:
        raise HTTPException(status_code=500)
    path = os.path.join(_config.screenshots_dir, space_key, filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(path, media_type="image/png")


@app.get("/drawio/{space_key}/{filename:path}")
async def serve_drawio(space_key: str, filename: str):
    """Serve converted DrawIO XML file."""
    if _config is None:
        raise HTTPException(status_code=500)
    if not filename.endswith(".drawio"):
        filename += ".drawio"
    path = os.path.join(_config.drawio_output_dir, space_key, filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="DrawIO file not found")
    return FileResponse(
        path,
        media_type="application/xml",
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, OPTIONS",
        },
    )


@app.get("/c4file/{space_key}/{filename:path}")
async def serve_c4_file(space_key: str, filename: str):
    """Serve C4 model JSON or DrawIO file."""
    if _config is None:
        raise HTTPException(status_code=500)
    path = os.path.join(_config.c4_output_dir, space_key, filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="C4 file not found")
    if filename.endswith(".json"):
        return FileResponse(path, media_type="application/json")
    return FileResponse(path, media_type="application/xml")


# ── App factory ─────────────────────────────────────────────────────

def create_app(config: Optional[ConversionConfig] = None) -> FastAPI:
    """Initialize the app with configuration."""
    global _config, _db, _c4_repo

    if config is None:
        config = ConversionConfig()

    _config = config
    config.ensure_directories()

    _db = ConversionDB(config.db_path)
    _c4_repo = C4Repository(_db)

    return app
