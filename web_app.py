import hashlib
import hmac
import os
import secrets
import sqlite3
import tempfile
from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import web_database


ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
DATABASE_URL = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL")
DATABASE_PATH = DATABASE_URL or Path(
    os.environ.get("EXPENSES_DB", str(ROOT / "data" / "accounts.db"))
)
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "1") != "0"
CSRF_COOKIE = "depenses_csrf"
LOCAL_USER_ID = 1
MAX_IMPORT_BYTES = 12 * 1024 * 1024
MAX_AMOUNT_XOF = 1_000_000_000_000_000
EUR_TO_XOF = Decimal("655.957")
ALLOWED_CATEGORIES = {
    "Alimentation",
    "Transport",
    "Logement",
    "Santé",
    "Loisirs",
    "Autre",
}


@asynccontextmanager
async def lifespan(_app):
    if os.environ.get("VERCEL") and not DATABASE_URL:
        raise RuntimeError(
            "Set DATABASE_URL or POSTGRES_URL to a persistent PostgreSQL database on Vercel."
        )
    web_database.initialize(DATABASE_PATH)
    yield


app = FastAPI(title="Finmee", docs_url=None, redoc_url=None, lifespan=lifespan)
app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")


class TransactionInput(BaseModel):
    amount: str = Field(max_length=40)
    type: str
    description: str = Field(min_length=1, max_length=200)
    category: str | None = Field(default=None, max_length=60)
    operation_date: str | None = None


class ProfileInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline' https://va.vercel-scripts.com; "
        "connect-src 'self' https://vitals.vercel-insights.com; manifest-src 'self'; "
        "worker-src 'self'; object-src 'none'; base-uri 'self'; "
        "frame-ancestors 'none'; form-action 'self'"
    )
    if COOKIE_SECURE:
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )
    return response


def _set_csrf_cookie(response):
    response.set_cookie(
        CSRF_COOKIE,
        secrets.token_urlsafe(32),
        max_age=365 * 24 * 60 * 60,
        httponly=False,
        secure=COOKIE_SECURE,
        samesite="strict",
        path="/",
    )


def _check_csrf(request):
    cookie_token = request.cookies.get(CSRF_COOKIE, "")
    header_token = request.headers.get("X-CSRF-Token", "")
    if not cookie_token or not header_token or not hmac.compare_digest(
        cookie_token, header_token
    ):
        raise HTTPException(status_code=403, detail="Vérification de sécurité expirée.")


@app.get("/")
def home():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(
        STATIC_DIR / "manifest.webmanifest",
        media_type="application/manifest+json",
    )


@app.get("/sw.js")
def service_worker():
    return FileResponse(
        STATIC_DIR / "sw.js",
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
    )


@app.get("/api/csrf")
def csrf(response: Response, request: Request):
    if not request.cookies.get(CSRF_COOKIE):
        _set_csrf_cookie(response)
    response.headers["Cache-Control"] = "no-store"
    return {"ok": True}


@app.get("/api/profile")
def get_profile():
    return web_database.get_local_profile(DATABASE_PATH)


@app.put("/api/profile")
def save_profile(payload: ProfileInput, request: Request):
    _check_csrf(request)
    name = payload.name.strip()
    if not name or len(name) > 80 or any(ord(character) < 32 for character in name):
        raise HTTPException(
            status_code=422,
            detail="Saisissez un nom valide de 1 à 80 caractères.",
        )
    web_database.save_local_profile(DATABASE_PATH, name)
    return {"name": name}


@app.get("/api/dashboard")
def dashboard():
    return web_database.get_dashboard(DATABASE_PATH, LOCAL_USER_ID)


@app.get("/api/transactions")
def transactions():
    return web_database.get_history(DATABASE_PATH, LOCAL_USER_ID)


@app.post("/api/transactions", status_code=201)
def create_transaction(
    payload: TransactionInput,
    request: Request,
):
    _check_csrf(request)
    if payload.type not in {"income", "expense"}:
        raise HTTPException(status_code=422, detail="Type d’opération invalide.")
    try:
        normalized_amount = payload.amount.strip().replace(" ", "").replace("\u202f", "")
        amount = Decimal(normalized_amount)
        if not amount.is_finite() or amount <= 0:
            raise ValueError
        if amount != amount.to_integral_value() or amount > MAX_AMOUNT_XOF:
            raise ValueError
        operation_date = payload.operation_date or date.today().isoformat()
        date.fromisoformat(operation_date)
    except (InvalidOperation, ValueError):
        raise HTTPException(
            status_code=422,
            detail="Montant ou date invalide. Saisissez un montant positif en FCFA, sans décimales.",
        ) from None
    if payload.type == "expense" and payload.category not in ALLOWED_CATEGORIES:
        raise HTTPException(status_code=422, detail="Catégorie de dépense invalide.")
    try:
        web_database.add_transaction(
            DATABASE_PATH,
            LOCAL_USER_ID,
            int(amount),
            payload.type,
            payload.description,
            payload.category,
            operation_date,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    return {"ok": True}


@app.get("/api/statistics")
def statistics(period: str = "month"):
    try:
        return web_database.get_statistics(DATABASE_PATH, LOCAL_USER_ID, period)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None


def _read_legacy_database(content):
    if not content.startswith(b"SQLite format 3\x00"):
        raise HTTPException(status_code=400, detail="Le fichier choisi n’est pas une base SQLite.")

    temporary_path = None
    source = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as temporary:
            temporary.write(content)
            temporary_path = temporary.name
        source = sqlite3.connect(temporary_path)
        source.row_factory = sqlite3.Row
        source.execute("PRAGMA query_only = ON")
        integrity = source.execute("PRAGMA quick_check").fetchone()[0]
        tables = {
            row["name"]
            for row in source.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        if integrity != "ok" or "transactions" not in tables:
            raise HTTPException(status_code=400, detail="La base ne contient pas de transactions valides.")
        columns = {
            row["name"] for row in source.execute("PRAGMA table_info(transactions)")
        }
        amount_column = "amount_xof" if "amount_xof" in columns else "amount_cents"
        required = {"operation_date", amount_column, "type", "description"}
        if not required.issubset(columns):
            raise HTTPException(status_code=400, detail="Format de base SQLite non reconnu.")
        if source.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] > 100_000:
            raise HTTPException(status_code=413, detail="La base contient trop de transactions.")

        rows = []
        is_legacy_web_database = "user_id" in columns
        query_columns = f"operation_date, {amount_column}, type, description"
        if "category" in columns:
            query_columns += ", category"
        for row in source.execute(f"SELECT {query_columns} FROM transactions"):
            transaction_type = row["type"]
            if transaction_type in {"revenu", "income"}:
                transaction_type = "income"
            elif transaction_type in {"dépense", "depense", "expense"}:
                transaction_type = "expense"
            operation_date = str(row["operation_date"])
            date.fromisoformat(operation_date)
            original_amount = row[amount_column]
            description = row["description"]
            category = row["category"] if "category" in columns else None
            if (
                transaction_type not in {"income", "expense"}
                or not isinstance(original_amount, int)
                or original_amount <= 0
                or not isinstance(description, str)
                or not description.strip()
            ):
                raise HTTPException(status_code=400, detail="Une transaction de la base est invalide.")
            if amount_column == "amount_cents" and is_legacy_web_database:
                converted_amount = (
                    Decimal(original_amount) * EUR_TO_XOF / 100
                ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
                if not 0 < converted_amount <= MAX_AMOUNT_XOF:
                    raise HTTPException(
                        status_code=400,
                        detail="Une ancienne opération dépasse le montant maximum en FCFA.",
                    )
                amount_xof = int(converted_amount)
            elif amount_column == "amount_cents":
                converted_amount = (Decimal(original_amount) / 100).quantize(
                    Decimal("1"), rounding=ROUND_HALF_UP
                )
                if not 0 < converted_amount <= MAX_AMOUNT_XOF:
                    raise HTTPException(
                        status_code=400,
                        detail="Une ancienne opération dépasse le montant maximum en FCFA.",
                    )
                amount_xof = int(converted_amount)
            else:
                amount_xof = original_amount
                if amount_xof > MAX_AMOUNT_XOF:
                    raise HTTPException(
                        status_code=400,
                        detail="Une opération dépasse le montant maximum en FCFA.",
                    )
            if transaction_type == "expense" and not category:
                category = "Autre"
            if transaction_type == "income":
                category = None
            rows.append(
                {
                    "operation_date": operation_date,
                    "amount_xof": amount_xof,
                    "type": transaction_type,
                    "description": description[:200],
                    "category": str(category)[:60] if category else None,
                }
            )
        return rows
    except HTTPException:
        raise
    except (sqlite3.Error, ValueError, TypeError) as error:
        raise HTTPException(status_code=400, detail="Impossible de lire cette base SQLite.") from error
    finally:
        if source is not None:
            source.close()
        if temporary_path:
            Path(temporary_path).unlink(missing_ok=True)


@app.post("/api/import")
async def import_database(
    request: Request,
    file: UploadFile = File(...),
):
    _check_csrf(request)
    content = await file.read(MAX_IMPORT_BYTES + 1)
    if len(content) > MAX_IMPORT_BYTES:
        raise HTTPException(status_code=413, detail="Le fichier dépasse la limite de 12 Mo.")
    rows = _read_legacy_database(content)
    imported = web_database.import_transactions(
        DATABASE_PATH,
        LOCAL_USER_ID,
        rows,
        hashlib.sha256(content).hexdigest(),
    )
    return {"imported": imported, "already_imported": imported == 0}
