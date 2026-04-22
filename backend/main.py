import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse
from fastapi.exceptions import HTTPException
from dotenv import load_dotenv

load_dotenv()

from .database import init_db
from .routers import auth, worker, staff, admin, payment, line_webhook, cron

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPLOAD_DIR = os.path.join(BASE_DIR, os.getenv("UPLOAD_DIR", "uploads"))

_on_vercel = os.getenv("VERCEL") == "1"

if not _on_vercel:
    os.makedirs(UPLOAD_DIR, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    if not _on_vercel:
        from .services.scheduler import start_scheduler
        start_scheduler()
    yield


app = FastAPI(title="90-Day Report Service", lifespan=lifespan)

app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
if not _on_vercel:
    app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

app.include_router(auth.router)
app.include_router(worker.router)
app.include_router(staff.router)
app.include_router(admin.router)
app.include_router(payment.router)
app.include_router(line_webhook.router)
app.include_router(cron.router)


def _fmt_phone(p):
    """0888888888 -> 088-888-8888"""
    if not p:
        return p or ""
    s = str(p).strip().replace("-", "").replace(" ", "")
    if len(s) == 10 and s.isdigit():
        return f"{s[:3]}-{s[3:6]}-{s[6:]}"
    return p


for _mod in (auth, worker, staff, admin):
    _mod.templates.env.filters["phone"] = _fmt_phone


@app.exception_handler(HTTPException)
async def auth_redirect_handler(request: Request, exc: HTTPException):
    """
    401/403 บน HTML routes → redirect ไป /login แทนแสดง JSON
    API routes (Accept: application/json หรือ path เริ่มต้น /api/) → คืน JSON ปกติ
    """
    is_api = (
        request.url.path.startswith("/api/")
        or "application/json" in request.headers.get("accept", "")
    )
    if exc.status_code in (401, 403) and not is_api:
        return RedirectResponse(url="/login", status_code=302)
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.get("/")
def root():
    return RedirectResponse(url="/login")
