import os
import hmac
import hashlib
from datetime import datetime
from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from ..database import get_db, User, ReportRequest, PaymentRequest
from ..dependencies import require_worker
from ..services.chillpay_service import create_qr_payment, verify_payment_signature

router = APIRouter(prefix="/payment", tags=["payment"])


@router.post("/api/create-qr/{report_id}")
def create_payment_qr(
    report_id: int,
    user: User = Depends(require_worker),
    db: Session = Depends(get_db),
):
    report = db.query(ReportRequest).filter(
        ReportRequest.id == report_id,
        ReportRequest.worker_id == user.id,
    ).first()
    if not report:
        raise HTTPException(404, "ไม่พบรายการ")
    if report.status != "pending_payment":
        return JSONResponse({"message": "ชำระเงินแล้ว"})

    result = create_qr_payment(
        order_id=f"90D-{report.id}-{int(datetime.utcnow().timestamp())}",
        amount=report.amount_charged,
        description=f"รายงานตัว 90 วัน #{report.id}",
    )

    pay_url = result.get("pay_url") or result.get("qr_data") or ""

    payment = PaymentRequest(
        worker_id=user.id,
        report_request_id=report.id,
        amount=report.amount_charged,
        status="pending",
        chillpay_order_id=result.get("order_id"),
        qr_data=pay_url,
    )
    db.add(payment)
    db.commit()
    db.refresh(payment)

    return JSONResponse({
        "payment_id": payment.id,
        "qr_data":    pay_url,
        "pay_url":    pay_url,
        "amount":     payment.amount,
        "order_id":   payment.chillpay_order_id,
    })


@router.post("/webhook/chillpay")
async def chillpay_webhook(request: Request, db: Session = Depends(get_db)):
    """ChillPay callback — รับได้ทั้ง JSON (จาก Worker) และ form data (จาก ChillPay โดยตรง)"""
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        body = await request.json()
    else:
        # form data จาก ChillPay โดยตรง
        form = await request.form()
        order_no = form.get("OrderNo") or form.get("order_id") or ""
        payment_status = form.get("PaymentStatus") or ""
        transaction_id = form.get("TransactionId") or ""
        body = {
            "order_id":       order_no,
            "status":         "success" if payment_status == "0" else "failed",
            "transaction_id": transaction_id,
        }

    if not verify_payment_signature(body):
        raise HTTPException(400, "Invalid signature")

    order_id = body.get("order_id")
    status = body.get("status")  # "success" / "failed"

    payment = db.query(PaymentRequest).filter(
        PaymentRequest.chillpay_order_id == order_id
    ).first()
    if not payment:
        return JSONResponse({"message": "not found"}, status_code=200)

    if status == "success" and payment.status != "paid":
        payment.status = "paid"
        payment.paid_at = datetime.utcnow()

        report = db.query(ReportRequest).filter(
            ReportRequest.id == payment.report_request_id
        ).first()
        if report and report.status == "pending_payment":
            if report.submission_mode == "online":
                report.status = "reviewing"
            else:
                report.status = "processing"

        db.commit()

    return JSONResponse({"message": "ok"})


@router.get("/api/status/{payment_id}")
def check_payment_status(payment_id: int, user: User = Depends(require_worker), db: Session = Depends(get_db)):
    payment = db.query(PaymentRequest).filter(
        PaymentRequest.id == payment_id,
        PaymentRequest.worker_id == user.id,
    ).first()
    if not payment:
        raise HTTPException(404, "ไม่พบรายการชำระเงิน")
    return {"status": payment.status, "paid_at": payment.paid_at}
