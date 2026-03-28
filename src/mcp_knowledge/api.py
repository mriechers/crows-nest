"""HTTP API for crows-nest — serves search over knowledge base and media archive.

Endpoints:
    POST /search       — Combined semantic + keyword search
    GET  /status       — Index health dashboard
    POST /reindex      — Trigger media archive reindex
    GET  /health       — Simple liveness check
"""
import asyncio
import logging

from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

logger = logging.getLogger("mcp_knowledge.api")


def create_api(semantic_index, knowledge_base=None) -> Starlette:
    """Create the HTTP API application."""

    async def search(request: Request) -> JSONResponse:
        body = await request.json()
        query = body.get("query")
        if not query:
            return JSONResponse({"error": "query is required"}, status_code=400)
        n_results = body.get("n_results", 10)
        platform = body.get("platform")

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

    async def status(request: Request) -> JSONResponse:
        semantic_status = await asyncio.to_thread(semantic_index.get_status)
        return JSONResponse({"semantic": semantic_status, "service": "crows-nest"})

    async def reindex(request: Request) -> JSONResponse:
        return JSONResponse({"error": "reindex not yet wired"}, status_code=501)

    async def health(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "service": "crows-nest"})

    app = Starlette(
        routes=[
            Route("/search", search, methods=["POST"]),
            Route("/status", status, methods=["GET"]),
            Route("/reindex", reindex, methods=["POST"]),
            Route("/health", health, methods=["GET"]),
        ],
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["app://obsidian.md", "http://localhost", "http://127.0.0.1"],
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )
    return app


async def start_api_server(semantic_index, knowledge_base=None,
                           host="127.0.0.1", port=27185, log_level="info"):
    """Start the HTTP API server as a background task."""
    import uvicorn
    app = create_api(semantic_index, knowledge_base)
    config = uvicorn.Config(app, host=host, port=port, log_level=log_level)
    server = uvicorn.Server(config)
    logger.info("HTTP API starting on %s:%d", host, port)
    await server.serve()
