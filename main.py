import os, secrets
from datetime import datetime, timezone
from typing import Optional
from fastapi import FastAPI, HTTPException, Header
import httpx
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, String, BigInteger, Integer, DateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./alicia_mini_apps.db")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0") or 0)
API_SECRET = os.getenv("API_SECRET", "").strip()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "*").strip()
STARS_PLAN_LIMIT = int(os.getenv("STARS_PLAN_LIMIT", "10000"))
engine = create_engine(DATABASE_URL, pool_pre_ping=True,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

class Base(DeclarativeBase): pass

class User(Base):
    __tablename__ = "users"
    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    first_name: Mapped[str] = mapped_column(String(128), default="")
    username: Mapped[str] = mapped_column(String(128), default="")
    credits: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

class Payment(Base):
    __tablename__ = "payments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    stars: Mapped[int] = mapped_column(Integer)
    payload: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    telegram_charge_id: Mapped[Optional[str]] = mapped_column(String(255), unique=True, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    credits: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    validated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

Base.metadata.create_all(engine)
app = FastAPI(title="Alicia Mini Apps Backend", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if FRONTEND_ORIGIN == "*" else [FRONTEND_ORIGIN],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

def db(): return SessionLocal()
def admin(uid):
    if not ADMIN_USER_ID or uid != ADMIN_USER_ID: raise HTTPException(403, "Admin access required")
def secret(value):
    if API_SECRET and value != API_SECRET: raise HTTPException(401, "Invalid API secret")

class UserIn(BaseModel):
    telegram_id: int
    first_name: str = ""
    username: str = ""

class PaymentIn(BaseModel):
    telegram_id: int
    stars: int = Field(gt=0, le=10000)

class ValidateIn(BaseModel):
    admin_user_id: int
    credits: int = Field(ge=0, le=10000000)

@app.get("/plans")
def plans():
    return {
        "currency": "XTR",
        "plans": [
            {"id": "starter", "stars": 100, "label": "100 ⭐"},
            {"id": "standard", "stars": 500, "label": "500 ⭐"},
            {"id": "pro", "stars": 1000, "label": "1000 ⭐"},
        ],
    }

@app.get("/health")
def health(): return {"status":"ok","service":"alicia-mini-apps"}

@app.post("/users")
def upsert_user(x: UserIn):
    with db() as s:
        u=s.get(User,x.telegram_id)
        if not u: u=User(telegram_id=x.telegram_id); s.add(u)
        u.first_name=x.first_name[:128]; u.username=x.username[:128]
        s.commit()
        return {"telegram_id":u.telegram_id,"credits":u.credits}

@app.get("/users/{telegram_id}")
def get_user(telegram_id:int):
    with db() as s:
        u=s.get(User,telegram_id)
        if not u: raise HTTPException(404,"User not found")
        return {"telegram_id":u.telegram_id,"credits":u.credits}

@app.post("/payments/create")
async def create_payment(x: PaymentIn, x_api_secret: Optional[str] = Header(default=None)):
    if not TELEGRAM_BOT_TOKEN:
        raise HTTPException(503, "TELEGRAM_BOT_TOKEN is not configured")
    if x.stars > STARS_PLAN_LIMIT:
        raise HTTPException(400, "Stars amount exceeds configured limit")

    payload = f"alicia_{x.telegram_id}_{secrets.token_urlsafe(16)}"
    with db() as s:
        if not s.get(User, x.telegram_id):
            s.add(User(telegram_id=x.telegram_id))
        p = Payment(telegram_id=x.telegram_id, stars=x.stars, payload=payload, status="pending")
        s.add(p)
        s.commit()
        s.refresh(p)
        payment_id = p.id

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/createInvoiceLink"
    body = {
        "title": "Alicia Mini Apps",
        "description": f"Abonnement Alicia Mini Apps — {x.stars} Stars",
        "payload": payload,
        "currency": "XTR",
        "prices": [{"label": "Alicia Mini Apps", "amount": x.stars}],
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(url, json=body)
            response.raise_for_status()
            data = response.json()
    except Exception as exc:
        with db() as s:
            p = s.get(Payment, payment_id)
            if p and p.status == "pending":
                s.delete(p)
                s.commit()
        raise HTTPException(502, f"Telegram invoice error: {exc}")

    if not data.get("ok") or not data.get("result"):
        with db() as s:
            p = s.get(Payment, payment_id)
            if p and p.status == "pending":
                s.delete(p)
                s.commit()
        raise HTTPException(502, data.get("description", "Telegram invoice creation failed"))

    return {
        "payment_id": payment_id,
        "payload": payload,
        "stars": x.stars,
        "status": "pending",
        "invoice_url": data["result"],
    }

@app.get("/payments/{payment_id}")
def payment(payment_id:int,telegram_id:int):
    with db() as s:
        p=s.get(Payment,payment_id)
        if not p or p.telegram_id!=telegram_id: raise HTTPException(404,"Payment not found")
        return {"id":p.id,"stars":p.stars,"credits":p.credits,"status":p.status,
                "created_at":p.created_at,"validated_at":p.validated_at}

@app.post("/payments/{payment_id}/mark-paid")
def mark_paid(payment_id:int,telegram_charge_id:str,stars:int,payload:str,x_api_secret:Optional[str]=Header(default=None)):
    secret(x_api_secret)
    with db() as s:
        p=s.get(Payment,payment_id)
        if not p or p.payload!=payload: raise HTTPException(404,"Payment not found")
        if p.status!="pending": return {"status":p.status}
        if stars!=p.stars: raise HTTPException(400,"Stars amount mismatch")
        p.telegram_charge_id=telegram_charge_id; p.status="paid"; s.commit()
        return {"status":"paid","payment_id":p.id}

@app.get("/admin/payments")
def admin_payments(admin_user_id:int):
    admin(admin_user_id)
    with db() as s:
        rows=s.query(Payment).order_by(Payment.id.desc()).limit(100).all()
        return [{"id":p.id,"telegram_id":p.telegram_id,"stars":p.stars,"credits":p.credits,
                 "status":p.status,"payload":p.payload,"telegram_charge_id":p.telegram_charge_id,
                 "created_at":p.created_at,"validated_at":p.validated_at} for p in rows]

@app.post("/admin/payments/{payment_id}/validate")
def validate(payment_id:int,x:ValidateIn):
    admin(x.admin_user_id)
    with db() as s:
        p=s.get(Payment,payment_id)
        if not p: raise HTTPException(404,"Payment not found")
        if p.status=="validated": raise HTTPException(409,"Payment already validated")
        if p.status!="paid": raise HTTPException(409,"Payment is not paid")
        u=s.get(User,p.telegram_id)
        if not u: u=User(telegram_id=p.telegram_id); s.add(u)
        u.credits+=x.credits; p.credits=x.credits; p.status="validated"
        p.validated_at=datetime.now(timezone.utc); s.commit()
        return {"status":"validated","payment_id":p.id,"telegram_id":p.telegram_id,
                "stars":p.stars,"credits_added":x.credits,"new_balance":u.credits}
