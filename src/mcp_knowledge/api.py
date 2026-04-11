"""HTTP API for crows-nest — serves search over knowledge base and media archive.

Endpoints:
    POST /search       — Combined semantic + keyword search
    GET  /status       — Index health dashboard
    POST /reindex      — Trigger media archive reindex
    GET  /health       — Simple liveness check
    POST /add-link     — Queue a URL for the pipeline (token-authed)
"""
import asyncio
import logging
import os
import sqlite3
import sys
from pathlib import Path

from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

logger = logging.getLogger("mcp_knowledge.api")

# Make the pipeline package importable for /add-link. api.py lives at
# src/mcp_knowledge/api.py; the pipeline package is at the project root.
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


def create_api(
    semantic_index,
    knowledge_base=None,
    *,
    api_token: str | None = None,
    db_path: str | None = None,
) -> Starlette:
    """Create the HTTP API application.

    Args:
        semantic_index: Semantic search backend.
        knowledge_base: Optional keyword search backend.
        api_token: Shared secret required on ``/add-link`` via the
            ``X-Crows-Nest-Token`` header. If None, read from the
            ``CROWS_NEST_API_TOKEN`` environment variable at call time.
            When no token is configured, ``/add-link`` returns 503.
        db_path: SQLite path for ``/add-link``. If None, falls back to
            ``pipeline.config.DB_PATH`` at request time.
    """
    resolved_token = api_token if api_token is not None else os.environ.get(
        "CROWS_NEST_API_TOKEN", ""
    )

    async def search(request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid JSON body"}, status_code=400)

        query = body.get("query")
        if not query:
            return JSONResponse({"error": "query is required"}, status_code=400)
        n_results = body.get("n_results", 10)
        platform = body.get("platform")

        try:
            # Semantic search over media archive
            semantic_results = await asyncio.to_thread(
                semantic_index.search, query=query, n_results=n_results, platform=platform,
            )

            # Keyword search over knowledge base (if available)
            keyword_results = []
            if knowledge_base:
                keyword_results = await asyncio.to_thread(
                    knowledge_base.search_knowledge, query=query, max_results=n_results,
                )

            return JSONResponse({"results": semantic_results + keyword_results})
        except Exception as exc:
            logger.exception("Search failed")
            return JSONResponse({"error": str(exc)}, status_code=500)

    async def status(request: Request) -> JSONResponse:
        try:
            semantic_status = await asyncio.to_thread(semantic_index.get_status)
        except Exception as exc:
            logger.exception("Status check failed")
            return JSONResponse({"error": str(exc)}, status_code=500)
        return JSONResponse({"semantic": semantic_status, "service": "crows-nest"})

    async def reindex(request: Request) -> JSONResponse:
        return JSONResponse({"error": "reindex not yet wired"}, status_code=501)

    async def health(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "service": "crows-nest"})

    async def add_link_endpoint(request: Request) -> JSONResponse:
        if not resolved_token:
            return JSONResponse(
                {
                    "error": (
                        "/add-link is disabled: set CROWS_NEST_API_TOKEN "
                        "to enable it"
                    )
                },
                status_code=503,
            )

        supplied = request.headers.get("X-Crows-Nest-Token", "")
        if supplied != resolved_token:
            return JSONResponse({"error": "invalid token"}, status_code=401)

        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid JSON body"}, status_code=400)

        url = (body or {}).get("url")
        if not url or not isinstance(url, str):
            return JSONResponse(
                {"error": "url is required (string)"}, status_code=400,
            )

        context = body.get("context")
        source_type = body.get("source_type") or "http"

        # Imported lazily so tests that don't exercise this route don't
        # incur the pipeline import cost.
        try:
            from pipeline.db import add_link, init_db
            from pipeline.content_types import classify_url
            from pipeline.config import DB_PATH as _DEFAULT_DB_PATH
        except ImportError as exc:
            logger.exception("pipeline import failed for /add-link")
            return JSONResponse(
                {"error": f"pipeline unavailable: {exc}"},
                status_code=500,
            )

        target_db = db_path or _DEFAULT_DB_PATH
        content_type = classify_url(url)

        def _insert() -> int:
            init_db(target_db)
            return add_link(
                url=url,
                source_type=source_type,
                sender=None,
                context=context,
                content_type=content_type,
                db_path=target_db,
            )

        try:
            link_id = await asyncio.to_thread(_insert)
        except sqlite3.IntegrityError:
            return JSONResponse(
                {"error": "url already queued", "url": url},
                status_code=409,
            )
        except Exception as exc:
            logger.exception("add_link failed")
            return JSONResponse({"error": str(exc)}, status_code=500)

        return JSONResponse(
            {
                "id": link_id,
                "status": "queued",
                "content_type": content_type,
                "source_type": source_type,
            },
            status_code=201,
        )

    app = Starlette(
        routes=[
            Route("/search", search, methods=["POST"]),
            Route("/status", status, methods=["GET"]),
            Route("/reindex", reindex, methods=["POST"]),
            Route("/health", health, methods=["GET"]),
            Route("/add-link", add_link_endpoint, methods=["POST"]),
        ],
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["app://obsidian.md", "http://localhost", "http://127.0.0.1"],
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*", "X-Crows-Nest-Token"],
    )
    return app


async def start_api_server(semantic_index, knowledge_base=None,
                           host="127.0.0.1", port=27185, log_level="info",
                           api_token: str | None = None,
                           db_path: str | None = None):
    """Start the HTTP API server as a background task.

    ``api_token`` and ``db_path`` default to the environment/pipeline defaults
    when omitted, which is the usual production path.
    """
    import uvicorn
    app = create_api(
        semantic_index,
        knowledge_base,
        api_token=api_token,
        db_path=db_path,
    )
    config = uvicorn.Config(app, host=host, port=port, log_level=log_level)
    server = uvicorn.Server(config)
    logger.info("HTTP API starting on %s:%d", host, port)
    if os.environ.get("CROWS_NEST_API_TOKEN"):
        logger.info("HTTP API: /add-link enabled (token auth)")
    else:
        logger.info("HTTP API: /add-link disabled (set CROWS_NEST_API_TOKEN to enable)")
    await server.serve()
