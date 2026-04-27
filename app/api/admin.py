import httpx
import random
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from app.core.config import (
    supabase_admin, twilio_client, TEFLON_SERVICE_SID, 
    TWILIO_VERIFY_SERVICE_SID, logger, SUPABASE_URL, SUPABASE_SERVICE_KEY,
    HTML_PAGES_CACHE, ADMIN_EMAIL
)
from app.core.dependencies import run_sync, get_current_user, AuthenticatedTradie, format_phone, log_activity

router = APIRouter(tags=["Admin"])

@router.post("/send-delete-code")
async def send_delete_code(request: Request, tradie: AuthenticatedTradie = Depends(get_current_user)):
    from app.core.config import SMS_AUTH_ENABLED
    if not SMS_AUTH_ENABLED:
        logger.info("SMS_AUTH_BYPASS: send_delete_code skipped.")
        return {"status": "success", "message": "SMS_BYPASS"}
    # [DESTRUCTIVE_ACTION_MFA: TWILIO_VERIFY]
    try:
        from app.core.dependencies import is_rate_limited
        client_ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or request.client.host
        if is_rate_limited(client_ip, "sms"):
            await log_activity(request, "DELETE_CODE_RATE_LIMIT", tradie_id=tradie.id)
            raise HTTPException(status_code=429, detail="Please wait 60 seconds before requesting another code.")

        res = await run_sync(supabase_admin.table("tradies").select("phone_number").eq("id", tradie.id).single().execute)
        if not res.data: raise HTTPException(status_code=404, detail="Profile not found.")
        
        phone = format_phone(res.data["phone_number"])
        
        if not twilio_client:
            raise HTTPException(status_code=500, detail="SMS service unavailable.")

        # Trigger Twilio Verify
        await run_sync(twilio_client.verify.v2.services(TWILIO_VERIFY_SERVICE_SID).verifications.create,
            to=phone,
            channel='sms'
        )
        
        await log_activity(request, "DELETE_CODE_SENT", tradie_id=tradie.id)
        logger.info(f"DELETE_CHALLENGE_SENT: tradie_id={tradie.id}")
        return {"status": "success", "message": "Code sent to your phone."}
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"SEND_DELETE_CODE_FAILURE: {e}")
        raise HTTPException(status_code=500, detail="Failed to send verification code.")

@router.delete("/delete-account/{slug}")
async def delete_account(slug: str, code: str, request: Request, tradie: AuthenticatedTradie = Depends(get_current_user)):
    from app.core.config import SMS_AUTH_ENABLED
    if not SMS_AUTH_ENABLED:
        logger.info("SMS_AUTH_BYPASS: delete_account accepted.")
    else:
        # [ACCOUNT_TERMINATION_LOGIC: TWILIO_VERIFY]
        try:
            res = await run_sync(supabase_admin.table("tradies").select("phone_number").eq("id", tradie.id).single().execute)
            phone = format_phone(res.data["phone_number"])

            # Check code via Twilio Verify
            check = await run_sync(twilio_client.verify.v2.services(TWILIO_VERIFY_SERVICE_SID).verification_checks.create,
                to=phone,
                code=code
            )
            
            if check.status != "approved":
                await log_activity(request, "DELETE_ACCOUNT_INVALID_CODE", tradie_id=tradie.id)
                raise HTTPException(status_code=400, detail="Invalid verification code.")
        except HTTPException as he: raise he
        except Exception as e:
            logger.error(f"VERIFY_CHECK_FAILURE: {e}")
            raise HTTPException(status_code=400, detail="Verification check failed.")

    # [SOFT_DELETE]
    try:
        now = datetime.utcnow().isoformat()
        await run_sync(supabase_admin.table("tradies").update({"deleted_at": now}).eq("id", tradie.id).execute)
        
        await log_activity(request, "ACCOUNT_SOFT_DELETED", tradie_id=tradie.id)
        logger.info(f"SOFT_DELETE_SUCCESS: tradie_id={tradie.id}")
        return {"status": "success"}
    except Exception as e:
        logger.error(f"SOFT_DELETION_FAILURE: {e}")
        raise HTTPException(status_code=500, detail="Account deletion failed.")

@router.get("/ops-tomas-99", response_class=HTMLResponse)
async def admin_page():
    return HTML_PAGES_CACHE.get("admin", "Page missing.")

@router.get("/admin-data")
async def get_admin_data(request: Request, tradie: AuthenticatedTradie = Depends(get_current_user)):
    if tradie.user.email != ADMIN_EMAIL:
        await log_activity(request, "ADMIN_ACCESS_DENIED", tradie_id=tradie.id)
        raise HTTPException(status_code=403, detail="Admin access denied.")

    await log_activity(request, "ADMIN_DATA_VIEW", tradie_id=tradie.id)
    tradies_res = await run_sync(
        supabase_admin.table("tradies")
        .select("id, business_name, email, credits, slug, created_at, deleted_at")
        .order("created_at", desc=True).execute
    )
    return tradies_res.data

@router.post("/admin/update-credits")
async def update_credits(data: dict, request: Request, tradie: AuthenticatedTradie = Depends(get_current_user)):
    if tradie.user.email != ADMIN_EMAIL:
        await log_activity(request, "ADMIN_CREDITS_DENIED", tradie_id=tradie.id)
        raise HTTPException(status_code=403, detail="Admin access denied.")

    tradie_id = data.get("tradie_id")
    new_credits = data.get("credits")

    if tradie_id is None or new_credits is None:
        raise HTTPException(status_code=400, detail="Missing data.")

    await run_sync(supabase_admin.table("tradies").update({"credits": new_credits}).eq("id", tradie_id).execute)
    await log_activity(request, "ADMIN_CREDITS_UPDATED", tradie_id=tradie.id, metadata={"target_tradie": tradie_id, "new_credits": new_credits})
    return {"status": "success"}
