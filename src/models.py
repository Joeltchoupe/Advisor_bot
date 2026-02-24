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
    HOT = "hot"
    WARM = "warm"
    COLD = "cold"


# ─────────────────────────────────────────
# CORE MODELS
# RÈGLE PYTHON 3.12 :
# - champs obligatoires (sans default) d'abord
# - champs avec default ensuite
# ─────────────────────────────────────────

@dataclass
class Deal:
    # Obligatoires
    id: str
    company_id: str
    connector_source: str          # NOT NULL en DB
    raw_id: str                    # NOT NULL en DB

    title: str
    amount: float
    stage: str

    # Optionnels / defaults
    currency: str = "EUR"
    stage_order: int = 0
    probability: float = 0.0
    probability_real: Optional[float] = None

    status: DealStatus = DealStatus.ACTIVE

    created_at: datetime = field(default_factory=datetime.utcnow)
    last_activity_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    expected_close_date: Optional[datetime] = None

    owner_id: str = ""
    owner_name: str = ""
    source: str = ""


@dataclass
class Contact:
    # Obligatoires
    id: str
    company_id: str
    connector_source: str
    raw_id: str

    email: str

    # Optionnels / defaults
    first_name: str = ""
    last_name: str = ""

    company_name: str = ""
    company_size: Optional[int] = None
    company_revenue: Optional[float] = None
    sector: str = ""

    source: str = ""
    source_detail: str = ""

    score: Optional[int] = None
    score_label: Optional[LeadScore] = None
    score_reason: str = ""

    created_at: datetime = field(default_factory=datetime.utcnow)
    last_activity_at: Optional[datetime] = None


@dataclass
class Invoice:
    # Obligatoires
    id: str
    company_id: str
    connector_source: str
    raw_id: str

    amount: float

    # Optionnels / defaults
    amount_paid: float = 0.0
    currency: str = "EUR"

    client_id: str = ""
    client_name: str = ""

    status: InvoiceStatus = InvoiceStatus.SENT

    issued_at: datetime = field(default_factory=datetime.utcnow)
    due_at: Optional[datetime] = None
    paid_at: Optional[datetime] = None
    payment_delay_days: Optional[int] = None


@dataclass
class Task:
    # Obligatoires
    id: str
    company_id: str
    connector_source: str
    raw_id: str

    title: str

    # Optionnels / defaults
    description: str = ""

    assignee_id: str = ""
    assignee_name: str = ""

    status: TaskStatus = TaskStatus.TODO

    created_at: datetime = field(default_factory=datetime.utcnow)
    due_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    cycle_time_days: Optional[float] = None


@dataclass
class Expense:
    # Obligatoires
    id: str
    company_id: str
    connector_source: str
    raw_id: str

    amount: float

    # Optionnels / defaults
    currency: str = "EUR"

    vendor: str = ""
    category: str = ""
    is_recurring: bool = False

    date: datetime = field(default_factory=datetime.utcnow)
