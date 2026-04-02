# [AI_INFO] Tradsiee Engine v1.2 - Core FastAPI Backend.
# [AI_FLOW] This service acts as the central hub for:
#         1. Tradie registration and authentication (Supabase Auth).
#         2. Lead management and status updates (Supabase Database).
#         3. Video upload coordination (Cloudinary).
#         4. SMS notifications and verification (Twilio).
#         5. HTML template serving with dynamic injection.
# [AI_CONSTRAINT] Requires Supabase URL/Key, Twilio SID/Token, and Cloudinary Config in .env.
# [AI_SIDE_EFFECT] Modifies Supabase 'tradies' and 'leads' tables. Sends SMS via Twilio.

import logging
import os
import random
import re
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Dict, Any, Optional, List

import cloudinary
from dotenv import load_dotenv
from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    HTTPException,
    Request,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response, FileResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, constr, EmailStr
from supabase import Client, create_client
from twilio.rest import Client as TwilioClient

# [AI_INFO] Environment variables are loaded first. Critical for all service clients.
load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("tradsiee-engine")

# [AI_CONSTRAINT] FRONTEND_URL is used for password reset redirection.
API_BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5500")

# [AI_INFO] Twilio Client initialization.
# [AI_CONSTRAINT] TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN must be valid.
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TEFLON_SERVICE_SID = os.getenv("TWILIO_MESSAGING_SERVICE_SID")
twilio_client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN) if TWILIO_ACCOUNT_SID else None

# [AI_INFO] Supabase Client initialization.
# [AI_SIDE_EFFECT] supabase_admin uses the same key, but is intended for bypass-RLS operations where needed.
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
supabase_admin: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# [AI_INFO] Cloudinary configuration for widget asset management and video processing.
cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET"),
    secure=True,
)

# [AI_INFO] In-memory state for non-persistent or transient data.
# [AI_FLOW] pending_registrations stores profile data between /register and /verify-code.
# [AI_FLOW] verification_codes stores 6-digit SMS codes.
# [AI_FLOW] HTML_PAGES_CACHE stores pre-processed HTML with injected environment paths.
pending_registrations: Dict[str, Any] = {}
verification_codes: Dict[str, str] = {}
WIDGET_TEMPLATE_CACHE: Optional[str] = None
HTML_PAGES_CACHE: Dict[str, str] = {}

@asynccontextmanager
async def lifespan(app: FastAPI):
    # [AI_FLOW] Startup: Validates environment, processes and caches HTML templates.
    # [AI_CONSTRAINT] Fails gracefully but logs errors if templates or env vars are missing.
    global WIDGET_TEMPLATE_CACHE, HTML_PAGES_CACHE
    try:
        # 1. Required environment check
        required_env = [
            "CLOUDINARY_NAME", "CLOUDINARY_UPLOAD_PRESET",
            "SUPABASE_URL", "SUPABASE_KEY",
            "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_MESSAGING_SERVICE_SID"
        ]
        missing = [key for key in required_env if not os.getenv(key)]
        if missing:
            logger.error(f"CRITICAL: Missing environment variables: {', '.join(missing)}")

        cloud_name = os.getenv("CLOUDINARY_NAME", "")
        upload_preset = os.getenv("CLOUDINARY_UPLOAD_PRESET", "")
        
        # 2. Configurable Paths for UI
        ui_paths = {
            "[[PATH_LOGIN]]": os.getenv("PATH_LOGIN", "/login"),
            "[[PATH_SIGNUP]]": os.getenv("PATH_SIGNUP", "/signup"),
            "[[PATH_PORTAL]]": os.getenv("PATH_PORTAL", "/portal"),
            "[[PATH_UPDATE_PWD]]": os.getenv("PATH_UPDATE_PWD", "/update-password"),
            "[[PATH_PREVIEW]]": os.getenv("PATH_PREVIEW", "/preview"),
        }

        def process_html(filename: str, is_widget: bool = False) -> str:
            # [AI_FLOW] Reads HTML, minifies, and replaces placeholders with env-derived paths.
            path = os.path.join("web", "templates", filename)
            if not os.path.exists(path):
                logger.warning(f"File not found: {path}")
                return ""
            with open(path, "r") as f:
                content = f.read()
            
            # Minify slightly
            content = re.sub(r'>\s+<', '><', content)
            
            # Replace common placeholders
            for placeholder, value in ui_paths.items():
                content = content.replace(placeholder, value)
            
            if is_widget:
                content = content.replace('[[CLOUD_NAME]]', cloud_name)
                content = content.replace('[[UPLOAD_PRESET]]', upload_preset)
            
            return content

        # Cache widget
        WIDGET_TEMPLATE_CACHE = process_html("index.html", is_widget=True)
        
        # Cache other pages
        pages = {
            "login": "login.html",
            "signup": "signup.html",
            "portal": "portal.html",
            "update-password": "update-password.html",
            "preview": "widget-preview.html"
        }
        for key, filename in pages.items():
            HTML_PAGES_CACHE[key] = process_html(filename)
            
        logger.info(f"HTML pages successfully cached: {', '.join(HTML_PAGES_CACHE.keys())}")
    except Exception as e:
        logger.error(f"Startup error: {e}")
    yield

app = FastAPI(title="Tradsiee_Engine_v1.2", lifespan=lifespan)
security = HTTPBearer()

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    # [AI_INFO] Catch-all for unhandled exceptions to prevent leaking stack traces.
    logger.error(f"Global exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "An internal server error occurred. Our team has been notified."}
    )

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    # [AI_INFO] Standardizes HTTP error responses.
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail}
    )

# [AI_CONSTRAINT] Wildcard CORS enabled for widget embedding.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=".*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"]
)

# [AI_INFO] Data Schemas for validation.
class ForgotPasswordSchema(BaseModel):
    email: EmailStr

class ResetPasswordSchema(BaseModel):
    new_password: constr(min_length=8)

class LeadData(BaseModel):
    # [AI_FLOW] Shape of lead submission from the widget.
    video_url: str
    customer_phone: str
    customer_description: constr(max_length=1000) = "No description"
    first_name: str = ""
    last_name: str = ""

async def run_sync(func, *args, **kwargs):
    # [AI_INFO] Utility for running blocking Supabase/Twilio calls in threads.
    return await asyncio.to_thread(func, *args, **kwargs)

def format_phone(phone: str) -> str:
    # [AI_FLOW] Normalizes phone numbers to E.164 (defaulting to +61 Australia).
    clean_phone = re.sub(r"[^\d]", "", phone)
    if not phone.startswith("+"):
        return f"+61{clean_phone.lstrip('0')}"
    return f"+{clean_phone}"

async def generate_unique_slug(name: str) -> str:
    # [AI_FLOW] Creates a unique URL slug for the tradie business.
    # [AI_SIDE_EFFECT] Queries Supabase 'tradies' table to check for collisions.
    base = re.sub(r"[^a-zA-Z0-9]", "-", name.lower()).strip("-")
    for _ in range(5):
        suffix = random.randint(1000, 9999)
        candidate = f"{base}-{suffix}"
        res = await run_sync(supabase_admin.table("tradies").select("id").eq("slug", candidate).execute)
        if not res.data:
            return candidate
    return f"{base}-{random.getrandbits(32)}"

async def get_current_user(auth: HTTPAuthorizationCredentials = Depends(security)):
    # [AI_INFO] Dependency for routes requiring a logged-in user.
    # [AI_FLOW] Input: Bearer token (JWT). Output: Supabase User object.
    # [AI_CONSTRAINT] Requires valid Supabase JWT in Authorization header.
    if not auth or not auth.credentials or auth.credentials == "null":
        raise HTTPException(status_code=401, detail="Session required.")
    try:
        res = await run_sync(supabase.auth.get_user, auth.credentials)
        user = getattr(res, 'user', None) or (res.get('user') if isinstance(res, dict) else None)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid session.")
        return user
    except Exception as e:
        logger.error(f"Auth error: {e}")
        raise HTTPException(status_code=401, detail="Authentication failed.")

@app.post("/register")
async def register_tradie(data: dict):
    # [AI_INFO] Step 1 of Tradie Sign-up.
    # [AI_FLOW] Creates Supabase Auth user and stores profile in memory (pending_registrations).
    # [AI_SIDE_EFFECT] Triggers Supabase Auth sign_up.
    name, email, password, phone = data.get("business_name"), data.get("email"), data.get("password"), data.get("phone_number")
    if not all([name, email, password, phone]):
        raise HTTPException(status_code=400, detail="Missing data.")

    try:
        auth_res = await run_sync(supabase.auth.sign_up, {"email": email, "password": password})
        if not auth_res.user:
            raise HTTPException(status_code=400, detail="Auth failed.")

        new_slug = await generate_unique_slug(name)
        formatted_phone = format_phone(phone)

        profile_data = {
            "id": auth_res.user.id, "business_name": name, "email": email,
            "phone_number": formatted_phone, "slug": new_slug, "credits": 10, "is_verified": True
        }

        pending_registrations[formatted_phone] = profile_data
        return {"status": "success", "slug": new_slug}
    except Exception as e:
        logger.error(f"Reg error: {e}")
        raise HTTPException(status_code=400, detail="Registration failed.")

@app.post("/login")
async def login(data: dict):
    # [AI_INFO] Tradie Login.
    # [AI_FLOW] Input: email, password. Output: access_token, slug.
    # [AI_SIDE_EFFECT] Authenticates with Supabase. Fetches slug from 'tradies' table.
    email, password = data.get("email"), data.get("password")
    try:
        auth_res = await run_sync(supabase.auth.sign_in_with_password, {"email": email, "password": password})
        if not auth_res.user:
            raise HTTPException(status_code=401, detail="Auth failed.")

        res = await run_sync(supabase_admin.table("tradies").select("slug").eq("id", auth_res.user.id).execute)
        if not res.data:
            raise HTTPException(status_code=403, detail="Profile missing.")

        return {"slug": res.data[0]["slug"], "access_token": auth_res.session.access_token}
    except Exception as e:
        logger.error(f"Login error: {e}")
        raise HTTPException(status_code=401, detail="Invalid credentials.")

@app.post("/send-verification")
async def send_verification(data: dict):
    # [AI_INFO] Sends SMS verification code via Twilio.
    # [AI_FLOW] Stores code in verification_codes map.
    # [AI_SIDE_EFFECT] Sends SMS via Twilio Messaging Service.
    phone = data.get("phone")
    if not phone: raise HTTPException(status_code=400, detail="Phone required.")
    
    formatted_phone = format_phone(phone)
    code = str(random.randint(100000, 999999))
    verification_codes[formatted_phone] = code
    
    if twilio_client:
        try:
            await run_sync(twilio_client.messages.create, 
                messaging_service_sid=TEFLON_SERVICE_SID,
                body=f"Your Tradsiee code: {code}",
                to=formatted_phone
            )
        except Exception as e:
            logger.error(f"SMS failed: {e}")
            raise HTTPException(status_code=500, detail="SMS failed.")
    return {"status": "success"}

@app.post("/verify-code")
async def verify_code(data: dict):
    # [AI_INFO] Finalizes registration by verifying the SMS code.
    # [AI_FLOW] If valid, moves profile from memory to Supabase 'tradies' table.
    # [AI_SIDE_EFFECT] Deletes code from memory. Inserts row into 'tradies'.
    phone, code = data.get("phone"), data.get("code")
    formatted_phone = format_phone(phone)
    if verification_codes.get(formatted_phone) != code:
        raise HTTPException(status_code=400, detail="Invalid code.")
    
    del verification_codes[formatted_phone]
    if formatted_phone in pending_registrations:
        profile_data = pending_registrations.pop(formatted_phone)
        await run_sync(supabase_admin.table("tradies").insert(profile_data).execute)
            
    return {"status": "success"}

@app.get("/get-leads/{slug}")
async def get_leads(slug: str, limit: int = 50, offset: int = 0, user=Depends(get_current_user)):
    # [AI_INFO] Fetches leads for a specific tradie.
    # [AI_FLOW] Input: slug. Output: List of lead objects.
    # [AI_CONSTRAINT] Tradie ID must match the logged-in User ID.
    tradie_res = await run_sync(supabase_admin.table("tradies").select("id, business_name, credits").eq("slug", slug).single().execute)
    if not tradie_res.data: raise HTTPException(status_code=404, detail="Not found.")

    tradie = tradie_res.data
    if tradie["id"] != user.id: raise HTTPException(status_code=403, detail="Unauthorized.")

    leads_res = await run_sync(
        supabase_admin.table("leads")
        .select("*").eq("tradie_id", tradie["id"])
        .order("created_at", desc=True).range(offset, offset + limit - 1).execute
    )
    return {"business_name": tradie["business_name"], "credits": tradie["credits"], "leads": leads_res.data}

@app.post("/submit-lead-data/{slug}")
async def submit_lead_data(slug: str, data: LeadData, background_tasks: BackgroundTasks):
    # [AI_INFO] Endpoint for the widget to submit customer lead data.
    # [AI_FLOW] Input: LeadData (video_url, phone, desc). Output: status.
    # [AI_SIDE_EFFECT] Inserts lead into Supabase. Triggers background SMS notifications.
    tradie_res = await run_sync(supabase_admin.table("tradies").select("id, phone_number, business_name").eq("slug", slug).single().execute)
    if not tradie_res.data: raise HTTPException(status_code=404, detail="Not found.")

    tradie = tradie_res.data
    lead_data = {
        "tradie_id": tradie["id"], "video_url": data.video_url, "customer_phone": format_phone(data.customer_phone),
        "customer_description": data.customer_description, "first_name": data.first_name, "last_name": data.last_name, "status": "pending"
    }
    
    await run_sync(supabase_admin.table("leads").insert(lead_data).execute)
    
    background_tasks.add_task(
        send_lead_notifications, 
        tradie["phone_number"], data.customer_phone, data.customer_description, tradie["business_name"]
    )
    return {"status": "success"}

@app.get("/widget-bundle.js")
async def get_widget_bundle():
    # [AI_INFO] Serves the static widget JS bundle with aggressive caching.
    path = os.path.join("web", "static", "widget-bundle.js")
    if os.path.exists(path):
        return FileResponse(
            path, 
            media_type="application/javascript",
            headers={"Cache-Control": "public, max-age=31536000, immutable"}
        )
    return Response(content="// widget-bundle.js not found", media_type="application/javascript")

@app.get("/loader.js")
async def loader_js(request: Request, slug: str):
    # [AI_INFO] Dynamically generates the JS snippet for embedding the widget on 3rd party sites.
    # [AI_FLOW] Injects the slug and origin into a self-executing script.
    origin = f"{request.url.scheme}://{request.url.netloc}"
    js = f"""
(function() {{
    window.TRADSIEE_SLUG = "{slug}";
    var origin = "{origin}";
    
    ['https://api.cloudinary.com', 'https://fonts.googleapis.com'].forEach(url => {{
        var link = document.createElement('link');
        link.rel = 'preconnect';
        link.href = url;
        document.head.appendChild(link);
    }});

    var s = document.createElement('script');
    s.src = origin + '/widget-bundle.js';
    s.async = true;
    document.head.appendChild(s);
    
    var iframe = document.createElement('iframe');
    iframe.src = origin + '/widget/{slug}';
    iframe.style.cssText = 'border:none;width:100%;min-height:600px;background:transparent;';
    iframe.loading = 'lazy';
    
    var container = document.getElementById('tradsiee-widget-root');
    if (container) {{
        container.appendChild(iframe);
    }} else {{
        document.currentScript ? document.currentScript.parentNode.insertBefore(iframe, document.currentScript) : document.body.appendChild(iframe);
    }}
}})();
"""
    return Response(
        content=js, 
        media_type="application/javascript",
        headers={"Cache-Control": "public, max-age=3600"}
    )

@app.get("/widget/{slug}", response_class=HTMLResponse)
async def get_widget_ui(slug: str):
    # [AI_INFO] Serves the widget UI (index.html) with slug replacement.
    content = WIDGET_TEMPLATE_CACHE or "Template missing."
    c_name = os.getenv("CLOUDINARY_NAME", "MISSING")
    u_preset = os.getenv("CLOUDINARY_UPLOAD_PRESET", "MISSING")
    logger.info(f"Serving widget for {slug} | Cloud: {c_name} | Preset: {u_preset}")
    return content.replace('[[SLUG_PLACEHOLDER]]', slug)

@app.patch("/update-lead-status/{lead_id}")
async def update_lead_status(lead_id: str, data: dict, user=Depends(get_current_user)):
    # [AI_INFO] Allows tradies to update lead status (e.g., 'contacted', 'archived').
    # [AI_SIDE_EFFECT] Updates 'leads' table in Supabase.
    # [AI_CONSTRAINT] Lead must belong to the logged-in tradie.
    status = data.get("status")
    if not status: raise HTTPException(status_code=400, detail="Status required.")
    
    lead_res = await run_sync(supabase_admin.table("leads").select("tradie_id").eq("id", lead_id).single().execute)
    if not lead_res.data: raise HTTPException(status_code=404, detail="Lead not found.")
    if lead_res.data["tradie_id"] != user.id: raise HTTPException(status_code=403, detail="Unauthorized.")
    
    await run_sync(supabase_admin.table("leads").update({"status": status}).eq("id", lead_id).execute)
    return {"status": "success"}

@app.post("/forgot-password")
async def forgot_password(data: ForgotPasswordSchema):
    # [AI_INFO] Triggers Supabase password reset email.
    # [AI_SIDE_EFFECT] Sends email via Supabase Auth.
    try:
        path_update = os.getenv("PATH_UPDATE_PWD", "/update-password")
        await run_sync(supabase.auth.reset_password_for_email, data.email, {"redirect_to": f"{FRONTEND_URL}{path_update}"})
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Forgot pwd error: {e}")
        raise HTTPException(status_code=400, detail="Failed to send reset link.")

@app.get("/", response_class=HTMLResponse)
async def serve_home():
    # [AI_INFO] Serves cached login page as home.
    return HTML_PAGES_CACHE.get("login", "Page missing.")

@app.get(os.getenv("PATH_LOGIN", "/login"), response_class=HTMLResponse)
async def serve_login():
    return HTML_PAGES_CACHE.get("login", "Page missing.")

@app.get(os.getenv("PATH_SIGNUP", "/signup"), response_class=HTMLResponse)
async def serve_signup():
    return HTML_PAGES_CACHE.get("signup", "Page missing.")

@app.get(os.getenv("PATH_PORTAL", "/portal"), response_class=HTMLResponse)
async def serve_portal():
    return HTML_PAGES_CACHE.get("portal", "Page missing.")

@app.get(os.getenv("PATH_UPDATE_PWD", "/update-password"), response_class=HTMLResponse)
async def serve_update_password():
    return HTML_PAGES_CACHE.get("update-password", "Page missing.")

@app.get(os.getenv("PATH_PREVIEW", "/preview"), response_class=HTMLResponse)
async def serve_preview():
    return HTML_PAGES_CACHE.get("preview", "Page missing.")

@app.post("/update-password")
async def update_password(data: ResetPasswordSchema, user=Depends(get_current_user)):
    # [AI_INFO] Updates password for the current user.
    # [AI_SIDE_EFFECT] Modifies Supabase Auth user record.
    try:
        await run_sync(supabase.auth.update_user, {"password": data.new_password})
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Update pwd error: {e}")
        raise HTTPException(status_code=400, detail="Failed to update password.")

@app.post("/send-delete-code")
async def send_delete_code(user=Depends(get_current_user)):
    # [AI_INFO] Sends a deletion confirmation code via SMS.
    res = await run_sync(supabase_admin.table("tradies").select("phone_number").eq("id", user.id).single().execute)
    if not res.data: raise HTTPException(status_code=404, detail="Profile not found.")
    
    phone = res.data["phone_number"]
    code = str(random.randint(100000, 999999))
    verification_codes[f"DEL_{user.id}"] = code
    
    if twilio_client:
        try:
            await run_sync(twilio_client.messages.create, 
                messaging_service_sid=TEFLON_SERVICE_SID,
                body=f"Your Tradsiee account deletion code: {code}. If you didn't request this, ignore it.",
                to=phone
            )
        except Exception as e:
            logger.error(f"SMS failed: {e}")
            raise HTTPException(status_code=500, detail="SMS failed.")
    return {"status": "success"}

@app.delete("/delete-account/{slug}")
async def delete_account(slug: str, code: str, user=Depends(get_current_user)):
    # [AI_INFO] Deletes tradie account and auth record.
    # [AI_SIDE_EFFECT] Deletes from 'tradies' table and Supabase Auth.
    if verification_codes.get(f"DEL_{user.id}") != code:
        raise HTTPException(status_code=400, detail="Invalid code.")
    
    res = await run_sync(supabase_admin.table("tradies").select("id, slug").eq("id", user.id).single().execute)
    if not res.data or res.data["slug"] != slug:
        raise HTTPException(status_code=404, detail="Slug mismatch or profile not found.")
    
    try:
        await run_sync(supabase_admin.table("tradies").delete().eq("id", user.id).execute)
        await run_sync(supabase_admin.auth.admin.delete_user, user.id)
        if f"DEL_{user.id}" in verification_codes:
            del verification_codes[f"DEL_{user.id}"]
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Delete error: {e}")
        raise HTTPException(status_code=500, detail="Deletion failed.")

@app.get("/health")
def health(): return {"status": "online", "version": "1.2.0"}

def send_lead_notifications(tradie_phone: str, customer_phone: str, description: str, biz_name: str):
    # [AI_INFO] Background task to notify both tradie and customer.
    # [AI_FLOW] Sends SMS to tradie with lead info and to customer as confirmation.
    # [AI_SIDE_EFFECT] Two SMS messages sent via Twilio.
    if not twilio_client: return
    t_phone, c_phone = format_phone(tradie_phone), format_phone(customer_phone)
    try:
        twilio_client.messages.create(messaging_service_sid=TEFLON_SERVICE_SID, to=t_phone,
            body=f"TRADSIEE: New lead! {c_phone}\nDesc: {description[:30]}...\nView: {FRONTEND_URL}/portal.html")
        twilio_client.messages.create(messaging_service_sid=TEFLON_SERVICE_SID, to=c_phone,
            body=f"Sent! {biz_name} has received your video.")
    except Exception as e: logger.error(f"Notification error: {e}")
