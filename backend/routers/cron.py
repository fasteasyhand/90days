"""
Vercel Cron endpoint — แทน APScheduler
ส่ง LINE reminder 15 วันก่อนครบกำหนด (ทุกวันเวลา 02:00 UTC)
"""
import os
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from ..database import get_db, User
from ..services.line_service import send_reminder_notification

router = APIRouter(prefix="/api/cron", tags=["cron"])


@router.get("/reminders")
def send_reminders(request: Request, db: Session = Depends(get_db)):
    # Vercel ส่ง Authorization: Bearer <CRON_SECRET>
    cron_secret = os.getenv("CRON_SECRET", "")
    if cron_secret:
        auth = request.headers.get("Authorization", "")
        if auth != f"Bearer {cron_secret}":
            raise HTTPException(401, "Unauthorized")

    today = datetime.utcnow().date()
    target_date = today + timedelta(days=15)
    target_start = datetime(target_date.year, target_date.month, target_date.day)
    target_end = target_start + timedelta(days=1)

    users = db.query(User).filter(
        User.next_report_date >= target_start,
        User.next_report_date < target_end,
        User.line_user_id.isnot(None),
    ).all()

    sent = 0
    for user in users:
        send_reminder_notification(user.line_user_id, user.next_report_date)
        sent += 1

    return JSONResponse({"sent": sent, "target_date": str(target_date)})
