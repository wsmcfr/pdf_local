from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from ..utils.paths import is_supported

router = APIRouter()


def runner(request: Request):
    return request.app.state.runner


def _existing_job(job_id: str, request: Request) -> dict:
    job = runner(request).get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="JOB_NOT_FOUND")
    return job


@router.post("/scan")
def scan(payload: dict, request: Request) -> dict:
    root_path = payload.get("root_path")
    if not root_path:
        raise HTTPException(status_code=400, detail="root_path required")

    root = Path(str(root_path)).expanduser()
    if not root.exists() or not root.is_dir():
        raise HTTPException(status_code=400, detail="INVALID_ROOT")

    return runner(request).enqueue("scan", {"root_path": str(root.resolve())})


@router.post("/import/files")
def import_files(payload: dict, request: Request) -> dict:
    raw_paths = payload.get("paths")
    if not isinstance(raw_paths, list) or not raw_paths:
        raise HTTPException(status_code=400, detail="paths required")

    paths: list[str] = []
    for raw in raw_paths:
        path = Path(str(raw)).expanduser()
        if not path.is_file():
            raise HTTPException(status_code=400, detail="INVALID_FILE")
        if not is_supported(path):
            raise HTTPException(status_code=400, detail="UNSUPPORTED_FILE")
        paths.append(str(path.resolve()))

    return runner(request).enqueue("import", {"paths": paths})


@router.get("/jobs")
def list_jobs(request: Request) -> list[dict]:
    return runner(request).list_jobs()


@router.get("/jobs/{job_id}")
def get_job(job_id: str, request: Request) -> dict:
    return _existing_job(job_id, request)


@router.post("/jobs/{job_id}/pause")
def pause(job_id: str, request: Request) -> dict:
    _existing_job(job_id, request)
    runner(request).request_pause(job_id)
    return runner(request).get(job_id)


@router.post("/jobs/{job_id}/resume")
def resume(job_id: str, request: Request) -> dict:
    _existing_job(job_id, request)
    runner(request).request_resume(job_id)
    return runner(request).get(job_id)


@router.post("/jobs/{job_id}/cancel")
def cancel(job_id: str, request: Request) -> dict:
    _existing_job(job_id, request)
    runner(request).request_cancel(job_id)
    return runner(request).get(job_id)
