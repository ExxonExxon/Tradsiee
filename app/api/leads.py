from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from app.core.config import (
    supabase_admin, LEAD_LIMITS_ENABLED, twilio_client, TEFLON_SERVICE_SID, 
    FRONTEND_URL, logger
)
from app.core.dependencies import (
    run_sync, format_phone, get_current_user, LeadData, AuthenticatedTradie
)

router = APIRouter(tags=["Leads"])

@router.get("/get-leads/{slug}")
async def get_leads(slug: str, limit: int = 50, offset: int = 0, tradie: AuthenticatedTradie = Depends(get_current_user)):
    biz_res = await run_sync(supabase_admin.table("tradies").select("id, business_name, credits").eq("slug", slug).single().execute)
    if not biz_res.data: raise HTTPException(status_code=404, detail="Not found.")
    if biz_res.data["id"] != tradie.id: raise HTTPException(status_code=403, detail="Unauthorized.")

    leads_res = await run_sync(
        tradie.supabase.table("leads")
        .select("*")
        .order("created_at", desc=True).range(offset, offset + limit - 1).execute
    )
    return {
        "business_name": biz_res.data["business_name"], 
        "credits": biz_res.data["credits"], 
        "email": tradie.user.email,
        "leads": leads_res.data
    }

@router.post("/submit-lead-data/{slug}")
async def submit_lead_data(slug: str, data: LeadData, background_tasks: BackgroundTasks):
    tradie_res = await run_sync(supabase_admin.table("tradies").select("id, phone_number, business_name, credits").eq("slug", slug).single().execute)
    if not tradie_res.data: raise HTTPException(status_code=404, detail="Not found.")

    tradie = tradie_res.data
    
    if LEAD_LIMITS_ENABLED:
        if (tradie.get("credits") or 0) <= 0:
            raise HTTPException(status_code=402, detail="Lead limit reached. Please contact support.")

    lead_data = {
        "tradie_id": tradie["id"], "video_url": data.video_url, "customer_phone": format_phone(data.customer_phone),
        "customer_description": data.customer_description, "first_name": data.first_name, "last_name": data.last_name, "status": "pending"
    }
    
    await run_sync(supabase_admin.table("leads").insert(lead_data).execute)
    
    if LEAD_LIMITS_ENABLED:
        await run_sync(supabase_admin.table("tradies").update({"credits": tradie["credits"] - 1}).eq("id", tradie["id"]).execute)
    
    background_tasks.add_task(
        send_lead_notifications, 
        tradie["phone_number"], data.customer_phone, data.customer_description, tradie["business_name"]
    )
    return {"status": "success"}

@router.patch("/update-lead-status/{lead_id}")
async def update_lead_status(lead_id: str, data: dict, tradie: AuthenticatedTradie = Depends(get_current_user)):
    status = data.get("status")
    if not status: raise HTTPException(status_code=400, detail="Status required.")
    
    res = await run_sync(tradie.supabase.table("leads").update({"status": status}).eq("id", lead_id).execute)
    if not res.data:
        raise HTTPException(status_code=403, detail="Unauthorized or lead not found.")
    return {"status": "success"}

# --- Background Communications Worker ---

def send_lead_notifications(tradie_phone: str, customer_phone: str, description: str, biz_name: str):
    if not twilio_client: return
    t_phone, c_phone = format_phone(tradie_phone), format_phone(customer_phone)
    try:
        twilio_client.messages.create(messaging_service_sid=TEFLON_SERVICE_SID, to=t_phone,
            body=f"TRADSIEE: New lead! {c_phone}\nDesc: {description[:30]}...\nView: {FRONTEND_URL}/portal.html")
        twilio_client.messages.create(messaging_service_sid=TEFLON_SERVICE_SID, to=c_phone,
            body=f"Sent! {biz_name} has received your video.")
    except Exception as e: logger.error(f"NOTIFICATION_PIPELINE_FAILURE: {e}")
