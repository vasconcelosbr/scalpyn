"""Private historical dashboard. No source DB, exchange credentials or trading handlers."""
import base64
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import secrets
import sqlite3
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request as HttpRequest, build_opener
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Request, Response, Depends
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from archive_contract import NoRedirect, validate, timestamp, unpack

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
ROOT = Path(__file__).parent
sessions = {}
attempts = {}
lock = threading.Lock()
COOKIE = "__Host-pool_session"


def setting(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise HTTPException(503, "Serviço não configurado")
    return value


def origin():
    value = setting("PUBLIC_ORIGIN")
    parts = urlsplit(value)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.path or parts.query or parts.fragment:
        raise HTTPException(503, "Serviço não configurado")
    return value


@contextmanager
def db(write=False):
    root = Path(setting("SNAPSHOT_DIR"))
    if os.environ.get("RAILWAY_SERVICE_ID") and (os.environ.get("RAILWAY_VOLUME_MOUNT_PATH") != str(root) or not os.path.ismount(root)):
        raise HTTPException(503, "Arquivo indisponível")
    path = root / "dashboard.sqlite3"
    if write:
        root.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=10)
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("CREATE TABLE IF NOT EXISTS snapshots (day TEXT PRIMARY KEY,captured_at TEXT,sha256 TEXT,payload_gzip BLOB,asset_count INTEGER,queried_at TEXT,last_scanned_at TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY,started_at TEXT,finished_at TEXT,status TEXT)")
        conn.commit()
    else:
        if not path.exists():
            raise HTTPException(503, "Aguardando sincronização inicial")
        conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        if write:
            conn.commit()
    finally:
        conn.close()


@app.middleware("http")
async def security(request, call_next):
    # Bound bodies before parsing, including chunked requests.
    limit = 4 * 1024 * 1024 if request.url.path == "/internal/snapshot" else 16384
    if request.method == "POST":
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > limit:
                return JSONResponse({"detail": "Requisição excede limite"}, 413, headers={"Cache-Control": "private, no-store"})
            chunks.append(chunk)
        request._body = b"".join(chunks)
    try:
        response = await call_next(request)
    except Exception:
        response = JSONResponse({"detail": "Serviço temporariamente indisponível"}, 503)
    response.headers.update({"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY",
        "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        "Strict-Transport-Security": "max-age=31536000"})
    return response


def authenticated(request: Request):
    key = request.cookies.get(COOKIE, "")
    with lock:
        record = sessions.get(key)
        if not record or record["expires"] <= time.time():
            sessions.pop(key, None)
            raise HTTPException(401, "Entre para consultar o arquivo")
    return record


def same_origin(request: Request):
    if request.headers.get("origin") != origin():
        raise HTTPException(403, "Origem não permitida")


def ingest_auth(request: Request):
    expected = setting("INGEST_KEY_SHA256")
    keys = request.headers.getlist("x-radar-ingest-key")
    if len(keys) != 1 or not keys[0] or not hmac.compare_digest(hashlib.sha256(keys[0].encode()).hexdigest(), expected):
        raise HTTPException(401, "Credencial inválida")


def auth_request(path, body=None, token=None):
    base = setting("SCALPYN_AUTH_ORIGIN")
    if base != "https://scalpyn.vercel.app":
        raise HTTPException(503, "Autenticação não configurada")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = HttpRequest(base + path, data=None if body is None else json.dumps(body).encode(), headers=headers,
                      method="GET" if body is None else "POST")
    try:
        with build_opener(NoRedirect()).open(req, timeout=20) as result:
            return json.loads(result.read(65536))
    except HTTPError as exc:
        status = exc.code
        exc.close()
        raise HTTPException(401 if status in (400, 401, 403, 404) else 503, "Não foi possível autenticar") from None
    except Exception:
        raise HTTPException(503, "Autenticação indisponível") from None


class Login(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=512)


@app.post("/api/login", dependencies=[Depends(same_origin)])
def login(body: Login, request: Request, response: Response):
    # Global bound avoids relying on user-controlled forwarded-IP headers.
    now = time.time()
    with lock:
        for k in list(attempts):
            if attempts[k][0] < now - 60:
                del attempts[k]
        email_key = hashlib.sha256(body.email.lower().encode()).hexdigest()
        for k, maximum in (("global", 30), (email_key, 5)):
            start, count = attempts.get(k, (now, 0))
            if count >= maximum:
                raise HTTPException(429, "Aguarde um minuto para tentar novamente")
            attempts[k] = (start, count + 1)
    result = auth_request("/api/auth/login", body.model_dump())
    user = auth_request("/api/auth/me", token=result["access_token"])
    if user.get("id") != setting("DASHBOARD_OWNER_ID"):
        raise HTTPException(403, "Conta sem acesso a este arquivo")
    session_key = secrets.token_urlsafe(32)
    ttl = int(os.environ.get("SESSION_TTL_SECONDS", "900"))
    with lock:
        for k in list(sessions):
            if sessions[k]["expires"] <= now:
                del sessions[k]
        if len(sessions) >= 100:
            raise HTTPException(503, "Limite de sessões")
        sessions[session_key] = {"name": user.get("name") or "Operador", "expires": now + ttl}
    # JWTs and password are discarded; cookie holds only a local random session ID.
    response.set_cookie(COOKIE, session_key, max_age=ttl, secure=True, httponly=True, samesite="strict", path="/")
    return {"name": user.get("name") or "Operador"}


@app.post("/api/logout", dependencies=[Depends(same_origin)])
def logout(request: Request, response: Response):
    with lock:
        sessions.pop(request.cookies.get(COOKIE, ""), None)
    response.delete_cookie(COOKIE, secure=True, httponly=True, samesite="strict", path="/")
    return {"ok": True}


@app.get("/api/session")
def session(user=Depends(authenticated)):
    return {"name": user["name"]}


@app.get("/internal/catalog", dependencies=[Depends(ingest_auth)])
def catalog():
    with db(write=True) as conn:
        return {"snapshots": {r["day"]: r["sha256"] for r in conn.execute("SELECT day,sha256 FROM snapshots")}}


class Snapshot(BaseModel):
    day: str
    captured_at: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    payload_gzip_b64: str


@app.post("/internal/snapshot", dependencies=[Depends(ingest_auth)])
def ingest(body: Snapshot):
    try:
        day = datetime.strptime(body.day, "%Y-%m-%d").date().isoformat()
        captured = timestamp(body.captured_at)
        if captured.astimezone(ZoneInfo("America/Sao_Paulo")).date().isoformat() != day:
            raise ValueError("day mismatch")
        compressed = base64.b64decode(body.payload_gzip_b64, validate=True)
        with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as reader:
            raw = reader.read(16777217)
        if len(raw) > 16777216 or hashlib.sha256(raw).hexdigest() != body.sha256:
            raise ValueError("checksum mismatch")
        data = validate(raw, setting("SCALPYN_WATCHLIST_ID"), setting("SCALPYN_POOL_ID"))
    except Exception:
        raise HTTPException(422, "Snapshot inválido") from None
    with db(write=True) as conn:
        row = conn.execute("SELECT sha256 FROM snapshots WHERE day=?", (day,)).fetchone()
        if row and row[0] != body.sha256:
            raise HTTPException(409, "Snapshot existente divergente")
        if not row:
            conn.execute("INSERT INTO snapshots VALUES (?,?,?,?,?,?,?)", (day, body.captured_at, body.sha256,
                         compressed, data["asset_count"], data["queried_at"], data["last_scanned_at"]))
    return {"verified": True, "sha256": body.sha256}


class Run(BaseModel):
    id: str
    started_at: str
    finished_at: str | None
    status: str


@app.post("/internal/run", dependencies=[Depends(ingest_auth)])
def ingest_run(body: Run):
    allowed = {"running", "collection_failed", "delivery_failed", "saved_verified", "already_saved_verified"}
    try:
        UUID(body.id)
        timestamp(body.started_at)
        if body.finished_at:
            if timestamp(body.finished_at) < timestamp(body.started_at):
                raise ValueError()
        if body.status not in allowed or (body.status == "running") != (body.finished_at is None):
            raise ValueError()
    except Exception:
        raise HTTPException(422, "Execução inválida") from None
    with db(write=True) as conn:
        existing = conn.execute("SELECT status FROM runs WHERE id=?", (body.id,)).fetchone()
        if existing and existing[0] != "running":
            return {"ok": True}  # Late retries must not rewrite a terminal result.
        conn.execute("INSERT INTO runs VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET finished_at=excluded.finished_at,status=excluded.status",
                     (body.id, body.started_at, body.finished_at, body.status))
    return {"ok": True}


@app.get("/api/overview", dependencies=[Depends(authenticated)])
def overview():
    now = datetime.now(timezone.utc)
    today = now.astimezone(ZoneInfo("America/Sao_Paulo")).date()
    hour = int(os.environ.get("COLLECTION_HOUR_UTC", "9"))
    scheduled = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    next_at = scheduled if now < scheduled else scheduled + timedelta(days=1)
    with db() as conn:
        snapshots = [dict(r) for r in conn.execute("SELECT day,captured_at,sha256,asset_count,queried_at,last_scanned_at FROM snapshots ORDER BY day DESC")]
        runs = [dict(r) for r in conn.execute("SELECT * FROM runs ORDER BY started_at DESC LIMIT 30")]
    grace = int(os.environ.get("COLLECTION_GRACE_SECONDS", "1800"))
    overdue = now > scheduled + timedelta(seconds=grace) and not any(r["day"] == today.isoformat() for r in snapshots)
    status = "overdue" if overdue else "waiting"
    if runs:
        status = runs[0]["status"]
        if status == "running" and (now - timestamp(runs[0]["started_at"])).total_seconds() > grace:
            status = "unconfirmed"
        if overdue:
            status = "overdue"
    return {"snapshots": snapshots, "runs": runs, "collector_status": status, "next_scheduled_at": next_at.isoformat(),
            "schedule_label": "Diariamente · 06:00 Brasília", "reported_at": now.isoformat(), "freshness": "not_assessed"}


@app.get("/api/snapshots/{day}", dependencies=[Depends(authenticated)])
def snapshot(day: str):
    try:
        if datetime.strptime(day, "%Y-%m-%d").date().isoformat() != day:
            raise ValueError()
    except ValueError:
        raise HTTPException(404, "Snapshot não encontrado") from None
    with db() as conn:
        row = conn.execute("SELECT sha256,payload_gzip FROM snapshots WHERE day=?", (day,)).fetchone()
    if not row:
        raise HTTPException(404, "Snapshot não encontrado")
    try:
        raw = unpack((row[0], row[1]))
    except Exception:
        raise HTTPException(503, "Integridade do snapshot não confirmada") from None
    return Response(raw, media_type="application/json", headers={"X-Snapshot-SHA256": row[0]})


@app.get("/health")
def health():
    origin()
    setting("DASHBOARD_OWNER_ID")
    setting("INGEST_KEY_SHA256")
    with db(write=True) as conn:
        conn.execute("SELECT 1")
    return {"status": "ok"}


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
