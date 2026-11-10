import base64
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import tempfile
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
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
SESSION_COOKIE = "finmee_session"
SESSION_DAYS = 30
PASSWORD_HASH_ROUNDS = 600_000
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


class RegisterInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=10, max_length=128)


class LoginInput(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)


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
        "script-src 'self'; connect-src 'self'; manifest-src 'self'; "
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


def _normalize_email(email):
    normalized = email.strip().casefold()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", normalized):
        raise HTTPException(status_code=422, detail="Saisissez une adresse e-mail valide.")
    return normalized


def _hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PASSWORD_HASH_ROUNDS
    )
    return "pbkdf2_sha256${}${}${}".format(
        PASSWORD_HASH_ROUNDS,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    )


def _verify_password(password, encoded_hash):
    try:
        algorithm, rounds, salt, expected = encoded_hash.split("$")
        if algorithm != "pbkdf2_sha256" or int(rounds) != PASSWORD_HASH_ROUNDS:
            raise ValueError("Unsupported password hash.")
        salt_bytes = base64.b64decode(salt, validate=True)
        expected_bytes = base64.b64decode(expected, validate=True)
        if len(salt_bytes) != 16 or len(expected_bytes) != 32:
            raise ValueError("Invalid password hash.")
    except (AttributeError, ValueError):
        hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), b"\0" * 16, PASSWORD_HASH_ROUNDS
        )
        return False
    actual = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt_bytes, PASSWORD_HASH_ROUNDS
    )
    return hmac.compare_digest(actual, expected_bytes)


def _new_session(response, user_id):
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)
    web_database.create_session(
        DATABASE_PATH,
        hashlib.sha256(token.encode("ascii")).hexdigest(),
        user_id,
        expires_at.isoformat(),
    )
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_DAYS * 24 * 60 * 60,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
        path="/",
    )


def _current_user(request):
    token = request.cookies.get(SESSION_COOKIE, "")
    if token:
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        user = web_database.get_session_user(
            DATABASE_PATH, token_hash, datetime.now(timezone.utc).isoformat()
        )
        if user:
            return user
    raise HTTPException(status_code=401, detail="Connectez-vous pour accéder à votre espace.")


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


@app.post("/api/auth/register", status_code=201)
def register(payload: RegisterInput, request: Request, response: Response):
    _check_csrf(request)
    email = _normalize_email(payload.email)
    name = payload.name.strip()
    if not name or any(ord(character) < 32 for character in name):
        raise HTTPException(status_code=422, detail="Saisissez un nom valide.")
    user = web_database.create_user(
        DATABASE_PATH, email, name, _hash_password(payload.password)
    )
    if user is None:
        raise HTTPException(
            status_code=409, detail="Un compte existe déjà avec cette adresse e-mail."
        )
    _new_session(response, user["id"])
    return {"id": user["id"], "name": user["name"], "email": user["email"]}


@app.post("/api/auth/login")
def login(payload: LoginInput, request: Request, response: Response):
    _check_csrf(request)
    email = _normalize_email(payload.email)
    user = web_database.get_user_by_email(DATABASE_PATH, email)
    password_hash = user["password_hash"] if user else None
    if not _verify_password(payload.password, password_hash):
        raise HTTPException(status_code=401, detail="Adresse e-mail ou mot de passe incorrect.")
    _new_session(response, user["id"])
    return {"id": user["id"], "name": user["name"], "email": user["email"]}


@app.get("/api/auth/me")
def current_account(request: Request):
    user = _current_user(request)
    return {"id": user["id"], "name": user["name"], "email": user["email"]}


@app.post("/api/auth/logout")
def logout(request: Request, response: Response):
    _check_csrf(request)
    token = request.cookies.get(SESSION_COOKIE, "")
    if token:
        web_database.delete_session(
            DATABASE_PATH, hashlib.sha256(token.encode("utf-8")).hexdigest()
        )
    response.delete_cookie(
        SESSION_COOKIE,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
        path="/",
    )
    return {"ok": True}


@app.get("/api/profile")
def get_profile(request: Request):
    user = _current_user(request)
    return {"name": user["name"]}

@app.put("/api/profile")
def save_profile(payload: ProfileInput, request: Request):
    _check_csrf(request)
    user = _current_user(request)
    name = payload.name.strip()
    if not name or len(name) > 80 or any(ord(character) < 32 for character in name):
        raise HTTPException(
            status_code=422,
            detail="Saisissez un nom valide de 1 à 80 caractères.",
        )
    web_database.save_user_profile(DATABASE_PATH, user["id"], name)
    return {"name": name}


@app.get("/api/dashboard")
def dashboard(request: Request):
    user = _current_user(request)
    return web_database.get_dashboard(DATABASE_PATH, user["id"])


@app.get("/api/transactions")
def transactions(request: Request):
    user = _current_user(request)
    return web_database.get_history(DATABASE_PATH, user["id"])


@app.post("/api/transactions", status_code=201)
def create_transaction(
    payload: TransactionInput,
    request: Request,
):
    _check_csrf(request)
    user = _current_user(request)
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
            user["id"],
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
def statistics(request: Request, period: str = "month"):
    user = _current_user(request)
    try:
        return web_database.get_statistics(DATABASE_PATH, user["id"], period)
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
        source_user_id = None
        if "users" in tables:
            user_columns = {
                row["name"] for row in source.execute("PRAGMA table_info(users)")
            }
            if "password_hash" in user_columns and "password_salt" not in user_columns:
                legacy_user = source.execute(
                    """
                    SELECT id FROM users
                    WHERE email = 'local@finmee.invalid' AND password_hash IS NULL
                    """
                ).fetchone()
                if not legacy_user:
                    raise HTTPException(
                        status_code=400,
                        detail="Cette base contient des comptes privés et ne peut pas être importée en bloc.",
                    )
                source_user_id = legacy_user["id"]
            elif source.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 1:
                raise HTTPException(
                    status_code=400,
                    detail="Une base avec plusieurs comptes ne peut pas être importée en bloc.",
                )
        amount_column = "amount_xof" if "amount_xof" in columns else "amount_cents"
        required = {"operation_date", amount_column, "type", "description"}
        if not required.issubset(columns):
            raise HTTPException(status_code=400, detail="Format de base SQLite non reconnu.")

        rows = []
        is_legacy_web_database = "user_id" in columns
        if source_user_id is not None and not is_legacy_web_database:
            raise HTTPException(
                status_code=400, detail="Format de base SQLite non reconnu."
            )
        query_columns = f"operation_date, {amount_column}, type, description"
        if "category" in columns:
            query_columns += ", category"
        where_clause = " WHERE user_id = ?" if source_user_id is not None else ""
        parameters = (source_user_id,) if source_user_id is not None else ()
        count = source.execute(
            f"SELECT COUNT(*) FROM transactions{where_clause}", parameters
        ).fetchone()[0]
        if count > 100_000:
            raise HTTPException(status_code=413, detail="La base contient trop de transactions.")
        for row in source.execute(
            f"SELECT {query_columns} FROM transactions{where_clause}", parameters
        ):
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
    user = _current_user(request)
    content = await file.read(MAX_IMPORT_BYTES + 1)
    if len(content) > MAX_IMPORT_BYTES:
        raise HTTPException(status_code=413, detail="Le fichier dépasse la limite de 12 Mo.")
    rows = _read_legacy_database(content)
    imported = web_database.import_transactions(
        DATABASE_PATH,
        user["id"],
        rows,
        hashlib.sha256(content).hexdigest(),
    )
    return {"imported": imported, "already_imported": imported == 0}
