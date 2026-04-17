import sys
import os

# Add project root to path so `backend` package is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from backend.main import app  # noqa: F401  — Vercel looks for `app`
