import os
import zipfile
import io
from datetime import datetime
from fastapi import APIRouter, Depends, Request, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ..database import get_db, User, ReportRequest
from ..dependencies import require_staff
from ..services.claude_service import extract_next_report_date, extract_full_tm47_data
from ..services.storage_service import save_upload, read_file_bytes, file_exists, get_ext
from ..services.line_service import send_completion_notification

router = APIRouter(prefix="/staff", tags=["staff"])
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))


# ──────────────────────────────────────────────────────────────────
# New state machine
#
# ONLINE:
#   pending_payment → reviewing → ready_to_submit → submitted_online
#                  → receipt_uploaded → completed
#
# OFFLINE:
#   pending_payment → processing → docs_downloaded
#                  → receipt_uploaded → completed
# ──────────────────────────────────────────────────────────────────
ACTIVE_STATUSES = [
    "pending_payment",
    "reviewing", "ready_to_submit", "submitted_online",
    "processing", "docs_downloaded",
    "receipt_uploaded",
]


@router.get("/dashboard", response_class=HTMLResponse)
def staff_dashboard(request: Request, user: User = Depends(require_staff), db: Session = Depends(get_db)):
    queue = (
        db.query(ReportRequest)
        .filter(ReportRequest.status.in_(ACTIVE_STATUSES))
        .join(User, ReportRequest.worker_id == User.id)
        .order_by(ReportRequest.created_at.asc())
        .all()
    )
    completed = (
        db.query(ReportRequest)
        .filter(ReportRequest.status == "completed")
        .order_by(ReportRequest.updated_at.desc())
        .limit(20)
        .all()
    )
    return templates.TemplateResponse("staff_dashboard.html", {
        "request": request, "user": user, "queue": queue, "completed": completed
    })


@router.get("/job/{report_id}", response_class=HTMLResponse)
def job_detail(request: Request, report_id: int, user: User = Depends(require_staff), db: Session = Depends(get_db)):
    report = db.query(ReportRequest).filter(ReportRequest.id == report_id).first()
    if not report:
        raise HTTPException(404, "ไม่พบรายการ")
    return templates.TemplateResponse("staff_job_detail.html", {
        "request": request, "user": user, "report": report
    })


# ═══ OFFLINE: download documents → status = docs_downloaded ═══
@router.get("/job/{report_id}/download-docs")
def download_documents(report_id: int, user: User = Depends(require_staff), db: Session = Depends(get_db)):
    report = db.query(ReportRequest).filter(ReportRequest.id == report_id).first()
    if not report:
        raise HTTPException(404, "ไม่พบรายการ")
    if report.status == "pending_payment":
        raise HTTPException(400, "ยังไม่ได้ชำระเงิน")

    # Offline step 2 → 3 (สตาฟดาวน์โหลดเอกสารแล้ว, รอ ตม. ตอบกลับ)
    if report.status in ("processing",):
        report.status = "docs_downloaded"
    report.doc_downloaded_at = datetime.utcnow()
    db.commit()

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for src, base_name in [
            (report.passport_file,   f"passport_{report_id}"),
            (report.visa_file,       f"visa_{report_id}"),
            (report.old_report_file, f"old_report_{report_id}"),
        ]:
            if src and file_exists(src):
                zf.writestr(f"{base_name}{get_ext(src)}", read_file_bytes(src))
    zip_buffer.seek(0)

    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=job_{report_id}_docs.zip"},
    )


# ═══ OFFLINE: download mailing address (สำหรับส่งใบใหม่คืนลูกค้า) ═══
@router.get("/job/{report_id}/download-address")
def download_address(report_id: int, user: User = Depends(require_staff), db: Session = Depends(get_db)):
    report = db.query(ReportRequest).filter(ReportRequest.id == report_id).first()
    if not report:
        raise HTTPException(404, "ไม่พบรายการ")

    addr = report.mailing_address or {}
    extracted = report.extracted_data or {}
    full_name = extracted.get("full_name", "")
    address_text = (
        f"ชื่อ: {full_name}\n"
        f"ที่อยู่: {addr.get('street', '')}\n"
        f"ตำบล {addr.get('tambol', '')} อำเภอ {addr.get('amphur', '')}\n"
        f"จังหวัด {addr.get('province', '')}\n"
        f"โทร: {addr.get('phone', '')}"
    )

    report.address_downloaded_at = datetime.utcnow()
    db.commit()

    return StreamingResponse(
        io.BytesIO(address_text.encode("utf-8")),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=address_{report_id}.txt"},
    )


# ═══ ONLINE: Extract ═══
@router.post("/job/{report_id}/extract-data")
async def extract_tm47_data(report_id: int, user: User = Depends(require_staff), db: Session = Depends(get_db)):
    report = db.query(ReportRequest).filter(ReportRequest.id == report_id).first()
    if not report:
        raise HTTPException(404, "ไม่พบรายการ")
    if report.submission_mode != "online":
        raise HTTPException(400, "ใช้เฉพาะ online mode")

    try:
        data = await extract_full_tm47_data(report.passport_file, report.visa_file)
    except Exception as e:
        print(f"[extract-data] error: {e}")
        data = {}

    def _keep(v):
        if v is None: return False
        if isinstance(v, str) and v.strip() == "": return False
        if isinstance(v, int) and v == 0: return False
        return True

    mapping = {
        "passport_no": "passport_no", "nationality": "nationality",
        "surname": "surname", "given_name": "given_name", "middle_name": "middle_name",
        "gender": "gender", "dob_day": "dob_day", "dob_month": "dob_month",
        "dob_year": "dob_year", "visa_expire": "visa_expire",
    }
    for field, key in mapping.items():
        v = data.get(key)
        if _keep(v):
            setattr(report, field, v)
    db.commit()

    return JSONResponse(data or {})


async def _save_tm47_fields(request: Request, report: ReportRequest) -> None:
    body = await request.json()
    report.passport_no  = body.get("passport_no",  report.passport_no)
    report.nationality  = body.get("nationality",  report.nationality)
    report.surname      = body.get("surname",      report.surname)
    report.given_name   = body.get("given_name",   report.given_name)
    report.middle_name  = body.get("middle_name",  report.middle_name or "")
    report.gender       = body.get("gender",       report.gender)
    report.dob_day      = body.get("dob_day",      report.dob_day)
    report.dob_month    = body.get("dob_month",    report.dob_month)
    report.dob_year     = body.get("dob_year",     report.dob_year)
    report.arrival_date = body.get("arrival_date", report.arrival_date)
    report.visa_expire  = body.get("visa_expire",  report.visa_expire)
    report.building_name = body.get("building_name", report.building_name or "")
    report.address_no   = body.get("address_no",   report.address_no or "")
    report.road         = body.get("road",          report.road or "")
    report.province     = body.get("province",      report.province)
    report.city         = body.get("city",          report.city)
    report.district     = body.get("district",      report.district)
    report.tm47_email    = body.get("tm47_email",    report.tm47_email)
    report.tm47_password = body.get("tm47_password", report.tm47_password)


# ═══ ONLINE step 2 → 3: Staff saves data ═══
@router.post("/job/{report_id}/save-data")
async def save_tm47_data(
    report_id: int,
    request: Request,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    report = db.query(ReportRequest).filter(ReportRequest.id == report_id).first()
    if not report:
        raise HTTPException(404, "ไม่พบรายการ")
    await _save_tm47_fields(request, report)
    report.data_confirmed_at = datetime.utcnow()
    # เลื่อนสถานะครั้งแรก (reviewing → ready_to_submit) — ครั้งต่อไปเก็บสถานะเดิม
    if report.status == "reviewing":
        report.status = "ready_to_submit"
    db.commit()
    return JSONResponse({
        "message": "บันทึกข้อมูลแล้ว",
        "status": report.status,
        "saved_at": report.data_confirmed_at.isoformat(),
    })


# backward-compat alias
@router.post("/job/{report_id}/confirm-data")
async def confirm_tm47_data(
    report_id: int,
    request: Request,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    report = db.query(ReportRequest).filter(ReportRequest.id == report_id).first()
    if not report:
        raise HTTPException(404, "ไม่พบรายการ")
    await _save_tm47_fields(request, report)
    report.data_confirmed_at = datetime.utcnow()
    db.commit()
    return JSONResponse({"message": "บันทึกข้อมูลแล้ว"})


# ═══ ONLINE step 3 → 4: Staff submits to ตม.website ═══
@router.post("/job/{report_id}/submit-immigration")
async def submit_to_immigration(
    report_id: int,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    report = db.query(ReportRequest).filter(ReportRequest.id == report_id).first()
    if not report:
        raise HTTPException(404, "ไม่พบรายการ")
    if report.submission_mode != "online":
        raise HTTPException(400, "ใช้เฉพาะ online mode")
    if report.status not in ("reviewing", "ready_to_submit"):
        raise HTTPException(400, f"สถานะไม่ถูกต้อง ({report.status})")

    report.status = "submitted_online"
    report.tm47_submitted_at = datetime.utcnow()
    if not report.data_confirmed_at:
        report.data_confirmed_at = datetime.utcnow()
    db.commit()
    return JSONResponse({"message": "สถานะอัพเดตเป็น 'ยื่น ตม. ออนไลน์แล้ว'"})


# ═══ ONLINE step 4 + OFFLINE step 3 → 4: Upload receipt from ตม. ═══
@router.post("/job/{report_id}/upload-receipt")
async def upload_receipt(
    report_id: int,
    receipt: UploadFile = File(...),
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    """
    Staff อัพโหลดใบรายงานตัวใบใหม่จาก ตม.
    → status = receipt_uploaded (ยังไม่ completed — ต้องรอสตาฟกดส่งให้ลูกค้าก่อน)
    → Claude extract วันครบกำหนดถัดไป
    """
    report = db.query(ReportRequest).filter(ReportRequest.id == report_id).first()
    if not report:
        raise HTTPException(404, "ไม่พบรายการ")

    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    ext = os.path.splitext(receipt.filename or "")[1] or ".jpg"
    receipt_bytes = await receipt.read()
    receipt_url = save_upload(
        receipt_bytes,
        f"{report.worker_id}/receipts",
        f"receipt_{report_id}_{timestamp}{ext}",
    )
    report.receipt_file = receipt_url
    report.status = "receipt_uploaded"
    report.receipt_uploaded_at = datetime.utcnow()
    db.commit()

    # Claude อ่านวันครบกำหนดถัดไป (non-blocking)
    next_date = None
    try:
        next_date = await extract_next_report_date(receipt_url)
    except Exception as e:
        print(f"[WARN] extract_next_report_date failed: {e}")

    worker_user = db.query(User).filter(User.id == report.worker_id).first()
    if next_date:
        report.next_report_date_extracted = next_date
        if worker_user:
            worker_user.next_report_date = next_date
        db.commit()

    return JSONResponse({
        "message": "อัพโหลดใบรายงานตัวสำเร็จ",
        "next_report_date": next_date.strftime("%d/%m/%Y") if next_date else None,
    })


# ═══ ONLINE step 5: Send via LINE → completed ═══
@router.post("/job/{report_id}/send-line")
async def send_document_via_line(
    report_id: int,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    report = db.query(ReportRequest).filter(ReportRequest.id == report_id).first()
    if not report:
        raise HTTPException(404, "ไม่พบรายการ")
    if report.submission_mode != "online":
        raise HTTPException(400, "ใช้เฉพาะ online mode")
    if report.status != "receipt_uploaded":
        raise HTTPException(400, "ต้องอัพโหลดใบรายงานตัวใบใหม่ก่อน")

    worker_user = db.query(User).filter(User.id == report.worker_id).first()
    try:
        if worker_user and worker_user.line_user_id:
            send_completion_notification(worker_user, report.next_report_date_extracted)
    except Exception as e:
        print(f"[WARN] LINE send failed: {e}")

    report.status = "completed"
    report.completed_at = datetime.utcnow()
    db.commit()

    return JSONResponse({"message": "ส่ง LINE แล้ว สถานะ → completed"})


# ═══ OFFLINE step 4: Mail receipt back to worker → completed ═══
@router.post("/job/{report_id}/mark-mailed")
async def mark_mailed_to_worker(
    report_id: int,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    report = db.query(ReportRequest).filter(ReportRequest.id == report_id).first()
    if not report:
        raise HTTPException(404, "ไม่พบรายการ")
    if report.submission_mode != "offline":
        raise HTTPException(400, "ใช้เฉพาะ offline mode")
    if report.status != "receipt_uploaded":
        raise HTTPException(400, "ต้องอัพโหลดใบรายงานตัวใบใหม่ก่อน")

    report.status = "completed"
    report.completed_at = datetime.utcnow()
    db.commit()
    return JSONResponse({"message": "ส่งใบรายงานตัวคืนลูกค้าแล้ว สถานะ → completed"})
