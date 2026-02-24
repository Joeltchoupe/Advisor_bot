# api/main.py

import os
import logging
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.routes import scan, agents, dashboard, webhooks

# Charge .env en local uniquement (en prod Railway injecte les vars)
load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s : %(message)s",
)
logger = logging.getLogger("kuria.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Kuria API — Démarrage")
    yield
    logger.info("Kuria API — Arrêt")


app = FastAPI(
    title="Kuria API",
    version="1.0.0",
    description="API Kuria — clarté d'abord",
    lifespan=lifespan,
)

# ─────────────────────────────────────────
# CORS
# ─────────────────────────────────────────
# Recommandation V1:
# FRONTEND_ORIGINS="https://kuria.lovable.app,https://kuria.vercel.app"
frontend_origins = os.getenv("FRONTEND_ORIGINS", "")
origins = [
    "http://localhost:3000",   # React local
    "http://localhost:8501",   # Streamlit local (si tu l'utilises encore)
]

if frontend_origins:
    origins.extend([o.strip() for o in frontend_origins.split(",") if o.strip()])

# Ancien nom si tu avais déjà DASHBOARD_URL
dashboard_url = os.getenv("DASHBOARD_URL", "").strip()
if dashboard_url:
    origins.append(dashboard_url)

# Dédoublonner
origins = sorted(set(origins))

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],  # inclut X-API-KEY
)

# ─────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────
app.include_router(webhooks.router, prefix="/webhooks", tags=["webhooks"])
app.include_router(agents.router, prefix="/agents", tags=["agents"])
app.include_router(dashboard.router, prefix="/dashboard", tags=["dashboard"])
app.include_router(scan.router, prefix="/scan", tags=["scan"])

# ─────────────────────────────────────────
# HEALTH
# ─────────────────────────────────────────
@app.get("/")
def root() -> dict:
    return {"status": "ok", "service": "kuria-api"}

@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "kuria-api"}

# ─────────────────────────────────────────
# ERREURS GLOBALES
# ─────────────────────────────────────────
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception(f"Erreur non gérée — {request.method} {request.url}: {exc}")
    return JSONResponse(
        status_code=500,
        content={"error": "Erreur interne", "detail": str(exc)},
)
