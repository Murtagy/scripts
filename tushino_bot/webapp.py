import hashlib
import hmac
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl

import requests

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import slots_service
import week_control
from slots_service import ConflictError, NotFoundError, PermissionError, ValidationError

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")
ALLOW_INSECURE_WEBAPP = os.environ.get("ALLOW_INSECURE_WEBAPP", "0") == "1"
ADMIN_USER_IDS = {
    int(x.strip())
    for x in os.environ.get("ADMIN_USER_IDS", "").split(",")
    if x.strip()
}

app = FastAPI(title="Tushino Slots Bot")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class ItemCreate(BaseModel):
    name: str


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/api/week/active")
def get_active_week(x_telegram_init_data: str | None = Header(default=None)):
    require_user(x_telegram_init_data)
    return slots_service.get_active_week()


@app.post("/api/week/init")
def init_week(x_telegram_init_data: str | None = Header(default=None)):
    user = require_user(x_telegram_init_data)
    require_admin(user)
    result = slots_service.create_or_get_active_week()
    week_control.refresh_week_control_sync()
    return result


@app.post("/api/week/reset")
def reset_week(x_telegram_init_data: str | None = Header(default=None)):
    user = require_user(x_telegram_init_data)
    require_admin(user)
    result = slots_service.reset_active_week()
    week_control.refresh_week_control_sync()
    return result


@app.get("/api/slots/{slot_code}")
def get_slot(slot_code: str, x_telegram_init_data: str | None = Header(default=None)):
    require_user(x_telegram_init_data)
    return slots_service.get_slot(slot_code)


@app.post("/api/slots/{slot_code}/items")
def create_item(slot_code: str, payload: ItemCreate, x_telegram_init_data: str | None = Header(default=None)):
    user = require_user(x_telegram_init_data)
    result = slots_service.add_item(slot_code, payload.name, user)
    week_control.refresh_week_control_sync()
    return result


@app.delete("/api/items/{item_id}")
def delete_item(item_id: int, x_telegram_init_data: str | None = Header(default=None)):
    user = require_user(x_telegram_init_data)
    slots_service.delete_item(item_id, user, ADMIN_USER_IDS)
    week_control.refresh_week_control_sync()
    return {"ok": True}


@app.get("/api/items/{item_id}")
def get_item(item_id: int, x_telegram_init_data: str | None = Header(default=None)):
    require_user(x_telegram_init_data)
    return slots_service.get_item(item_id)


@app.post("/api/items/{item_id}/roll")
def roll_item(item_id: int, x_telegram_init_data: str | None = Header(default=None)):
    user = require_user(x_telegram_init_data)
    result = slots_service.roll_for_item(item_id, user)
    week_control.refresh_week_control_sync()
    return result


@app.post("/api/items/{item_id}/call")
def call_item(item_id: int, x_telegram_init_data: str | None = Header(default=None)):
    user = require_user(x_telegram_init_data)
    result = slots_service.call_item(item_id, user)
    week_control.refresh_week_control_sync()
    return result


@app.post("/api/items/{item_id}/reopen")
def reopen_item(item_id: int, x_telegram_init_data: str | None = Header(default=None)):
    user = require_user(x_telegram_init_data)
    require_admin(user)
    result = slots_service.reopen_item(item_id)
    week_control.refresh_week_control_sync()
    return result


@app.exception_handler(NotFoundError)
async def not_found_handler(_, exc: NotFoundError):
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(ConflictError)
async def conflict_handler(_, exc: ConflictError):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(ValidationError)
async def validation_handler(_, exc: ValidationError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(PermissionError)
async def permission_handler(_, exc: PermissionError):
    return JSONResponse(status_code=403, content={"detail": str(exc)})


def require_admin(user: dict[str, Any]) -> None:
    if ADMIN_USER_IDS and user["user_id"] not in ADMIN_USER_IDS:
        raise HTTPException(status_code=403, detail="Admin only")


def require_user(init_data: str | None) -> dict[str, Any]:
    if ALLOW_INSECURE_WEBAPP and not init_data:
        return {"user_id": 0, "username": "local_dev", "display_name": "Local Dev"}
    if not BOT_TOKEN:
        raise HTTPException(status_code=500, detail="BOT_TOKEN missing")
    if not init_data:
        raise HTTPException(status_code=401, detail="Telegram init data missing")
    user = validate_init_data(init_data, BOT_TOKEN)
    ensure_chat_member(user["user_id"])
    return user


def ensure_chat_member(user_id: int) -> None:
    if not CHAT_ID:
        raise HTTPException(status_code=500, detail="CHAT_ID missing")
    r = requests.get(
        f"https://api.telegram.org/bot{BOT_TOKEN}/getChatMember",
        params={"chat_id": CHAT_ID, "user_id": user_id},
        timeout=20,
    )
    data = r.json()
    if not data.get("ok"):
        raise HTTPException(status_code=403, detail="User not allowed")
    status = data["result"].get("status")
    if status not in {"creator", "administrator", "member", "restricted"}:
        raise HTTPException(status_code=403, detail="Only members of target chat allowed")


def validate_init_data(init_data: str, bot_token: str) -> dict[str, Any]:
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    their_hash = pairs.pop("hash", None)
    if not their_hash:
        raise HTTPException(status_code=401, detail="Telegram hash missing")

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    calc_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc_hash, their_hash):
        raise HTTPException(status_code=401, detail="Telegram auth failed")

    raw_user = pairs.get("user")
    if not raw_user:
        raise HTTPException(status_code=401, detail="Telegram user missing")
    tg_user = json.loads(raw_user)
    username = tg_user.get("username")
    pseudonim = (tg_user.get("first_name") or "").strip()
    full_name = " ".join(x for x in [tg_user.get("first_name"), tg_user.get("last_name")] if x).strip()
    return {
        "user_id": tg_user["id"],
        "username": f"@{username}" if username else None,
        "display_name": pseudonim or full_name or username or str(tg_user["id"]),
    }
