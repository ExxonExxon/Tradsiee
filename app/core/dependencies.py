import re
import random
import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, constr, EmailStr

from app.core.config import (
    supabase_admin, get_supabase_user_client, twilio_client, TEFLON_SERVICE_SID,
    verification_codes, sms_last_sent, registration_attempts, lead_submissions, FRONTEND_URL, logger
)

security = HTTPBearer()

# ... Pydantic Models ...

class ForgotPasswordSchema(BaseModel):
    email: EmailStr

class ResetPasswordSchema(BaseModel):
    new_password: constr(min_length=8)

class UpdateProfileSchema(BaseModel):
    business_name: Optional[str] = None
    slug: Optional[str] = None

class UpdateAccountSchema(BaseModel):
    email: Optional[EmailStr] = None
    password: Optional[constr(min_length=8)] = None

class LeadData(BaseModel):
    video_url: str
    customer_phone: str
    customer_description: constr(max_length=1000) = "No description"
    first_name: str = ""
    last_name: str = ""

# --- Utilities ---

async def run_sync(func, *args, **kwargs):
    return await asyncio.to_thread(func, *args, **kwargs)

def format_phone(phone: str) -> str:
    clean = re.sub(r"[^\d+]", "", phone)
    if not clean.startswith("+"):
        return f"+61{clean.lstrip('0')}"
    return clean

def is_rate_limited(ip: str, limit_type: str = "sms") -> bool:
    now = datetime.now()
    if limit_type == "sms":
        last_sent = sms_last_sent.get(ip)
        if last_sent and (now - last_sent).total_seconds() < 60:
            return True
        sms_last_sent[ip] = now
    elif limit_type == "register":
        attempts = registration_attempts.get(ip, [])
        valid_attempts = [t for t in attempts if (now - t).total_seconds() < 3600]
        if len(valid_attempts) >= 5:
            return True
        valid_attempts.append(now)
        registration_attempts[ip] = valid_attempts
    elif limit_type == "lead_submit":
        attempts = lead_submissions.get(ip, [])
        valid_attempts = [t for t in attempts if (now - t).total_seconds() < 3600] # max 10 leads per hour per IP
        if len(valid_attempts) >= 10:
            return True
        valid_attempts.append(now)
        lead_submissions[ip] = valid_attempts
    return False

async def generate_unique_slug(name: str) -> str:
    base = re.sub(r"[^a-zA-Z0-9]", "-", name.lower()).strip("-")
    for _ in range(5):
        suffix = random.randint(1000, 9999)
        candidate = f"{base}-{suffix}"
        res = await run_sync(supabase_admin.table("tradies").select("id").eq("slug", candidate).execute)
        if not res.data:
            return candidate
    return f"{base}-{random.getrandbits(32)}"

def get_base_url(request: Request) -> str:
    scheme = request.url.scheme
    netloc = request.url.netloc
    return f"{scheme}://{netloc}"

# --- Authentication Dependency ---

class AuthenticatedTradie:
    def __init__(self, user: Any, client: Any):
        self.user = user
        self.supabase = client
        self.id = user.id

async def get_current_user(auth: HTTPAuthorizationCredentials = Depends(security)) -> AuthenticatedTradie:
    if not auth or not auth.credentials or auth.credentials == "null":
        raise HTTPException(status_code=401, detail="Session required.")
    try:
        res = await run_sync(supabase_admin.auth.get_user, auth.credentials)
        user = getattr(res, 'user', None) or (res.get('user') if isinstance(res, dict) else None)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid session.")
        
        if not getattr(user, 'email_confirmed_at', None):
            raise HTTPException(status_code=403, detail="EMAIL_NOT_VERIFIED")

        # [SOFT_DELETE_VERIFICATION]
        # Ensure the user has not marked their profile for deletion.
        profile_res = await run_sync(supabase_admin.table("tradies").select("deleted_at").eq("id", user.id).single().execute)
        if profile_res.data and profile_res.data.get("deleted_at"):
            raise HTTPException(status_code=403, detail="ACCOUNT_SCHEDULED_FOR_DELETION")
            
        return AuthenticatedTradie(user, get_supabase_user_client(auth.credentials))
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"AUTH_VERIFICATION_FAILURE: {e}")
        raise HTTPException(status_code=401, detail="Authentication failed.")
