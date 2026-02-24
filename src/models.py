# models.py

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from enum import Enum


# ─────────────────────────────────────────
# ENUMS
# ─────────────────────────────────────────

class DealStatus(str, Enum):
    ACTIVE = "active"
    WON = "won"
    LOST = "lost"
    STAGNANT = "stagnant"


class InvoiceStatus(str, Enum):
    DRAFT = "draft"
    SENT = "sent"
    PAID = "paid"
    OVERDUE = "overdue"


class TaskStatus(str, Enum):
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    OVERDUE = "overdue"


class LeadScore(str, Enum):
    HOT = "hot"        # > 70
    WARM = "warm"      # 40-70
    COLD = "cold"      # < 40


# ─────────────────────────────────────────
# CORE MODELS
# ─────────────────────────────────────────

@dataclass
class Deal:
    # Identité
    id: str
    title: str
    company_id: str                    # à quel client Kuria appartient ce deal

    # Valeur
    amount: float
    currency: str = "EUR"

    # Pipeline
    stage: str                         # nom du stage dans le CRM source
    stage_order: int = 0               # position ordinale du stage (0, 1, 2...)
    probability: float = 0.0           # probabilité déclarée dans le CRM (0-1)
    probability_real: Optional[float] = None  # calculée par Kuria

    # Statut
    status: DealStatus = DealStatus.ACTIVE

    # Dates critiques
    created_at: datetime = field(default_factory=datetime.utcnow)
    last_activity_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    expected_close_date: Optional[datetime] = None

    # Ownership
    owner_id: str = ""
    owner_name: str = ""

    # Acquisition
    source: str = ""                   # canal d'acquisition (pour CAC)

    # Méta
    connector_source: str = ""         # "hubspot", "salesforce", "pipedrive"
    raw_id: str = ""                   # l'id dans le système source


@dataclass
class Contact:
    # Identité
    id: str
    company_id: str

    # Données personnelles
    email: str
    first_name: str = ""
    last_name: str = ""

    # Entreprise
    company_name: str = ""
    company_size: Optional[int] = None     # nombre d'employés
    company_revenue: Optional[float] = None
    sector: str = ""

    # Acquisition
    source: str = ""                       # premier canal de contact
    source_detail: str = ""                # ex: "LinkedIn - campagne mars 2025"

    # Scoring
    score: Optional[int] = None            # 0-100, calculé par Kuria
    score_label: Optional[LeadScore] = None
    score_reason: str = ""                 # explication LLM

    # Dates
    created_at: datetime = field(default_factory=datetime.utcnow)
    last_activity_at: Optional[datetime] = None

    # Méta
    connector_source: str = ""
    raw_id: str = ""


@dataclass
class Invoice:
    # Identité
    id: str
    company_id: str

    # Montant
    amount: float
    amount_paid: float = 0.0
    currency: str = "EUR"

    # Client
    client_id: str = ""
    client_name: str = ""

    # Statut
    status: InvoiceStatus = InvoiceStatus.SENT

    # Dates critiques
    issued_at: datetime = field(default_factory=datetime.utcnow)
    due_at: Optional[datetime] = None
    paid_at: Optional[datetime] = None

    # Paiement réel vs contractuel
    payment_delay_days: Optional[int] = None
    # = (paid_at - due_at).days si paid_at existe
    # positif = en retard, négatif = en avance

    # Méta
    connector_source: str = ""
    raw_id: str = ""


@dataclass
class Task:
    # Identité
    id: str
    company_id: str

    # Contenu
    title: str
    description: str = ""

    # Assignation
    assignee_id: str = ""
    assignee_name: str = ""

    # Statut
    status: TaskStatus = TaskStatus.TODO

    # Dates
    created_at: datetime = field(default_factory=datetime.utcnow)
    due_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    # Cycle time
    cycle_time_days: Optional[float] = None
    # = (completed_at - created_at).days si completed_at existe

    # Méta
    connector_source: str = ""         # "asana", "notion", "trello"
    raw_id: str = ""


@dataclass
class Expense:
    # Identité
    id: str
    company_id: str

    # Montant
    amount: float
    currency: str = "EUR"

    # Catégorie
    vendor: str = ""
    category: str = ""                 # classification brute du connecteur
    is_recurring: bool = False

    # Date
    date: datetime = field(default_factory=datetime.utcnow)

    # Méta
    connector_source: str = ""
    raw_id: str = ""
