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
from ..services.claude_service import extract_next_report_date
from ..services.storage_service import save_upload, read_file_bytes, file_exists, get_ext
from ..services.line_service import send_completion_notification

router = APIRouter(prefix="/staff", tags=["staff"])
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))


@router.get("/dashboard", response_class=HTMLResponse)
def staff_dashboard(request: Request, user: User = Depends(require_staff), db: Session = Depends(get_db)):
    queue = (
        db.query(ReportRequest)
        .filter(ReportRequest.status.in_(["processing", "pending_payment", "mailing"]))
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

    # Claude อ่านวันครบกำหนดถัดไปจากใบ
    next_date = await extract_next_report_date(receipt_url)

    worker_user = db.query(User).filter(User.id == report.worker_id).first()

    if next_date:
        report.next_report_date_extracted = next_date
        worker_user.next_report_date = next_date
        # Reminder ถูกส่งโดย Vercel Cron (/api/cron/reminders) ทุกวัน

    # Auto-status: completed
    report.status = "completed"
    db.commit()

    # แจ้ง Line ว่าเสร็จแล้ว
    if worker_user.line_user_id:
        send_completion_notification(worker_user, next_date)

    return JSONResponse({
        "message": "อัพโหลดใบรายงานตัวสำเร็จ",
        "next_report_date": next_date.strftime("%d/%m/%Y") if next_date else None,
    })
