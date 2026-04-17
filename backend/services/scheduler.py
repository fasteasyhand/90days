import os
import logging
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

logger = logging.getLogger(__name__)
_scheduler: BackgroundScheduler = None


def start_scheduler():
    global _scheduler
    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./90days.db")
    jobstores = {"default": SQLAlchemyJobStore(url=DATABASE_URL)}
    _scheduler = BackgroundScheduler(jobstores=jobstores)
    _scheduler.start()
    logger.info("Scheduler started")


def schedule_line_reminder(worker, next_report_date: datetime):
    """ตั้ง job ส่ง Line reminder 15 วันก่อน next_report_date"""
    if not _scheduler:
        return
    if not worker.line_user_id:
        return

    remind_at = next_report_date - timedelta(days=15)
    if remind_at <= datetime.utcnow():
        # เลยกำหนดแล้ว ส่งทันที
        from .line_service import send_reminder_notification
        send_reminder_notification(worker.line_user_id, next_report_date)
        return

    job_id = f"reminder_user_{worker.id}"

    # ยกเลิก job เดิมถ้ามี
    if _scheduler.get_job(job_id):
        _scheduler.remove_job(job_id)

    from .line_service import send_reminder_notification
    _scheduler.add_job(
        send_reminder_notification,
        trigger="date",
        run_date=remind_at,
        args=[worker.line_user_id, next_report_date],
        id=job_id,
        replace_existing=True,
    )
    logger.info(f"Scheduled reminder for user {worker.id} at {remind_at}")
