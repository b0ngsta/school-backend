"""Report-card designs and print runs saved for later (web app "Report Cards"). MANAGERS only.

- GET    /print-templates[?kind=report_card|report_card_back]   default first, then newest
- GET    /print-templates/{id}
- POST   /print-templates          {name, kind, is_default, layout}
- PUT    /print-templates/{id}     same body (is_default: true clears the other default of that kind)
- DELETE /print-templates/{id}

- GET    /print-batches[?status=open|done|all]                  report cards waiting to print
- GET    /print-batches/{id}
- POST   /print-batches            {source, exam_id?, class_id?, front_template_id?, back_template_id?, title,
                                    pending: [{student_id, section_id?, full_name?, missing[]}], printed_count}
- PUT    /print-batches/{id}       {pending?, printed_count?, status?}   (empty pending → done)
- DELETE /print-batches/{id}
"""
from fastapi import APIRouter, Depends, Query, Response

from app.core.roles import MANAGERS
from app.db import Db
from app.dependencies import get_db, require_roles
from app.models.auth import CurrentUser
from app.models.print_templates import PrintBatchCreate, PrintBatchUpdate, PrintTemplateSave
from app.services import print_templates as service

manager = require_roles(*MANAGERS)

router = APIRouter(tags=["print templates"])


@router.get("/print-templates")
def list_templates(kind: str | None = Query(None, pattern="^(report_card|report_card_back)$"),
                   user: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    return service.list_templates(db, kind)


@router.get("/print-templates/{template_id}")
def get_template(template_id: int, user: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    return service.get_template(db, template_id)


@router.post("/print-templates", status_code=201)
def create_template(body: PrintTemplateSave, actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    return service.create_template(db, actor, body)


@router.put("/print-templates/{template_id}")
def update_template(template_id: int, body: PrintTemplateSave, actor: CurrentUser = Depends(manager),
                    db: Db = Depends(get_db)):
    return service.update_template(db, actor, template_id, body)


@router.delete("/print-templates/{template_id}", status_code=204)
def delete_template(template_id: int, actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    service.delete_template(db, actor, template_id)
    return Response(status_code=204)


@router.get("/print-batches")
def list_batches(status: str = Query("open", pattern="^(open|done|all)$"),
                 user: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    return service.list_batches(db, None if status == "all" else status)


@router.get("/print-batches/{batch_id}")
def get_batch(batch_id: int, user: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    return service.get_batch(db, batch_id)


@router.post("/print-batches", status_code=201)
def create_batch(body: PrintBatchCreate, actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    return service.create_batch(db, actor, body)


@router.put("/print-batches/{batch_id}")
def update_batch(batch_id: int, body: PrintBatchUpdate, actor: CurrentUser = Depends(manager),
                 db: Db = Depends(get_db)):
    return service.update_batch(db, actor, batch_id, body)


@router.delete("/print-batches/{batch_id}", status_code=204)
def delete_batch(batch_id: int, actor: CurrentUser = Depends(manager), db: Db = Depends(get_db)):
    service.delete_batch(db, actor, batch_id)
    return Response(status_code=204)
