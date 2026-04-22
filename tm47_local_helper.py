"""
tm47_local_helper.py
────────────────────
Local bridge ที่รันบนเครื่องของสตาฟ
เปิดพอร์ต http://localhost:8765 รอคำสั่งจากหน้าเว็บ staff_job_detail
เมื่อสตาฟกดปุ่ม "📨 ยื่น ตม. ทางออนไลน์" → หน้าเว็บจะ fetch มาที่นี่
→ เครื่องสตาฟจะรัน tm47_bot.py --id <report_id> (flow v65 อัตโนมัติ)

วิธีใช้:
    pip install fastapi uvicorn
    python tm47_local_helper.py

แล้วเปิดเบราว์เซอร์ใช้งานระบบได้ตามปกติ
(ต้องเปิดทิ้งไว้ตลอดช่วงทดสอบ)
"""
import os
import sys
import subprocess
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

HERE = Path(__file__).resolve().parent
BOT_SCRIPT = HERE / "tm47_bot.py"

app = FastAPI(title="TM47 Local Helper")

# อนุญาตให้หน้าเว็บ production เรียก localhost ได้
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["*"],
)

# เก็บ process ที่กำลังรันอยู่ กันยิงซ้อน
_running: dict[int, subprocess.Popen] = {}


@app.get("/ping")
def ping():
    return {"ok": True, "bot_exists": BOT_SCRIPT.exists()}


@app.post("/run")
def run(id: int):
    if not BOT_SCRIPT.exists():
        raise HTTPException(500, f"ไม่พบ {BOT_SCRIPT}")

    # ถ้า process เก่ายังรันอยู่ให้ return บอก
    old = _running.get(id)
    if old and old.poll() is None:
        return {"ok": True, "status": "already_running", "pid": old.pid}

    # รัน tm47_bot.py --id N (headed) แบบไม่บล็อก
    python_exe = sys.executable
    proc = subprocess.Popen(
        [python_exe, str(BOT_SCRIPT), "--id", str(id)],
        cwd=str(HERE),
        creationflags=subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0,
    )
    _running[id] = proc
    return {"ok": True, "status": "started", "pid": proc.pid}


@app.get("/status")
def status(id: int):
    proc = _running.get(id)
    if not proc:
        return {"running": False}
    rc = proc.poll()
    return {"running": rc is None, "exit_code": rc}


if __name__ == "__main__":
    print("🚀 TM47 Local Helper → http://localhost:8765")
    print(f"   bot script: {BOT_SCRIPT}")
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="info")
