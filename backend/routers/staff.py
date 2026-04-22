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


@router.get("/dashboard", response_class=HTMLResponse)
def staff_dashboard(request: Request, user: User = Depends(require_staff), db: Session = Depends(get_db)):
    queue = (
        db.query(ReportRequest)
        .filter(ReportRequest.status.in_([
            "processing", "pending_payment", "mailing",
            "pending_review", "pending_bot", "submitted_to_immigration", "document_sent",
        ]))
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


@router.get("/job/{report_id}/download-docs")
def download_documents(report_id: int, user: User = Depends(require_staff), db: Session = Depends(get_db)):
    """
    Staff กด Download Documents → ระบบ auto เปลี่ยนสถานะเป็น 'processing'
    ส่งกลับ ZIP: เอกสารที่ worker อัพโหลด (passport + visa + ใบเดิม ถ้ามี)
    """
    report = db.query(ReportRequest).filter(ReportRequest.id == report_id).first()
    if not report:
        raise HTTPException(404, "ไม่พบรายการ")
    if report.status == "pending_payment":
        raise HTTPException(400, "ยังไม่ได้ชำระเงิน")

    # Auto-status: processing
    if report.status not in ("processing", "mailing", "completed"):
        report.status = "processing"
    report.doc_downloaded_at = datetime.utcnow()
    db.commit()

    # Pack ZIP — เฉพาะไฟล์ที่ worker อัพโหลดมา (รองรับทั้ง local path และ Cloudinary URL)
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


@router.get("/job/{report_id}/download-address")
def download_address(report_id: int, user: User = Depends(require_staff), db: Session = Depends(get_db)):
    """
    Staff กด Download Mailing Address → ระบบ auto เปลี่ยนสถานะเป็น 'mailing'
    ส่งกลับ TXT ที่อยู่สำหรับหน้าซอง
    """
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

    # Auto-status: mailing
    if report.status in ("processing",):
        report.status = "mailing"
    report.address_downloaded_at = datetime.utcnow()
    db.commit()

    return StreamingResponse(
        io.BytesIO(address_text.encode("utf-8")),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=address_{report_id}.txt"},
    )


@router.post("/job/{report_id}/extract-data")
async def extract_tm47_data(report_id: int, user: User = Depends(require_staff), db: Session = Depends(get_db)):
    """
    Extract ข้อมูล TM47 ทั้งหมดจากเอกสาร (รอบ 2 หลังจ่ายเงิน — online mode)
    เรียกจากหน้า staff review ผ่าน AJAX เมื่อ passport_no ยังว่างอยู่
    """
    report = db.query(ReportRequest).filter(ReportRequest.id == report_id).first()
    if not report:
        raise HTTPException(404, "ไม่พบรายการ")
    if report.submission_mode != "online":
        raise HTTPException(400, "ใช้เฉพาะ online mode")

    data = await extract_full_tm47_data(report.passport_file, report.visa_file)
    if not data:
        raise HTTPException(500, "Extract ไม่สำเร็จ")

    report.passport_no  = data.get("passport_no")
    report.nationality  = data.get("nationality")
    report.surname      = data.get("surname")
    report.given_name   = data.get("given_name")
    report.middle_name  = data.get("middle_name", "")
    report.gender       = data.get("gender")
    report.dob_day      = data.get("dob_day")
    report.dob_month    = data.get("dob_month")
    report.dob_year     = data.get("dob_year")
    report.arrival_date = data.get("arrival_date")
    report.visa_expire  = data.get("visa_expire")
    db.commit()

    return JSONResponse(data)


async def _save_tm47_fields(request: Request, report: ReportRequest) -> None:
    """Helper: อ่าน JSON body แล้วเซฟลงฟิลด์ TM47 — ไม่เปลี่ยนสถานะ"""
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


@router.post("/job/{report_id}/save-data")
async def save_tm47_data(
    report_id: int,
    request: Request,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    """สตาฟกด 'บันทึก' ระหว่างทำงาน — เซฟข้อมูลเฉยๆ ไม่เปลี่ยนสถานะ"""
    report = db.query(ReportRequest).filter(ReportRequest.id == report_id).first()
    if not report:
        raise HTTPException(404, "ไม่พบรายการ")
    await _save_tm47_fields(request, report)
    report.data_confirmed_at = datetime.utcnow()
    db.commit()
    return JSONResponse({"message": "บันทึกข้อมูลแล้ว"})


# Backward-compat alias — เผื่อฟรอนต์เก่ายังเรียก confirm-data
@router.post("/job/{report_id}/confirm-data")
async def confirm_tm47_data(
    report_id: int,
    request: Request,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    """(legacy) เหมือน save-data — เก็บไว้เพื่อ backward compat"""
    report = db.query(ReportRequest).filter(ReportRequest.id == report_id).first()
    if not report:
        raise HTTPException(404, "ไม่พบรายการ")
    await _save_tm47_fields(request, report)
    report.data_confirmed_at = datetime.utcnow()
    db.commit()
    return JSONResponse({"message": "บันทึกข้อมูลแล้ว"})


@router.post("/job/{report_id}/submit-immigration")
async def submit_to_immigration(
    report_id: int,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    """สตาฟกด 'ยื่น ตม. เรียบร้อยแล้ว' → status = submitted_to_immigration"""
    report = db.query(ReportRequest).filter(ReportRequest.id == report_id).first()
    if not report:
        raise HTTPException(404, "ไม่พบรายการ")
    if report.submission_mode != "online":
        raise HTTPException(400, "ใช้เฉพาะ online mode")
    if report.status not in ("pending_review", "pending_bot"):
        raise HTTPException(400, f"สถานะไม่ถูกต้อง ({report.status})")

    report.status = "submitted_to_immigration"
    report.tm47_submitted_at = datetime.utcnow()
    if not report.data_confirmed_at:
        report.data_confirmed_at = datetime.utcnow()
    db.commit()
    return JSONResponse({"message": "สถานะอัพเดตเป็น 'ยื่น ตม. แล้ว'"})


@router.post("/job/{report_id}/send-line")
async def send_document_via_line(
    report_id: int,
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    """ส่งเอกสารให้คนงานทาง LINE → status = document_sent"""
    report = db.query(ReportRequest).filter(ReportRequest.id == report_id).first()
    if not report:
        raise HTTPException(404, "ไม่พบรายการ")
    if report.status != "submitted_to_immigration":
        raise HTTPException(400, "ต้องส่ง ตม. ก่อน")

    worker_user = db.query(User).filter(User.id == report.worker_id).first()
    try:
        if worker_user and worker_user.line_user_id:
            from ..services.line_service import _push
            _push(worker_user.line_user_id, f"✅ ยื่น ตม.47 เรียบร้อยแล้ว!\n\nรอรับใบรายงานตัวจาก ตม. ประมาณ 5-7 วันทำการครับ/ค่ะ")
    except Exception as e:
        print(f"[WARN] LINE send failed: {e}")

    report.status = "document_sent"
    db.commit()

    return JSONResponse({"message": "ส่ง LINE แล้ว สถานะ → document_sent"})


@router.post("/job/{report_id}/upload-receipt")
async def upload_receipt(
    report_id: int,
    receipt: UploadFile = File(...),
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    """
    Staff อัพโหลดรูปใบ ตม.47 ที่ ตม. ประทับตราคืนมา
    → Claude extract วันครบกำหนดถัดไป
    → อัพเดท User.next_report_date
    → ตั้ง Line reminder 15 วันก่อนครบ
    → สถานะ completed อัตโนมัติ
    """
    report = db.query(ReportRequest).filter(ReportRequest.id == report_id).first()
    if not report:
        raise HTTPException(404, "ไม่พบรายการ")

    # บันทึกไฟล์ (Cloudinary หรือ local dev)
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    ext = os.path.splitext(receipt.filename or "")[1] or ".jpg"
    receipt_bytes = await receipt.read()
    receipt_url = save_upload(
        receipt_bytes,
        f"{report.worker_id}/receipts",
        f"receipt_{report_id}_{timestamp}{ext}",
    )
    report.receipt_file = receipt_url
    report.status = "completed"
    db.commit()

    # Claude อ่านวันครบกำหนดถัดไปจากใบ (non-blocking — ถ้า Claude ล้มก็ยังสำเร็จ)
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

    # แจ้ง Line ว่าเสร็จแล้ว
    try:
        if worker_user and worker_user.line_user_id:
            send_completion_notification(worker_user, next_date)
    except Exception as e:
        print(f"[WARN] LINE notify failed: {e}")

    return JSONResponse({
        "message": "อัพโหลดใบรายงานตัวสำเร็จ",
        "next_report_date": next_date.strftime("%d/%m/%Y") if next_date else None,
    })
