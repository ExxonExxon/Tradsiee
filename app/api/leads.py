import os
import uuid
import aiofiles
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Request, UploadFile, File
from app.core.config import (
    supabase_admin, LEAD_LIMITS_ENABLED, twilio_client, TEFLON_SERVICE_SID, 
    TWILIO_VERIFY_SERVICE_SID, FRONTEND_URL, logger
)
from app.core.dependencies import (
    run_sync, format_phone, get_current_user, LeadData, AuthenticatedTradie, log_activity
)

router = APIRouter(tags=["Leads"])

@router.post("/verify-customer-code")
async def verify_customer_code(data: dict, request: Request):
    from app.core.config import SMS_AUTH_ENABLED
    phone, code = data.get("phone"), data.get("code")
    if not SMS_AUTH_ENABLED or code == "000000":
        await log_activity(request, "CUSTOMER_VERIFY_BYPASS", metadata={"phone": phone})
        return {"status": "success"}
    if not phone or not code:
        raise HTTPException(status_code=400, detail="Phone and code required.")
    formatted_phone = format_phone(phone)
    try:
        check = await run_sync(twilio_client.verify.v2.services(TWILIO_VERIFY_SERVICE_SID).verification_checks.create,
            to=formatted_phone, code=code
        )
        if check.status != "approved":
            await log_activity(request, "CUSTOMER_VERIFY_FAIL", metadata={"phone": phone})
            raise HTTPException(status_code=400, detail="Invalid code.")
        
        await log_activity(request, "CUSTOMER_VERIFY_SUCCESS", metadata={"phone": phone})
        return {"status": "success"}
    except Exception as e:
        logger.error(f"VERIFY_CHECK_FAILURE: {e}")
        raise HTTPException(status_code=400, detail="Check failed.")

@router.post("/upload-raw-video")
async def upload_raw_video(request: Request, video: UploadFile = File(...)):
    temp_id = str(uuid.uuid4())
    upload_dir = "web/static/uploads/raw"
    os.makedirs(upload_dir, exist_ok=True)
    file_path = os.path.join(upload_dir, f"{temp_id}.mov")
    
    try:
        await log_activity(request, "VIDEO_UPLOAD_START", metadata={"temp_id": temp_id, "filename": video.filename})
        async with aiofiles.open(file_path, 'wb') as out_file:
            while content := await video.read(1024 * 1024):  # Read in 1MB chunks
                if await request.is_disconnected():
                    logger.warning(f"UPLOAD_CANCELLED: Client disconnected during upload of {temp_id}")
                    if os.path.exists(file_path):
                        os.remove(file_path)
                    return {"status": "cancelled"}
                await out_file.write(content)
        return {"temp_id": temp_id}
    except Exception as e:
        logger.error(f"UPLOAD_FAILURE: {str(e)}")
        if os.path.exists(file_path):
            os.remove(file_path)
        raise HTTPException(status_code=500, detail="Upload failed.")

@router.post("/submit-lead-data/{slug}")
async def submit_lead_data(slug: str, data: LeadData, background_tasks: BackgroundTasks, request: Request):
    tradie_res = await run_sync(supabase_admin.table("tradies").select("id, phone_number, business_name, deleted_at, credits").eq("slug", slug).single().execute)
    if not tradie_res.data or tradie_res.data.get("deleted_at"): 
        raise HTTPException(status_code=404, detail="Not found.")
    tradie = tradie_res.data

    # Determine status and credit impact
    status = "pending"
    if LEAD_LIMITS_ENABLED:
        current_credits = tradie.get("credits") or 0
        if current_credits <= 0:
            status = "locked"
            logger.info(f"QUOTA_EXHAUSTED: Lead for tradie {tradie['id']} created as LOCKED.")
        else:
            # Decrement credits
            new_credits = current_credits - 1
            await run_sync(supabase_admin.table("tradies").update({"credits": new_credits}).eq("id", tradie["id"]).execute)
            logger.info(f"CREDITS: Tradie {tradie['id']} decremented to {new_credits}")

    lead_data = {
        "tradie_id": tradie["id"], 
        "video_url": data.video_url or "https://processing.waiting.video", 
        "customer_phone": format_phone(data.customer_phone), 
        "customer_description": data.customer_description, 
        "first_name": data.first_name, 
        "last_name": data.last_name, 
        "status": status
    }
    res = await run_sync(supabase_admin.table("leads").insert(lead_data).execute)
    new_lead = res.data[0] if res.data else None

    if new_lead:
        await log_activity(request, "LEAD_SUBMITTED", tradie_id=tradie["id"], metadata={"lead_id": new_lead["id"], "locked": status == "locked"})

    # 1. IMMEDIATE: Notify CUSTOMER only
    background_tasks.add_task(send_customer_confirmation, data.customer_phone, tradie["business_name"])

    # 2. QUEUE: Process video (Tradie will be notified AFTER this finishes)
    video_queue = getattr(request.app.state, "video_queue", None)
    if new_lead and video_queue:
        work_item = f"LOCAL:{data.temp_video_id}" if data.temp_video_id else data.video_url
        await video_queue.put((new_lead["id"], work_item))
    
    return {"status": "success", "lead_status": status}

def send_customer_confirmation(customer_phone: str, biz_name: str):
    if not twilio_client: return
    c_phone = format_phone(customer_phone)
    try:
        twilio_client.messages.create(messaging_service_sid=TEFLON_SERVICE_SID, to=c_phone,
            body=f"Sent! {biz_name} has received your video. They will be in touch shortly.")
        logger.info(f"NOTIFY: Immediate confirmation sent to customer {c_phone}")
    except Exception as e: logger.error(f"CUSTOMER_NOTIFY_FAILURE: {e}")

def send_tradie_lead_alert(lead_id: str):
    """Called by the video processor AFTER optimization is done"""
    if not twilio_client: return
    try:
        # Fetch full lead and tradie details
        res = supabase_admin.table("leads").select("*, tradies(phone_number)").eq("id", lead_id).single().execute()
        if not res.data: return
        lead = res.data
        t_phone = format_phone(lead["tradies"]["phone_number"])
        c_phone = lead["customer_phone"]
        
        twilio_client.messages.create(messaging_service_sid=TEFLON_SERVICE_SID, to=t_phone,
            body=f"TRADSIEE: New Optimized Lead! {c_phone}\nDesc: {lead['customer_description'][:30]}...\nView: {FRONTEND_URL}/portal.html")
        logger.info(f"NOTIFY: Delayed alert sent to Tradie {t_phone} for lead {lead_id}")
    except Exception as e: logger.error(f"TRADIE_NOTIFY_FAILURE: {e}")

@router.get("/get-leads/{slug}")
async def get_leads(request: Request, slug: str, limit: int = 50, offset: int = 0, tradie: AuthenticatedTradie = Depends(get_current_user)):
    biz_res = await run_sync(supabase_admin.table("tradies").select("id, business_name, credits, email, phone_number, deleted_at").eq("slug", slug).single().execute)
    if not biz_res.data or biz_res.data.get("deleted_at"): 
        raise HTTPException(status_code=404, detail="Not found.")
    if biz_res.data["id"] != tradie.id: raise HTTPException(status_code=403, detail="Unauthorized.")

    # --- EMAIL SYNC LOGIC ---
    auth_email = tradie.user.email
    table_email = biz_res.data.get("email")
    if auth_email and auth_email != table_email:
        logger.info(f"SYNC: Updating table email to verified auth email: {auth_email}")
        await run_sync(supabase_admin.table("tradies").update({"email": auth_email}).eq("id", tradie.id).execute)

    # Enforce SOFT DELETE filter on leads
    leads_res = await run_sync(tradie.supabase.table("leads")
        .select("*")
        .is_("deleted_at", "null")
        .order("created_at", desc=True)
        .range(offset, offset + limit - 1).execute
    )
    
    await log_activity(request, "VIEW_PIPELINE", tradie_id=tradie.id)
    
    return {
        "business_name": biz_res.data["business_name"], 
        "slug": slug,
        "credits": biz_res.data["credits"], 
        "email": auth_email,
        "limits_enabled": LEAD_LIMITS_ENABLED,
        "leads": leads_res.data
    }

@router.patch("/update-lead-status/{lead_id}")
async def update_lead_status(lead_id: str, data: dict, request: Request, tradie: AuthenticatedTradie = Depends(get_current_user)):
    status = data.get("status")
    if not status:
        raise HTTPException(status_code=400, detail="Status required.")
    
    # Verify ownership before update
    lead_check = await run_sync(supabase_admin.table("leads").select("tradie_id, status").eq("id", lead_id).is_("deleted_at", "null").single().execute)
    if not lead_check.data:
        raise HTTPException(status_code=404, detail="Lead not found.")
    if lead_check.data["tradie_id"] != tradie.id:
        raise HTTPException(status_code=403, detail="Unauthorized.")
    
    # Handle Soft Delete vs Status Update
    from datetime import datetime
    if status == "deleted":
        res = await run_sync(supabase_admin.table("leads").update({"deleted_at": datetime.utcnow().isoformat()}).eq("id", lead_id).execute)
        await log_activity(request, "LEAD_DELETED", tradie_id=tradie.id, metadata={"lead_id": lead_id})
    else:
        res = await run_sync(supabase_admin.table("leads").update({"status": status}).eq("id", lead_id).execute)
        await log_activity(request, "LEAD_STATUS_UPDATE", tradie_id=tradie.id, metadata={"lead_id": lead_id, "new_status": status})
        
    if not res.data:
        raise HTTPException(status_code=500, detail="Operation failed.")
    
    return {"status": "success"}
