#!/usr/bin/env bash
# ============================================================
# Scalpyn — First-time local setup script
#
# What this script does:
#   1. Checks prerequisites (docker, docker compose)
#   2. Copies .env.example → .env if not present
#   3. Starts db and redis (needed before migrations)
#   4. Waits for the database to be ready
#   5. Runs Alembic migrations (all 3 revisions)
#   6. Runs init_db.py to create TimescaleDB hypertables
#   7. Creates a default admin user (if none exists)
#   8. Seeds default engine configs via seed_service
#   9. Starts remaining services
#
# Usage:
#   chmod +x setup.sh
#   ./setup.sh
# ============================================================

set -euo pipefail

# ── Colors ──────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

ok()   { echo -e "${GREEN}✓${NC} $*"; }
info() { echo -e "${BLUE}→${NC} $*"; }
warn() { echo -e "${YELLOW}⚠${NC}  $*"; }
fail() { echo -e "${RED}✗${NC} $*"; exit 1; }
header() { echo -e "\n${BOLD}${BLUE}$*${NC}\n"; }

# ── 1. Prerequisites ─────────────────────────────────────────
header "Checking prerequisites"

command -v docker  >/dev/null 2>&1 || fail "Docker is not installed. Visit https://docs.docker.com/get-docker/"
command -v docker compose version >/dev/null 2>&1 || \
  command -v docker-compose >/dev/null 2>&1 || \
  fail "Docker Compose is not installed."
ok "Docker $(docker --version | awk '{print $3}' | tr -d ',')"
ok "Docker Compose available"

# ── 2. .env file ─────────────────────────────────────────────
header "Environment"

if [ ! -f .env ]; then
  cp .env.example .env
  warn ".env created from .env.example — review your credentials before going live"
else
  ok ".env already exists"
fi

# Load .env into the current shell (skip comments and blank lines)
set -a
# shellcheck disable=SC1091
source <(grep -v '^\s*#' .env | grep -v '^\s*$') 2>/dev/null || true
set +a

# ── 3. Start db + redis ───────────────────────────────────────
header "Starting database and Redis"

docker compose up -d db redis

ok "Containers started"

# ── 4. Wait for DB ────────────────────────────────────────────
header "Waiting for PostgreSQL to be ready"

RETRIES=30
WAITED=0
until docker compose exec -T db pg_isready \
    -U "${POSTGRES_USER:-scalpyn}" \
    -d "${POSTGRES_DB:-scalpyn}" >/dev/null 2>&1; do
  WAITED=$((WAITED + 1))
  if [ "$WAITED" -ge "$RETRIES" ]; then
    fail "PostgreSQL did not become ready after ${RETRIES} seconds."
  fi
  echo -n "."
  sleep 1
done
echo ""
ok "PostgreSQL is ready"

# ── 5. Alembic migrations ─────────────────────────────────────
header "Running Alembic migrations"

docker compose run --rm \
  -e DATABASE_URL="postgresql+asyncpg://${POSTGRES_USER:-scalpyn}:${POSTGRES_PASSWORD:-scalpyn}@db:5432/${POSTGRES_DB:-scalpyn}" \
  backend \
  sh -c "cd /app && alembic upgrade head"

ok "Migrations complete"

# ── 6. Init DB (TimescaleDB hypertables) ─────────────────────
header "Initializing database schema (TimescaleDB hypertables)"

docker compose run --rm \
  -e DATABASE_URL="postgresql+asyncpg://${POSTGRES_USER:-scalpyn}:${POSTGRES_PASSWORD:-scalpyn}@db:5432/${POSTGRES_DB:-scalpyn}" \
  backend \
  python -m app.init_db

ok "Schema initialized"

# ── 7. Create default admin user ─────────────────────────────
header "Creating default admin user"

docker compose run --rm \
  -e DATABASE_URL="postgresql+asyncpg://${POSTGRES_USER:-scalpyn}:${POSTGRES_PASSWORD:-scalpyn}@db:5432/${POSTGRES_DB:-scalpyn}" \
  -e JWT_SECRET="${JWT_SECRET:-change-me-use-openssl-rand-hex-32}" \
  -e ENCRYPTION_KEY="${ENCRYPTION_KEY:-0123456789abcdef0123456789abcdef}" \
  backend \
  python - <<'PYEOF'
import asyncio
from app.database import AsyncSessionLocal
from app.models.user import User
from sqlalchemy import select
from passlib.context import CryptContext
import uuid

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")

ADMIN_EMAIL    = "admin@scalpyn.local"
ADMIN_PASSWORD = "scalpyn2024!"
ADMIN_NAME     = "Admin"

async def main():
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.email == ADMIN_EMAIL))
        existing = result.scalars().first()
        if existing:
            print(f"Admin user already exists: {ADMIN_EMAIL}")
            return
        user = User(
            id=uuid.uuid4(),
            email=ADMIN_EMAIL,
            name=ADMIN_NAME,
            password_hash=pwd_ctx.hash(ADMIN_PASSWORD),
            is_active=True,
            role="admin",
        )
        db.add(user)
        await db.commit()
        print(f"Admin user created: {ADMIN_EMAIL} / {ADMIN_PASSWORD}")

asyncio.run(main())
PYEOF

ok "Admin user ready"

# ── 8. Seed default configs ───────────────────────────────────
header "Seeding default engine configs"

docker compose run --rm \
  -e DATABASE_URL="postgresql+asyncpg://${POSTGRES_USER:-scalpyn}:${POSTGRES_PASSWORD:-scalpyn}@db:5432/${POSTGRES_DB:-scalpyn}" \
  -e JWT_SECRET="${JWT_SECRET:-change-me-use-openssl-rand-hex-32}" \
  -e ENCRYPTION_KEY="${ENCRYPTION_KEY:-0123456789abcdef0123456789abcdef}" \
  backend \
  python - <<'PYEOF'
import asyncio
from app.database import AsyncSessionLocal
from app.models.user import User
from app.services.seed_service import seed_user_defaults
from app.services.config_service import config_service
from app.schemas.spot_engine_config import SpotEngineConfig
from app.schemas.futures_engine_config import FuturesEngineConfig
from sqlalchemy import select

async def main():
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User))
        users = result.scalars().all()
        for user in users:
            # Seed standard configs (indicators, score, signal, risk, etc.)
            await seed_user_defaults(db, user.id)
            # Seed spot engine default config
            try:
                await config_service.update_config(
                    db, "spot_engine", user.id,
                    SpotEngineConfig().model_dump(),
                    user.id, change_description="System Default"
                )
            except Exception as e:
                print(f"  spot_engine config: {e}")
            # Seed futures engine default config
            try:
                await config_service.update_config(
                    db, "futures_engine", user.id,
                    FuturesEngineConfig().model_dump(),
                    user.id, change_description="System Default"
                )
            except Exception as e:
                print(f"  futures_engine config: {e}")
            print(f"  Seeded configs for user: {user.email}")
        await db.commit()

asyncio.run(main())
PYEOF

ok "Default configs seeded"

# ── 9. Start all services ─────────────────────────────────────
header "Starting all services"

docker compose up -d

echo ""
echo -e "${GREEN}${BOLD}============================================================${NC}"
echo -e "${GREEN}${BOLD}  Scalpyn is running!${NC}"
echo -e "${GREEN}${BOLD}============================================================${NC}"
echo ""
echo -e "  Frontend:   ${BOLD}http://localhost:${FRONTEND_PORT:-3000}${NC}"
echo -e "  Backend:    ${BOLD}http://localhost:${BACKEND_PORT:-8000}${NC}"
echo -e "  API Docs:   ${BOLD}http://localhost:${BACKEND_PORT:-8000}/docs${NC}"
echo -e "  DB:         ${BOLD}localhost:${POSTGRES_PORT:-5432}${NC}  (${POSTGRES_USER:-scalpyn}/${POSTGRES_PASSWORD:-scalpyn})"
echo -e "  Redis:      ${BOLD}localhost:${REDIS_PORT:-6379}${NC}"
echo ""
echo -e "  Admin login: ${BOLD}admin@scalpyn.local${NC} / ${BOLD}scalpyn2024!${NC}"
echo ""
echo -e "  Logs:       ${BOLD}docker compose logs -f [service]${NC}"
echo -e "  Stop:       ${BOLD}docker compose down${NC}"
echo -e "  Reset DB:   ${BOLD}docker compose down -v${NC}"
echo ""
