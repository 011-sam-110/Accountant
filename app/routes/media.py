"""Blob endpoints (foundation-owned).

- PUT /api/blob/{key} — local-storage receiver for the browser's direct upload.
  Re-encodes via Pillow to strip EXIF/GPS and validate it's really an image
  (plan §8 hardening). In prod (R2) the browser PUTs straight to R2 instead.
- GET /api/blob/{key} — auth-checked display. Ownership is enforced by the key
  prefix (keys are namespaced `userId/...`); a foreign key returns 404.
"""
from __future__ import annotations

import io

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from PIL import Image

from ..config import settings
from ..models import AppUser
from ..security import require_user
from ..storage import get_storage

router = APIRouter()

MAX_UPLOAD = 12 * 1024 * 1024  # 12 MB raw cap


def _own(key: str, user: AppUser):
    if not key.startswith(f"{user.id}/"):
        raise HTTPException(status_code=404, detail="Not found")


@router.put("/api/blob/{key:path}")
async def put_blob(key: str, request: Request, user: AppUser = Depends(require_user)):
    _own(key, user)
    data = await request.body()
    if not data or len(data) > MAX_UPLOAD:
        raise HTTPException(status_code=400, detail="That file is too large.")
    try:
        img = Image.open(io.BytesIO(data))
        img = img.convert("RGB")
        img.thumbnail((2000, 2000))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=82)  # re-encode strips EXIF/GPS
        clean = buf.getvalue()
    except Exception:
        raise HTTPException(status_code=400, detail="That didn't look like a photo.")
    get_storage().put_bytes(key, clean, "image/jpeg")
    return {"ok": True, "key": key, "bytes": len(clean)}


@router.get("/api/blob/{key:path}")
def get_blob(key: str, user: AppUser = Depends(require_user)):
    _own(key, user)
    storage = get_storage()
    if settings.effective_storage == "r2":
        return RedirectResponse(storage.display_url(key))
    try:
        data = storage.get_bytes(key)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Not found")
    return Response(content=data, media_type="image/jpeg",
                    headers={"Cache-Control": "private, max-age=3600"})
