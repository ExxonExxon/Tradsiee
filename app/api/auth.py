import re
import random
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials
from app.core.config import (
    supabase_admin, twilio_client, TEFLON_SERVICE_SID, 
    TWILIO_VERIFY_SERVICE_SID, logger, SUPABASE_URL, SUPABASE_SERVICE_KEY
)
from app.core.dependencies import (
    run_sync, format_phone, is_rate_limited, generate_unique_slug, 
    get_base_url, security, ForgotPasswordSchema, ResetPasswordSchema,
    UpdateProfileSchema, UpdateAccountSchema, AuthenticatedTradie,
    get_current_user
)

router = APIRouter(tags=["Authentication"])

async def force_delete_auth_user(user_id: str):
    # [ADMIN_BYPASS_CLEANUP]
    # Uses raw API to ensure Auth records are wiped even if SDK session is locked.
    async with httpx.AsyncClient() as client:
        try:
            res = await client.delete(
                f"{SUPABASE_URL}/auth/v1/admin/users/{user_id}",
                headers={
                    "apikey": SUPABASE_SERVICE_KEY,
                    "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                    "Content-Type": "application/json"
                }
            )
            if res.status_code in [200, 204, 404]:
                logger.info(f"AUTH_RECLAMATION_SUCCESS: {user_id}")
            else:
                logger.error(f"AUTH_RECLAMATION_FAILED: {res.status_code} - {res.text}")
        except Exception as e:
            logger.error(f"AUTH_RECLAMATION_EXCEPTION: {e}")

@router.post("/resend-confirmation")
async def resend_confirmation(data: dict):
    email = data.get("email")
    if not email: raise HTTPException(status_code=400, detail="Email required.")
    try:
        await run_sync(supabase_admin.auth.resend, {"type": "signup", "email": email})
        return {"status": "success"}
    except Exception as e:
        logger.error(f"RESEND_FAILURE: {e}")
        raise HTTPException(status_code=400, detail="Failed to resend email.")

@router.post("/register")
async def register_tradie(data: dict, request: Request):
    client_ip = request.client.host
    if is_rate_limited(client_ip, "register"):
        logger.warning(f"RATE_LIMIT_EXCEEDED: register from ip={client_ip}")
        raise HTTPException(status_code=429, detail="Too many registration attempts. Please try again in an hour.")

    name, email, password, phone = data.get("business_name"), data.get("email"), data.get("password"), data.get("phone_number")
    if not all([name, email, password, phone]):
        raise HTTPException(status_code=400, detail="Missing data.")

    try:
        formatted_phone = format_phone(phone)
        
        # 1. Email Conflict Check (Including Unverified/Soft-Deleted Reclamation)
        email_check = await run_sync(supabase_admin.table("tradies").select("id, deleted_at").eq("email", email).execute)
        if email_check.data:
            existing = email_check.data[0]
            is_verified = False
            try:
                auth_user = await run_sync(supabase_admin.auth.admin.get_user_by_id, existing["id"])
                if auth_user.user and auth_user.user.email_confirmed_at:
                    is_verified = True
            except: pass # Orphan profile

            if is_verified and not existing.get("deleted_at"):
                raise HTTPException(status_code=400, detail="This email is already registered. Please sign in.")
            
            logger.info(f"IDENTITY_RECLAMATION: wiping email={email} (verified={is_verified}, soft_delete={bool(existing.get('deleted_at'))})")
            await run_sync(supabase_admin.table("tradies").delete().eq("id", existing["id"]).execute)
            await force_delete_auth_user(existing["id"])

        # 2. Phone Conflict Check (Including Unverified/Soft-Deleted Reclamation)
        phone_check = await run_sync(supabase_admin.table("tradies").select("id, deleted_at").eq("phone_number", formatted_phone).execute)
        if phone_check.data:
            existing = phone_check.data[0]
            is_verified = False
            try:
                auth_user = await run_sync(supabase_admin.auth.admin.get_user_by_id, existing["id"])
                if auth_user.user and auth_user.user.email_confirmed_at:
                    is_verified = True
            except: pass # Orphan profile

            if is_verified and not existing.get("deleted_at"):
                raise HTTPException(status_code=400, detail="This phone number is already verified.")
            
            logger.info(f"IDENTITY_RECLAMATION: wiping phone={formatted_phone} (verified={is_verified}, soft_delete={bool(existing.get('deleted_at'))})")
            await run_sync(supabase_admin.table("tradies").delete().eq("id", existing["id"]).execute)
            await force_delete_auth_user(existing["id"])

        # [STAGING]
        new_slug = await generate_unique_slug(name)
        staged_data = {
            "business_name": name, "email": email, "password": password,
            "phone_number": formatted_phone, "slug": new_slug, "credits": 10
        }
        await run_sync(supabase_admin.table("staged_registrations").upsert(staged_data, on_conflict="phone_number").execute)
        return {"status": "success", "slug": new_slug}
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"REGISTRATION_FAILURE: {e}")
        raise HTTPException(status_code=400, detail="Registration failed.")

@router.post("/login")
async def login(data: dict):
    email, password = data.get("email"), data.get("password")
    try:
        auth_res = await run_sync(supabase_admin.auth.sign_in_with_password, {"email": email, "password": password})
        
        res = await run_sync(supabase_admin.table("tradies").select("slug, deleted_at").eq("id", auth_res.user.id).single().execute)
        if not res.data:
            raise HTTPException(status_code=403, detail="Profile missing.")
        
        if res.data.get("deleted_at"):
            raise HTTPException(status_code=403, detail="ACCOUNT_SCHEDULED_FOR_DELETION")

        return {"slug": res.data["slug"], "access_token": auth_res.session.access_token}
    except HTTPException as he:
        raise he
    except Exception as e:
        msg = str(e)
        if "Email not confirmed" in msg:
            tradie = await run_sync(supabase_admin.table("tradies").select("slug").eq("email", email).single().execute)
            if tradie.data:
                raise HTTPException(status_code=403, detail="EMAIL_NOT_VERIFIED")
        
        logger.error(f"LOGIN_FAILURE: {e}")
        raise HTTPException(status_code=401, detail="Invalid credentials.")

@router.post("/send-verification")
async def send_verification(data: dict, request: Request):
    client_ip = request.client.host
    if is_rate_limited(client_ip, "sms"):
        raise HTTPException(status_code=429, detail="Please wait 60 seconds before requesting another code.")

    phone = data.get("phone")
    if not phone: raise HTTPException(status_code=400, detail="Phone required.")
    
    formatted_phone = format_phone(phone)
    if not twilio_client:
        raise HTTPException(status_code=500, detail="SMS service unavailable.")

    try:
        await run_sync(twilio_client.verify.v2.services(TWILIO_VERIFY_SERVICE_SID).verifications.create,
            to=formatted_phone,
            channel='sms'
        )
        return {"status": "success"}
    except Exception as e:
        logger.error(f"VERIFY_DISPATCH_FAILURE: {e}")
        raise HTTPException(status_code=500, detail="Failed to send verification SMS.")

@router.post("/verify-code")
async def verify_code(data: dict, request: Request):
    phone, code = data.get("phone"), data.get("code")
    formatted_phone = format_phone(phone)
    
    if not twilio_client:
        raise HTTPException(status_code=500, detail="SMS service unavailable.")

    try:
        check = await run_sync(twilio_client.verify.v2.services(TWILIO_VERIFY_SERVICE_SID).verification_checks.create,
            to=formatted_phone,
            code=code
        )
        if check.status != "approved":
            raise HTTPException(status_code=400, detail="Invalid or expired verification code.")
    except Exception as e:
        logger.error(f"VERIFY_CHECK_FAILURE: {e}")
        raise HTTPException(status_code=400, detail="Verification check failed.")
    
    staged_res = await run_sync(supabase_admin.table("staged_registrations").select("*").eq("phone_number", formatted_phone).single().execute)
    if not staged_res.data:
        raise HTTPException(status_code=400, detail="Signup session expired.")
    
    profile_data = staged_res.data
    password = profile_data.pop("password")
    base_url = get_base_url(request).rstrip('/')
    redirect_url = f"{base_url}/verified"
    
    try:
        await run_sync(supabase_admin.table("tradies").delete().eq("phone_number", formatted_phone).execute)

        auth_res = await run_sync(supabase_admin.auth.sign_up, {
            "email": profile_data["email"], 
            "password": password,
            "options": { "redirect_to": redirect_url, "email_redirect_to": redirect_url }
        })
        if not auth_res.user:
            raise HTTPException(status_code=400, detail="Auth creation failed.")
        
        profile_data["id"] = auth_res.user.id
        await run_sync(supabase_admin.table("tradies").insert(profile_data).execute)
        await run_sync(supabase_admin.table("staged_registrations").delete().eq("phone_number", formatted_phone).execute)
        
        return {"status": "success"}
    except Exception as e:
        logger.error(f"AUTH_PROVISIONING_FAILURE: {e}")
        raise HTTPException(status_code=500, detail="Failed to finalize account.")

@router.post("/forgot-password")
async def forgot_password(data: ForgotPasswordSchema, request: Request):
    try:
        base_url = get_base_url(request)
        await run_sync(supabase_admin.auth.reset_password_for_email, 
            data.email, 
            options={"redirect_to": f"{base_url}/update-password"}
        )
        return {"status": "success"}
    except Exception as e:
        logger.error(f"PASSWORD_RESET_FAILURE: {e}")
        raise HTTPException(status_code=400, detail="Failed to send reset link.")

@router.post("/update-password")
async def update_password(data: ResetPasswordSchema, auth: HTTPAuthorizationCredentials = Depends(security)):
    try:
        res = await run_sync(supabase_admin.auth.get_user, auth.credentials)
        user = getattr(res, 'user', None) or (res.get('user') if isinstance(res, dict) else None)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid session.")
        
        await run_sync(supabase_admin.auth.admin.update_user_by_id, user.id, {"password": data.new_password})
        tradie_res = await run_sync(supabase_admin.table("tradies").select("slug").eq("id", user.id).single().execute)
        slug = tradie_res.data.get("slug") if tradie_res.data else None
        return {"status": "success", "slug": slug}
    except Exception as e:
        logger.error(f"PASSWORD_UPDATE_FAILURE: {e}")
        raise HTTPException(status_code=400, detail="Failed to update password.")

@router.patch("/update-profile")
async def update_profile(data: UpdateProfileSchema, tradie: AuthenticatedTradie = Depends(get_current_user)):
    updates = {}
    if data.business_name: updates["business_name"] = data.business_name
    if data.slug:
        if not re.match(r'^[a-z0-9-]+$', data.slug.lower()):
            raise HTTPException(status_code=400, detail="Slug can only contain letters, numbers, and hyphens.")
        check = await run_sync(supabase_admin.table("tradies").select("id").eq("slug", data.slug.lower()).neq("id", tradie.id).execute)
        if check.data:
            raise HTTPException(status_code=400, detail="This slug is already taken.")
        updates["slug"] = data.slug.lower()

    if not updates: return {"status": "no-op"}
    res = await run_sync(supabase_admin.table("tradies").update(updates).eq("id", tradie.id).execute)
    if not res.data: raise HTTPException(status_code=400, detail="Update failed.")
    return {"status": "success", "data": res.data[0]}

@router.patch("/update-account")
async def update_account(data: UpdateAccountSchema, tradie: AuthenticatedTradie = Depends(get_current_user)):
    auth_updates = {}
    if data.email: auth_updates["email"] = data.email
    if data.password: auth_updates["password"] = data.password
    if not auth_updates: return {"status": "no-op"}

    try:
        await run_sync(supabase_admin.auth.admin.update_user_by_id, tradie.id, auth_updates)
        if data.email:
            await run_sync(supabase_admin.table("tradies").update({"email": data.email}).eq("id", tradie.id).execute)
        return {"status": "success"}
    except Exception as e:
        logger.error(f"ACCOUNT_UPDATE_FAILURE: {e}")
        raise HTTPException(status_code=400, detail="Failed to update account credentials.")
