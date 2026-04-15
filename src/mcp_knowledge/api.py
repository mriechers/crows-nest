"""HTTP API for crows-nest knowledge + pipeline server."""

from __future__ import annotations

import logging

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from service_base import Scheduler, health_endpoint

from . import knowledge
from .mcp_adapter import (
    _RSS_AVAILABLE,
    _get_server_info,
    _list_recent_articles,
    _pipeline_queue,
    _search_knowledge,
)

logger = logging.getLogger("mcp_knowledge.api")


def create_api(scheduler: Scheduler | None = None) -> Starlette:
    """Create the crows-nest HTTP API."""

    async def search(request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

        query = body.get("query")
        if not query:
            return JSONResponse({"error": "query is required"}, status_code=400)

        results = _search_knowledge(
            query=query,
            category=body.get("category"),
            max_results=body.get("max_results", 5),
            full_document=body.get("full_document", False),
        )
        return JSONResponse({"results": results})

    async def status(request: Request) -> JSONResponse:
        info = _get_server_info()
        payload = dict(info)
        if scheduler:
            payload["jobs"] = scheduler.get_status()
        return JSONResponse(payload)

    async def pipeline(request: Request) -> JSONResponse:
        recent_limit = int(request.query_params.get("recent_limit", 20))
        result = _pipeline_queue(recent_limit=recent_limit)
        return JSONResponse(result)

    async def articles(request: Request) -> JSONResponse:
        limit = int(request.query_params.get("limit", 8))
        max_age_hours = int(request.query_params.get("max_age_hours", 48))
        result = _list_recent_articles(limit=limit, max_age_hours=max_age_hours)
        return JSONResponse({"articles": result})

    async def jobs(request: Request) -> JSONResponse:
        if not scheduler:
            return JSONResponse({"jobs": []})
        return JSONResponse({"jobs": scheduler.get_status()})

    async def run_job(request: Request) -> JSONResponse:
        if not scheduler:
            return JSONResponse({"error": "Scheduler not available"}, status_code=503)
        name = request.path_params["name"]
        try:
            result = await scheduler.run_now(name)
            return JSONResponse(result)
        except KeyError:
            return JSONResponse({"error": f"Unknown job: {name}"}, status_code=404)
        except Exception as exc:
            logger.error("Job %s failed on demand: %s", name, exc)
            return JSONResponse({"error": str(exc)}, status_code=500)

    app = Starlette(
        routes=[
            Route("/health", health_endpoint("crows-nest", scheduler=scheduler)),
            Route("/search", search, methods=["POST"]),
            Route("/status", status, methods=["GET"]),
            Route("/pipeline", pipeline, methods=["GET"]),
            Route("/articles", articles, methods=["GET"]),
            Route("/jobs", jobs, methods=["GET"]),
            Route("/jobs/{name}/run", run_job, methods=["POST"]),
        ],
    )
    return app
