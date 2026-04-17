import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse
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


@app.get("/")
def root():
    return RedirectResponse(url="/login")
