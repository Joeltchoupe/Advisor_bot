# api/main.py

import os
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.routes import scan, agents, dashboard, webhooks

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s : %(message)s"
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────
# LIFESPAN
# Code qui tourne au démarrage et à l'arrêt
# ─────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Kuria API — Démarrage")
    yield
    logger.info("Kuria API — Arrêt")


# ─────────────────────────────────────────
# APP
# ─────────────────────────────────────────

app = FastAPI(
    title="Kuria API",
    version="1.0.0",
    description="API interne de Kuria — clarté d'abord",
    lifespan=lifespan
)

# CORS : Streamlit sur un autre port en local
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8501",         # Streamlit local
        os.environ.get("DASHBOARD_URL", ""),
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────

app.include_router(webhooks.router, prefix="/webhooks", tags=["webhooks"])
app.include_router(agents.router,   prefix="/agents",   tags=["agents"])
app.include_router(dashboard.router,prefix="/dashboard",tags=["dashboard"])
app.include_router(scan.router,     prefix="/scan",     tags=["scan"])


# ─────────────────────────────────────────
# HEALTH CHECK
# Railway et Railway healthcheck
# ─────────────────────────────────────────

@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "kuria-api"}


# ─────────────────────────────────────────
# GESTION D'ERREURS GLOBALE
# ─────────────────────────────────────────

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Erreur non gérée : {exc} — {request.url}")
    return JSONResponse(
        status_code=500,
        content={"error": "Erreur interne", "detail": str(exc)}
)


# api/main.py — déjà prévu, juste compléter

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8501",
        "https://ton-app.lovable.app",    # ← ajouter
        os.environ.get("DASHBOARD_URL", "")
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
                                       )

# À ajouter dans api/main.py

from fastapi import Header, HTTPException

async def verify_token(x_api_key: str = Header(...)):
    """
    Middleware d'auth minimaliste pour la V1.
    Chaque client a une clé dans Supabase.
    """
    from services.database import get_client
    client = get_client()

    result = client.table("companies").select(
        "id"
    ).eq("api_key", x_api_key).limit(1).execute()

    if not result.data:
        raise HTTPException(status_code=401, detail="Non autorisé")

    return result.data[0]["id"]
