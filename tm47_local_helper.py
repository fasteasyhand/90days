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

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
import uvicorn

HERE = Path(__file__).resolve().parent
BOT_SCRIPT = HERE / "tm47_bot.py"

app = FastAPI(title="TM47 Local Helper")

# อนุญาตให้หน้าเว็บ production (HTTPS) เรียก localhost (HTTP) ได้
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Chrome 94+ Private Network Access: ต้องเติม header นี้
# เพื่อให้ HTTPS page เรียก http://localhost ได้
@app.middleware("http")
async def add_pna_header(request: Request, call_next):
    if request.method == "OPTIONS":
        resp = Response(status_code=200)
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "*"
        resp.headers["Access-Control-Allow-Headers"] = "*"
        resp.headers["Access-Control-Allow-Private-Network"] = "true"
        resp.headers["Access-Control-Max-Age"] = "86400"
        return resp
    resp = await call_next(request)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Private-Network"] = "true"
    return resp

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

    # รัน tm47_bot.py --id N — เก็บ log ลงไฟล์เพื่อ debug
    log_path = HERE / f"tm47_bot_{id}.log"
    log_f = open(log_path, "w", encoding="utf-8", buffering=1)

    python_exe = sys.executable
    # บังคับ UTF-8 ให้ emoji ใน print ไม่ทำให้ bot crash บน Windows cp874
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    # ใช้ -u เพื่อ unbuffered output + shell=False + ส่ง stdout/stderr ไปไฟล์
    # แยก console ใหม่เผื่ออยากดู realtime ก็ยังได้
    proc = subprocess.Popen(
        [python_exe, "-u", str(BOT_SCRIPT), "--id", str(id), "--auto-submit"],
        cwd=str(HERE),
        stdout=log_f,
        stderr=subprocess.STDOUT,
        env=env,
        creationflags=subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0,
    )
    _running[id] = proc
    print(f"▶️  spawned tm47_bot --id {id}  pid={proc.pid}  log={log_path}")
    return {"ok": True, "status": "started", "pid": proc.pid, "log": str(log_path)}


@app.get("/log")
def get_log(id: int):
    log_path = HERE / f"tm47_bot_{id}.log"
    if not log_path.exists():
        return {"log": "(ยังไม่มี log)"}
    return {"log": log_path.read_text(encoding="utf-8", errors="replace")}


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
