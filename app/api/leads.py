import os
import uuid
import aiofiles
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Request, UploadFile, File
from app.core.config import (
    supabase_admin, LEAD_LIMITS_ENABLED, twilio_client, TEFLON_SERVICE_SID, 
    TWILIO_VERIFY_SERVICE_SID, FRONTEND_URL, logger
)
from app.core.dependencies import (
    run_sync, format_phone, get_current_user, LeadData, AuthenticatedTradie
)

router = APIRouter(tags=["Leads"])

@router.post("/verify-customer-code")
async def verify_customer_code(data: dict, request: Request):
    from app.core.config import SMS_AUTH_ENABLED
    phone, code = data.get("phone"), data.get("code")
    if not SMS_AUTH_ENABLED or code == "000000":
        return {"status": "success"}
    if not phone or not code:
        raise HTTPException(status_code=400, detail="Phone and code required.")
    formatted_phone = format_phone(phone)
    try:
        check = await run_sync(twilio_client.verify.v2.services(TWILIO_VERIFY_SERVICE_SID).verification_checks.create,
            to=formatted_phone, code=code
        )
        if check.status != "approved":
            raise HTTPException(status_code=400, detail="Invalid code.")
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
    tradie_res = await run_sync(supabase_admin.table("tradies").select("id, phone_number, business_name").eq("slug", slug).single().execute)
    if not tradie_res.data: raise HTTPException(status_code=404, detail="Not found.")
    tradie = tradie_res.data

    lead_data = {
        "tradie_id": tradie["id"], "video_url": data.video_url or "https://processing.waiting.video", 
        "customer_phone": format_phone(data.customer_phone), "customer_description": data.customer_description, 
        "first_name": data.first_name, "last_name": data.last_name, "status": "pending"
    }
    res = await run_sync(supabase_admin.table("leads").insert(lead_data).execute)
    new_lead = res.data[0] if res.data else None

    # 1. IMMEDIATE: Notify CUSTOMER only
    background_tasks.add_task(send_customer_confirmation, data.customer_phone, tradie["business_name"])

    # 2. QUEUE: Process video (Tradie will be notified AFTER this finishes)
    video_queue = getattr(request.app.state, "video_queue", None)
    if new_lead and video_queue:
        work_item = f"LOCAL:{data.temp_video_id}" if data.temp_video_id else data.video_url
        await video_queue.put((new_lead["id"], work_item))
        logger.info(f"VIDEO_QUEUE: Lead {new_lead['id']} queued. Tradie notification delayed.")
    
    return {"status": "success"}

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
async def get_leads(slug: str, limit: int = 50, offset: int = 0, tradie: AuthenticatedTradie = Depends(get_current_user)):
    biz_res = await run_sync(supabase_admin.table("tradies").select("id, business_name, credits, email, phone_number").eq("slug", slug).single().execute)
    if not biz_res.data: raise HTTPException(status_code=404, detail="Not found.")
    if biz_res.data["id"] != tradie.id: raise HTTPException(status_code=403, detail="Unauthorized.")

    # --- EMAIL SYNC LOGIC ---
    # If the Auth email is different from the table email, it means a verified change happened.
    auth_email = tradie.user.email
    table_email = biz_res.data.get("email")
    if auth_email and auth_email != table_email:
        logger.info(f"SYNC: Updating table email to verified auth email: {auth_email}")
        await run_sync(supabase_admin.table("tradies").update({"email": auth_email}).eq("id", tradie.id).execute)

    leads_res = await run_sync(tradie.supabase.table("leads").select("*").order("created_at", desc=True).range(offset, offset + limit - 1).execute)
    return {
        "business_name": biz_res.data["business_name"], 
        "slug": slug,
        "credits": biz_res.data["credits"], 
        "email": auth_email,
        "limits_enabled": LEAD_LIMITS_ENABLED,
        "leads": leads_res.data
    }

@router.patch("/update-lead-status/{lead_id}")
async def update_lead_status(lead_id: str, data: dict, tradie: AuthenticatedTradie = Depends(get_current_user)):
    status = data.get("status")
    if not status:
        raise HTTPException(status_code=400, detail="Status required.")
    
    # Verify ownership before update
    lead_check = await run_sync(supabase_admin.table("leads").select("tradie_id").eq("id", lead_id).single().execute)
    if not lead_check.data:
        raise HTTPException(status_code=404, detail="Lead not found.")
    if lead_check.data["tradie_id"] != tradie.id:
        raise HTTPException(status_code=403, detail="Unauthorized.")
    
    res = await run_sync(supabase_admin.table("leads").update({"status": status}).eq("id", lead_id).execute)
    if not res.data:
        raise HTTPException(status_code=500, detail="Failed to update status.")
    
    return {"status": "success"}
