import os
from datetime import datetime
from sqlalchemy import (
    create_engine, Column, Integer, String, DateTime,
    Boolean, ForeignKey, Float, JSON, Text, inspect as sa_inspect
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./90days.db")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    phone = Column(String(20), unique=True, index=True, nullable=False)
    role = Column(String(10), default="worker")  # worker / staff / admin
    line_user_id = Column(String(50), unique=True, nullable=True)
    password_hash = Column(String(255), nullable=True)   # staff/admin ใช้ password
    is_verified = Column(Boolean, default=False)
    next_report_date = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    report_requests = relationship("ReportRequest", back_populates="worker")
    payment_requests = relationship("PaymentRequest", back_populates="worker")


class ReportRequest(Base):
    __tablename__ = "report_requests"

    id = Column(Integer, primary_key=True, index=True)
    worker_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    # normal = เอกสารครบ ไม่เกินกำหนด (300฿)
    # urgent = ใบหาย / เกินกำหนด (800฿)
    case_type = Column(String(10), default="normal")
    status = Column(String(20), default="pending_payment")
    # pending_payment → processing → mailing → completed

    passport_file = Column(Text, nullable=True)
    visa_file = Column(Text, nullable=True)
    old_report_file = Column(Text, nullable=True)  # ใบรายงานตัวเดิม

    mailing_address = Column(JSON, nullable=True)  # {street, tambol, amphur, province, phone}
    extracted_data = Column(JSON, nullable=True)   # ข้อมูลที่ Claude extract ได้ (เดิม)
    form_filled_file = Column(Text, nullable=True)  # PDF ตม.47 ที่กรอกแล้ว

    receipt_file = Column(Text, nullable=True)  # รูปใบที่ ตม. ประทับตราคืนมา
    next_report_date_extracted = Column(DateTime, nullable=True)  # extract จาก receipt

    amount_charged = Column(Float, nullable=True)

    # Online / Offline mode
    # pending_payment → (online) pending_review → pending_bot → submitted_to_immigration → document_sent → completed
    # pending_payment → (offline) processing → mailing → completed
    submission_mode = Column(String(10), default="offline")  # online / offline

    # TM47 website login credentials (ใส่โดยสตาฟตอน confirm)
    tm47_email    = Column(String(200), nullable=True)
    tm47_password = Column(String(200), nullable=True)

    # TM47 fields — extract จากเอกสาร (Claude รอบ 2 หลังจ่ายเงิน)
    passport_no    = Column(String(50),  nullable=True)
    nationality    = Column(String(10),  nullable=True)
    surname        = Column(String(100), nullable=True)
    given_name     = Column(String(100), nullable=True)
    middle_name    = Column(String(100), nullable=True)
    gender         = Column(String(5),   nullable=True)
    dob_day        = Column(Integer,     nullable=True)
    dob_month      = Column(Integer,     nullable=True)
    dob_year       = Column(Integer,     nullable=True)
    arrival_date   = Column(String(10),  nullable=True)   # DD/MM/YYYY
    visa_expire    = Column(String(10),  nullable=True)   # DD/MM/YYYY

    # TM47 fields — คนงานกรอกบนเว็บ
    building_name  = Column(String(200), nullable=True)
    address_no     = Column(String(50),  nullable=True)
    road           = Column(String(100), nullable=True)
    province       = Column(String(100), nullable=True)
    city           = Column(String(100), nullable=True)
    district       = Column(String(100), nullable=True)

    # Online flow timestamps
    data_confirmed_at  = Column(DateTime, nullable=True)  # สตาฟกดยืนยันข้อมูล
    tm47_submitted_at  = Column(DateTime, nullable=True)  # บอทกรอก TM47 เสร็จ

    # Auto-status triggers (offline)
    doc_downloaded_at = Column(DateTime, nullable=True)
    address_downloaded_at = Column(DateTime, nullable=True)

    # Shared flow timestamps
    receipt_uploaded_at = Column(DateTime, nullable=True)  # สตาฟอัพโหลดใบใหม่
    completed_at        = Column(DateTime, nullable=True)  # ปิดงาน (ส่ง LINE หรือส่งคืน)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    worker = relationship("User", back_populates="report_requests")
    payment_requests = relationship("PaymentRequest", back_populates="report_request")


class PaymentRequest(Base):
    __tablename__ = "payment_requests"

    id = Column(Integer, primary_key=True, index=True)
    worker_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    report_request_id = Column(Integer, ForeignKey("report_requests.id"), nullable=False)
    amount = Column(Float, nullable=False)
    status = Column(String(20), default="pending")  # pending / paid / failed
    chillpay_order_id = Column(String(100), nullable=True)
    qr_data = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    paid_at = Column(DateTime, nullable=True)

    worker = relationship("User", back_populates="payment_requests")
    report_request = relationship("ReportRequest", back_populates="payment_requests")


class LineLinkCode(Base):
    __tablename__ = "line_link_codes"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    code = Column(String(8), unique=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)
    is_used = Column(Boolean, default=False)


class OTPLog(Base):
    __tablename__ = "otp_logs"

    id = Column(Integer, primary_key=True, index=True)
    phone = Column(String(20), nullable=False)
    otp_code = Column(String(6), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)
    is_used = Column(Boolean, default=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    Base.metadata.create_all(bind=engine)
    # Safe migration: เพิ่ม column ใหม่ถ้ายังไม่มี (SQLite)
    with engine.connect() as conn:
        from sqlalchemy import text, inspect
        inspector = inspect(engine)
        cols = [c["name"] for c in inspector.get_columns("users")]
        if "password_hash" not in cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN password_hash TEXT"))
            conn.commit()
