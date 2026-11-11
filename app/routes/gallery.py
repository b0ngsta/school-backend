"""School photo gallery — managers upload / delete; everyone signed in can see it.

- GET    /gallery            newest first (paged with ?limit=&offset=)
- POST   /gallery            multipart: file (JPEG / PNG / WebP, 8 MB), caption?
- DELETE /gallery/{id}
"""
from fastapi import APIRouter, Depends, File, Form, Response, UploadFile

from app.core.roles import MANAGERS
from app.db import Db
from app.dependencies import get_current_user, get_db, page_params, paged, require_roles
from app.models.auth import CurrentUser
from app.services import gallery as service
from app.services.pagination import Page

router = APIRouter(prefix="/gallery", tags=["gallery"])

manager = require_roles(*MANAGERS)


@router.get("")
def list_photos(response: Response, page: Page = Depends(page_params),
                user: CurrentUser = Depends(get_current_user), db: Db = Depends(get_db)):
    return paged(response, service.list_photos(db, page))


@router.post("", status_code=201)
def add_photo(file: UploadFile = File(...), caption: str | None = Form(None),
              actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    return service.add_photo(db, actor, file, caption)


@router.delete("/{photo_id}", status_code=204)
def delete_photo(photo_id: int, actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    service.delete_photo(db, actor, photo_id)
    return Response(status_code=204)
