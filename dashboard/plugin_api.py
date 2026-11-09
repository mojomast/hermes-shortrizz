"""Studio routes mounted at /api/plugins/shortrizz/.

The pane calls these with ctx.rest. Handlers return JSON. They never include
secret values, and every media path is confined to the runs root by shortrizz.studio.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Request

from shortrizz.studio import (
    StudioError,
    get_run,
    health,
    list_runs,
    open_target,
    preview,
    read_media,
    restore_version,
    save_version,
    scrub,
    start_render,
    start_repick,
    start_restyle,
    start_select,
)

router = APIRouter()


def _fail(exc: StudioError):
    return {"ok": False, "error": exc.message, "fix": exc.fix}


def _guard(fn):
    try:
        return scrub(fn())
    except StudioError as exc:
        return _fail(exc)
    except Exception:
        return {
            "ok": False,
            "error": "Studio could not finish that.",
            "fix": "Check ffmpeg, the source, and the run folder, then try again.",
        }


async def _body(request: Request) -> dict:
    raw = await request.body()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        raise StudioError("Send a JSON object.") from None
    if not isinstance(data, dict):
        raise StudioError("Send a JSON object.")
    return data


@router.get("/health")
def route_health():
    return _guard(health)


@router.get("/runs")
def route_runs():
    return _guard(list_runs)


@router.get("/runs/{run_id}")
def route_run(run_id: str):
    return _guard(lambda: get_run(run_id))


@router.get("/media")
def route_media(run: str, path: str):
    return _guard(lambda: read_media(run, path))


@router.post("/runs")
async def route_create(request: Request):
    try:
        body = await _body(request)
    except StudioError as exc:
        return _fail(exc)
    return _guard(lambda: start_select(body))


@router.post("/runs/{run_id}/picks")
async def route_save(run_id: str, request: Request):
    try:
        body = await _body(request)
    except StudioError as exc:
        return _fail(exc)
    return _guard(lambda: save_version(run_id, body))


@router.post("/runs/{run_id}/restore")
async def route_restore(run_id: str, request: Request):
    try:
        body = await _body(request)
    except StudioError as exc:
        return _fail(exc)
    return _guard(lambda: restore_version(run_id, body.get("revision")))


@router.post("/runs/{run_id}/preview")
async def route_preview(run_id: str, request: Request):
    try:
        body = await _body(request)
    except StudioError as exc:
        return _fail(exc)
    return _guard(lambda: preview(run_id, str(body.get("pick_id") or "")))


@router.post("/runs/{run_id}/render")
async def route_render(run_id: str, request: Request):
    try:
        body = await _body(request)
    except StudioError as exc:
        return _fail(exc)
    return _guard(lambda: start_render(run_id, body))


@router.post("/runs/{run_id}/restyle")
async def route_restyle(run_id: str, request: Request):
    try:
        body = await _body(request)
    except StudioError as exc:
        return _fail(exc)
    return _guard(lambda: start_restyle(run_id, body))


@router.post("/runs/{run_id}/repick")
async def route_repick(run_id: str, request: Request):
    try:
        body = await _body(request)
    except StudioError as exc:
        return _fail(exc)
    return _guard(lambda: start_repick(run_id))


@router.post("/runs/{run_id}/open")
def route_open(run_id: str):
    return _guard(lambda: open_target(run_id))
