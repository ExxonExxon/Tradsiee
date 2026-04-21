# [ENGINE_SPECIFICATION]
# Tradsiee Engine v1.2 - Core FastAPI Backend.
# This service acts as the central orchestration hub for the Tradsiee platform, handling 
# multi-tenant tradie logic, lead ingestion, and secure media coordination.

# [SYSTEM_ARCHITECTURE_FLOW]
# 1. Identity & Access: Manages Tradie registration, SMS-based verification, and JWT-secured login via Supabase.
# 2. Lead Lifecycle: Ingests lead data from client-side widgets, stores it in Supabase, and manages status transitions.
# 3. Media Coordination: Facilitates secure video uploads by providing the necessary configuration for Cloudinary.
# 4. Outbound Communications: Dispatches real-time SMS alerts to Tradies and confirmation messages to customers via Twilio.
# 5. Template Engine: Dynamically renders, minifies, and caches HTML pages with environment-specific variable injection.

# [SYSTEM_CONSTRAINTS]
# Execution requires a valid .env configuration containing:
# - Supabase: Project URL and Secret Key (Service Role for admin tasks).
# - Twilio: Account SID, Auth Token, and Messaging Service SID.
# - Cloudinary: Cloud Name, API Key, and Secret for media signing.

# [STATE_MUTATION_DETAILS]
# Operations in this service directly modify the 'tradies' and 'leads' tables in the Supabase PostgreSQL database.
# SMS dispatch via Twilio represents an external side-effect that consumes credits and impacts external system state.

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

# [BOOTSTRAP_SEQUENCE]
# Load environment variables into the process before initializing any service clients.
load_dotenv()

# [OBSERVABILITY_SETUP]
# Standardized logging configuration to ensure traceability of system events across threads.
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("tradsiee-engine")

# [ENVIRONMENT_VARS_RESOLUTION]
# API_BASE_URL: The external-facing endpoint of this backend (used for loader scripts).
# FRONTEND_URL: The root URL of the dashboard/portal (used for password reset redirection).
# LEAD_LIMITS_ENABLED: Master toggle for enforcing the credit system.
API_BASE_URL = os.getenv("API_BASE_URL", "https://tradsiee.com")
FRONTEND_URL = os.getenv("FRONTEND_URL", "https://tradsiee.com")
LEAD_LIMITS_ENABLED = os.getenv("LEAD_LIMITS_ENABLED", "false").lower() == "true"

# [CLIENT_INITIALIZATION: TWILIO]
# Establishes the connection to the Twilio REST API for SMS orchestration.
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TEFLON_SERVICE_SID = os.getenv("TWILIO_MESSAGING_SERVICE_SID")
twilio_client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN) if TWILIO_ACCOUNT_SID else None

# [CLIENT_INITIALIZATION: SUPABASE]
# supabase_admin: Uses SERVICE_ROLE key. Bypasses RLS. Use ONLY for registration and internal logic.
# supabase_anon: Uses ANON key. Respects RLS. Use for user-facing data requests.
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_KEY") # This should be your service_role key
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", SUPABASE_SERVICE_KEY) # Fallback to service if not set

supabase_admin: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

def get_supabase_user_client(token: str) -> Client:
    # [RLS_ORCHESTRATION]
    # Creates a Supabase client scoped to the specific user's JWT.
    # This ensures that Row Level Security (RLS) is enforced at the database level.
    client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
    client.postgrest.auth(token)
    return client

# [CLIENT_INITIALIZATION: CLOUDINARY]
# Configures the multimedia SDK for secure asset management.
cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET"),
    secure=True,
)

# [TRANSIENT_MEMORY_STATE]
# We've moved registrations to the DB, but we keep verification_codes in memory 
# for speed. Since we have Identity Reclamation, a lost code on restart just 
# requires the user to click "Resend."
verification_codes: Dict[str, str] = {}

# [SECURITY_ORCHESTRATION: RATE_LIMITING]
# Tracks IP-based activity to prevent Twilio credit exhaustion and brute-force attacks.
# sms_last_sent: Maps IP to timestamp of last SMS dispatch.
# registration_attempts: Maps IP to count of signups in the current window.
sms_last_sent: Dict[str, datetime] = {}
registration_attempts: Dict[str, List[datetime]] = {}

def is_rate_limited(ip: str, limit_type: str = "sms") -> bool:
    now = datetime.now()
    if limit_type == "sms":
        last_sent = sms_last_sent.get(ip)
        if last_sent and (now - last_sent).total_seconds() < 60:
            return True
        sms_last_sent[ip] = now
    
    elif limit_type == "register":
        attempts = registration_attempts.get(ip, [])
        # Filter for attempts in the last hour
        valid_attempts = [t for t in attempts if (now - t).total_seconds() < 3600]
        if len(valid_attempts) >= 5: # Max 5 signups per hour per IP
            return True
        valid_attempts.append(now)
        registration_attempts[ip] = valid_attempts
        
    return False

WIDGET_TEMPLATE_CACHE: Optional[str] = None
HTML_PAGES_CACHE: Dict[str, str] = {}

@asynccontextmanager
async def lifespan(app: FastAPI):
    # [STARTUP_PROCEDURES]
    # Executed on application boot. Validates the environment and prepares the template cache.
    # This prevents expensive disk I/O operations during active request handling.
    global WIDGET_TEMPLATE_CACHE, HTML_PAGES_CACHE
    try:
        # 1. Integrity Check: Verify all required environmental secrets are loaded.
        required_env = [
            "CLOUDINARY_NAME", "CLOUDINARY_UPLOAD_PRESET",
            "SUPABASE_URL", "SUPABASE_KEY",
            "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_MESSAGING_SERVICE_SID"
        ]
        missing = [key for key in required_env if not os.getenv(key)]
        if missing:
            logger.error(f"CRITICAL_FAILURE: Missing required environment variables: {', '.join(missing)}")

        cloud_name = os.getenv("CLOUDINARY_NAME", "")
        upload_preset = os.getenv("CLOUDINARY_UPLOAD_PRESET", "")
        
        # 2. Path Resolution: Maps internal placeholder strings to their configured public routes.
        ui_paths = {
            "[[PATH_LOGIN]]": os.getenv("PATH_LOGIN", "/login"),
            "[[PATH_SIGNUP]]": os.getenv("PATH_SIGNUP", "/signup"),
            "[[PATH_PORTAL]]": os.getenv("PATH_PORTAL", "/portal"),
            "[[PATH_UPDATE_PWD]]": os.getenv("PATH_UPDATE_PWD", "/update-password"),
            "[[PATH_PREVIEW]]": os.getenv("PATH_PREVIEW", "/preview"),
        }

        def process_html(filename: str, is_widget: bool = False) -> str:
            # [TEMPLATE_PROCESSING_LOGIC]
            # Performs a surgical read, light minification, and placeholder replacement 
            # to prepare static HTML templates for dynamic serving.
            path = os.path.join("web", "templates", filename)
            if not os.path.exists(path):
                logger.warning(f"ASSET_NOT_FOUND: Template file {path} is missing from the filesystem.")
                return ""
            with open(path, "r") as f:
                content = f.read()
            
            # Minification Strategy: Remove redundant whitespace between HTML elements.
            content = re.sub(r'>\s+<', '><', content)
            
            # Injection: Replace navigation placeholders with actual environment-defined paths.
            for placeholder, value in ui_paths.items():
                content = content.replace(placeholder, value)
            
            # Widget Injection: Specifically inject Cloudinary credentials for direct-to-cloud uploads.
            if is_widget:
                content = content.replace('[[CLOUD_NAME]]', cloud_name)
                content = content.replace('[[UPLOAD_PRESET]]', upload_preset)
            
            return content

        # Warm the cache for the widget UI (index.html).
        WIDGET_TEMPLATE_CACHE = process_html("index.html", is_widget=True)
        
        # Warm the cache for all administrative and dashboard pages.
        pages = {
            "login": "login.html",
            "signup": "signup.html",
            "portal": "portal.html",
            "update-password": "update-password.html",
            "preview": "widget-preview.html",
            "admin": "admin.html",
            "verified": "verified.html"
        }
        for key, filename in pages.items():
            HTML_PAGES_CACHE[key] = process_html(filename)
            
        logger.info(f"TEMPLATE_CACHE_LOADED: Caching complete for keys: {', '.join(HTML_PAGES_CACHE.keys())}")
    except Exception as e:
        logger.error(f"STARTUP_EXCEPTION: Initialization failed with error: {e}")
    yield

app = FastAPI(title="Tradsiee_Engine_v1.2", lifespan=lifespan)
security = HTTPBearer()

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    # [GLOBAL_ERROR_TRAP]
    # Prevents sensitive system state (stack traces) from leaking to the client 
    # while ensuring all failures are logged for internal auditing.
    logger.error(f"UNHANDLED_SYSTEM_EXCEPTION: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "An internal server error occurred. Our team has been notified."}
    )

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    # [CONTROLLED_ERROR_NORMALIZATION]
    # Ensures all expected HTTP errors follow a consistent response structure.
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail}
    )

# [CORS_MIDDLEWARE_CONFIGURATION]
# Implements permissive Cross-Origin Resource Sharing (CORS) to allow the Tradsiee widget 
# to be embedded and functional on any 3rd party website.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=".*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"]
)

# [DATA_VALIDATION_MODELS]
# Pydantic schemas for enforcing structural integrity on all inbound API payloads.
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
    # [LEAD_PAYLOAD_STRUCTURE]
    # Defines the data expected from the lead-generation widget.
    video_url: str
    customer_phone: str
    customer_description: constr(max_length=1000) = "No description"
    first_name: str = ""
    last_name: str = ""

async def run_sync(func, *args, **kwargs):
    # [ASYNC_SYNC_BRIDGE]
    # Offloads blocking SDK calls (Supabase, Twilio) to an external thread pool 
    # to maintain high concurrency in the main event loop.
    return await asyncio.to_thread(func, *args, **kwargs)

def format_phone(phone: str) -> str:
    # [PHONE_NORMALIZATION_LOGIC]
    # Remove all non-digit characters except the leading plus
    clean = re.sub(r"[^\d+]", "", phone)
    if not clean.startswith("+"):
        # Default to Australia if no country code provided
        return f"+61{clean.lstrip('0')}"
    return clean

async def generate_unique_slug(name: str) -> str:
    # [IDENTITY_SLUG_GENERATION]
    # Creates a unique, URL-safe business identifier.
    # Recursively checks for collisions in the 'tradies' table to ensure global uniqueness.
    base = re.sub(r"[^a-zA-Z0-9]", "-", name.lower()).strip("-")
    for _ in range(5):
        suffix = random.randint(1000, 9999)
        candidate = f"{base}-{suffix}"
        res = await run_sync(supabase_admin.table("tradies").select("id").eq("slug", candidate).execute)
        if not res.data:
            return candidate
    return f"{base}-{random.getrandbits(32)}"

class AuthenticatedTradie:
    def __init__(self, user: Any, client: Client):
        self.user = user
        self.supabase = client
        self.id = user.id

async def get_current_user(auth: HTTPAuthorizationCredentials = Depends(security)) -> AuthenticatedTradie:
    # [AUTHENTICATION_GUARD]
    if not auth or not auth.credentials or auth.credentials == "null":
        raise HTTPException(status_code=401, detail="Session required.")
    try:
        # 1. Standard session check via Admin client (for speed/validation)
        res = await run_sync(supabase_admin.auth.get_user, auth.credentials)
        user = getattr(res, 'user', None) or (res.get('user') if isinstance(res, dict) else None)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid session.")
        
        if not getattr(user, 'email_confirmed_at', None):
            raise HTTPException(status_code=403, detail="EMAIL_NOT_VERIFIED")
            
        # 2. Return the user along with a client scoped to their JWT
        return AuthenticatedTradie(user, get_supabase_user_client(auth.credentials))
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"AUTH_VERIFICATION_FAILURE: {e}")
        raise HTTPException(status_code=401, detail="Authentication failed.")

@app.post("/resend-confirmation")
async def resend_confirmation(data: dict):
    # [IDENTITY_RECOVERY]
    # Allows users to trigger a new confirmation email if the original was lost.
    email = data.get("email")
    if not email: raise HTTPException(status_code=400, detail="Email required.")
    try:
        await run_sync(supabase_admin.auth.resend, {"type": "signup", "email": email})
        return {"status": "success"}
    except Exception as e:
        logger.error(f"RESEND_FAILURE: {e}")
        raise HTTPException(status_code=400, detail="Failed to resend email.")

@app.post("/register")
async def register_tradie(data: dict, request: Request):
    # [REGISTRATION_PIPELINE: STEP 1]
    # Collects profile data and validates identity uniqueness.
    client_ip = request.client.host
    if is_rate_limited(client_ip, "register"):
        logger.warning(f"RATE_LIMIT_EXCEEDED: register from ip={client_ip}")
        raise HTTPException(status_code=429, detail="Too many registration attempts. Please try again in an hour.")

    name, email, password, phone = data.get("business_name"), data.get("email"), data.get("password"), data.get("phone_number")
    if not all([name, email, password, phone]):
        raise HTTPException(status_code=400, detail="Missing data.")

    try:
        formatted_phone = format_phone(phone)
        
        # 1. Check if the Email is already in use
        email_check = await run_sync(supabase_admin.table("tradies").select("id").eq("email", email).execute)
        if email_check.data:
            raise HTTPException(status_code=400, detail="This email is already registered. Please sign in.")

        # 2. Check if the Phone is already in use
        phone_check = await run_sync(supabase_admin.table("tradies").select("id").eq("phone_number", formatted_phone).execute)
        if phone_check.data:
            existing_user_id = phone_check.data[0]["id"]
            # Check if that user is already verified in Supabase Auth
            try:
                auth_user = await run_sync(supabase_admin.auth.admin.get_user_by_id, existing_user_id)
                if auth_user.user and auth_user.user.email_confirmed_at:
                     raise HTTPException(status_code=400, detail="This phone number is already verified to another account.")
            except:
                # If auth lookup fails, assume unverified or orphaned profile
                pass
            
            # If we reach here, the phone belongs to an UNVERIFIED account.
            # We allow the new registration to proceed.
            logger.info(f"IDENTITY_RECLAMATION_START: Phone {formatted_phone} is claiming from unverified email.")

        # [STAGING]
        # We persist the signup data to Supabase so it survives server reloads.
        new_slug = await generate_unique_slug(name)
        staged_data = {
            "business_name": name, "email": email, "password": password,
            "phone_number": formatted_phone, "slug": new_slug, "credits": 10
        }

        # Upsert into staged table (wipes any old attempt for this phone)
        await run_sync(supabase_admin.table("staged_registrations").upsert(staged_data, on_conflict="phone_number").execute)
        return {"status": "success", "slug": new_slug}
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"REGISTRATION_FAILURE: {e}")
        raise HTTPException(status_code=400, detail="Registration failed.")

@app.post("/login")
async def login(data: dict):
    # [AUTHENTICATION_PIPELINE]
    email, password = data.get("email"), data.get("password")
    try:
        # If Supabase Auth is set to 'Confirm Email', this call will succeed 
        # but the session might be limited, or it might throw 403.
        auth_res = await run_sync(supabase_admin.auth.sign_in_with_password, {"email": email, "password": password})
        
        # Get business slug
        res = await run_sync(supabase_admin.table("tradies").select("slug").eq("id", auth_res.user.id).execute)
        if not res.data:
            raise HTTPException(status_code=403, detail="Profile missing.")

        return {"slug": res.data[0]["slug"], "access_token": auth_res.session.access_token}
    except Exception as e:
        msg = str(e)
        if "Email not confirmed" in msg:
            # We still need the slug to redirect to the portal banner.
            # We'll search for the user by email in our tradies table.
            tradie = await run_sync(supabase_admin.table("tradies").select("slug").eq("email", email).single().execute)
            if tradie.data:
                # Note: We can't return a token if Supabase blocked the login,
                # but we can return the slug and a special status.
                raise HTTPException(status_code=403, detail="EMAIL_NOT_VERIFIED")
        
        logger.error(f"LOGIN_FAILURE: {e}")
        raise HTTPException(status_code=401, detail="Invalid credentials.")

@app.post("/send-verification")
async def send_verification(data: dict, request: Request):
    # [MFA_CHALLENGE_DISPATCH]
    client_ip = request.client.host
    if is_rate_limited(client_ip, "sms"):
        logger.warning(f"RATE_LIMIT_EXCEEDED: sms from ip={client_ip}")
        raise HTTPException(status_code=429, detail="Please wait 60 seconds before requesting another code.")

    phone = data.get("phone")
    if not phone: raise HTTPException(status_code=400, detail="Phone required.")
    
    formatted_phone = format_phone(phone)
    code = str(random.randint(100000, 999999))
    verification_codes[formatted_phone] = code
    
    # [DEBUG_LOG] This allows developers to see the code in the terminal
    logger.info(f"VERIFICATION_CODE_GENERATED: phone={formatted_phone} | code={code}")
    
    if twilio_client:
        try:
            await run_sync(twilio_client.messages.create, 
                messaging_service_sid=TEFLON_SERVICE_SID,
                body=f"Your Tradsiee code: {code}",
                to=formatted_phone
            )
        except Exception as e:
            logger.error(f"SMS_DISPATCH_FAILURE: {e}")
            raise HTTPException(status_code=500, detail="SMS failed.")
    return {"status": "success"}

def get_base_url(request: Request) -> str:
    # [ENVIRONMENT_SENSITIVE_RESOLUTION]
    # Dynamically detects the base URL (localhost vs production) based on the request origin.
    # This ensures email redirects (Supabase) always point back to the correct environment.
    origin = request.headers.get("origin")
    host = request.headers.get("host", "")
    
    if (origin and "localhost" in origin) or "localhost" in host or "127.0.0.1" in host:
        return "http://localhost:8000"
    return FRONTEND_URL # Defaults to https://tradsiee.com from .env

@app.post("/verify-code")
async def verify_code(data: dict, request: Request):
    # [REGISTRATION_PIPELINE: STEP 2]
    # Confirms the MFA code. On success, the Supabase Auth record is created 
    # (triggering the confirmation email) and the profile is persisted.
    phone, code = data.get("phone"), data.get("code")
    formatted_phone = format_phone(phone)
    
    stored_code = verification_codes.get(formatted_phone)
    if not stored_code or stored_code != code:
        raise HTTPException(status_code=400, detail="Invalid or expired verification code.")
    
    del verification_codes[formatted_phone]
    
    # [PERSISTENT_RECOVERY]
    staged_res = await run_sync(supabase_admin.table("staged_registrations").select("*").eq("phone_number", formatted_phone).single().execute)
    if not staged_res.data:
        raise HTTPException(status_code=400, detail="Signup session expired.")
    
    profile_data = staged_res.data
    password = profile_data.pop("password")
    
    base_url = get_base_url(request)
    
    try:
        # [RECLAMATION_LOGIC]
        existing = await run_sync(supabase_admin.table("tradies").select("id").eq("phone_number", formatted_phone).execute)
        if existing.data:
            old_id = existing.data[0]["id"]
            logger.warning(f"IDENTITY_RECLAMATION_EXECUTING: Wiping stale account {old_id}")
            await run_sync(supabase_admin.table("tradies").delete().eq("id", old_id).execute)
            try: await run_sync(supabase_admin.auth.admin.delete_user, old_id)
            except: pass

        # [PROVISIONING]
        # Use dynamic base_url to ensure redirects work in both local and prod
        auth_res = await run_sync(supabase.auth.sign_up, {
            "email": profile_data["email"], 
            "password": password,
            "options": { "redirect_to": f"{base_url}/verified" }
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
            
    return {"status": "success"}

@app.get("/get-leads/{slug}")
async def get_leads(slug: str, limit: int = 50, offset: int = 0, tradie: AuthenticatedTradie = Depends(get_current_user)):
    # [PIPELINE_ORCHESTRATION]
    # Retrieves leads using the scoped client, enforcing RLS at the DB level.
    # Note: Slug check is still performed via admin for speed/validation.
    biz_res = await run_sync(supabase_admin.table("tradies").select("id, business_name, credits").eq("slug", slug).single().execute)
    if not biz_res.data: raise HTTPException(status_code=404, detail="Not found.")
    if biz_res.data["id"] != tradie.id: raise HTTPException(status_code=403, detail="Unauthorized.")

    # Enforcement: Scoped client only sees rows matching tradie_id = tradie.id
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

@app.post("/submit-lead-data/{slug}")
async def submit_lead_data(slug: str, data: LeadData, background_tasks: BackgroundTasks):
    """
    Ingests new lead data from the client-side widget.
    
    1. Resolves the target business identity.
    2. Checks/Enforces lead limits if the master toggle is ENABLED.
    3. Normalizes and persists the lead metadata.
    4. Offloads SMS notifications to background tasks.
    """
    tradie_res = await run_sync(supabase_admin.table("tradies").select("id, phone_number, business_name, credits").eq("slug", slug).single().execute)
    if not tradie_res.data: raise HTTPException(status_code=404, detail="Not found.")

    tradie = tradie_res.data
    
    # [CREDIT_VALIDATION]
    # Only enforce limits if the Global Master Toggle is turned ON.
    if LEAD_LIMITS_ENABLED:
        if (tradie.get("credits") or 0) <= 0:
            raise HTTPException(status_code=402, detail="Lead limit reached. Please contact support.")

    lead_data = {
        "tradie_id": tradie["id"], "video_url": data.video_url, "customer_phone": format_phone(data.customer_phone),
        "customer_description": data.customer_description, "first_name": data.first_name, "last_name": data.last_name, "status": "pending"
    }
    
    # 1. Store lead
    await run_sync(supabase_admin.table("leads").insert(lead_data).execute)
    
    # 2. Decrement credit ONLY if limits are enabled
    if LEAD_LIMITS_ENABLED:
        await run_sync(supabase_admin.table("tradies").update({"credits": tradie["credits"] - 1}).eq("id", tradie["id"]).execute)
    
    # Asynchronous Notification
    background_tasks.add_task(
        send_lead_notifications, 
        tradie["phone_number"], data.customer_phone, data.customer_description, tradie["business_name"]
    )
    return {"status": "success"}

@app.get("/config.js")
async def get_config_js():
    # [DYNAMIC_CONFIG_GENERATION]
    # Injects server-side environment flags into the client-side configuration.
    
    js_content = f"""
/**
 * Tradsiee Global Configuration (Dynamically Generated)
 */
const TRADSIEE_ENV = {{
    isLocal: window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1',
    leadLimitsEnabled: {str(LEAD_LIMITS_ENABLED).lower()},
    get API_BASE() {{
        return this.isLocal ? "http://localhost:8000" : "https://tradsiee.com";
    }}
}};
"""
    return Response(
        content=js_content, 
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"}
    )

@app.get("/widget-bundle.js")
async def get_widget_bundle():
    # [STATIC_LOGIC_DISPATCH]
    # Serves the compiled widget bundle with aggressive caching headers.
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
    # [WIDGET_INJECTION_LOGIC]
    # Generates a dynamic JS loader used by 3rd party sites to embed the Tradsiee iframe.
    # Injects the specific business 'slug' to ensure the correct context is loaded.
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
    # [WIDGET_HTML_PROVIDER]
    # Serves the widget's internal UI, loading the template from memory cache 
    # and injecting the active business slug for data routing.
    content = WIDGET_TEMPLATE_CACHE or "Template missing."
    c_name = os.getenv("CLOUDINARY_NAME", "MISSING")
    u_preset = os.getenv("CLOUDINARY_UPLOAD_PRESET", "MISSING")
    logger.info(f"WIDGET_SERVED: slug={slug} | cloudinary={c_name}")
    return content.replace('[[SLUG_PLACE_HOLDER]]', slug)

@app.patch("/update-lead-status/{lead_id}")
async def update_lead_status(lead_id: str, data: dict, tradie: AuthenticatedTradie = Depends(get_current_user)):
    # [PIPELINE_STATE_TRANSITION]
    status = data.get("status")
    if not status: raise HTTPException(status_code=400, detail="Status required.")
    
    # DB-level check: RLS will block the update if lead_id does not belong to auth.uid()
    # The scoped client ensures we can only touch our own leads.
    res = await run_sync(tradie.supabase.table("leads").update({"status": status}).eq("id", lead_id).execute)
    if not res.data:
        raise HTTPException(status_code=403, detail="Unauthorized or lead not found.")
    return {"status": "success"}

@app.post("/forgot-password")
async def forgot_password(data: ForgotPasswordSchema, request: Request):
    # [PASSWORD_RECOVERY_GATEWAY]
    # Initiates a password reset via Supabase Auth.
    # Uses dynamic base_url to ensure the reset link works in the current environment.
    try:
        base_url = get_base_url(request)
        path_update = os.getenv("PATH_UPDATE_PWD", "/update-password")
        await run_sync(supabase_admin.auth.reset_password_for_email, data.email, {"redirect_to": f"{base_url}{path_update}"})
        return {"status": "success"}
    except Exception as e:
        logger.error(f"PASSWORD_RESET_FAILURE: {e}")
        raise HTTPException(status_code=400, detail="Failed to send reset link.")

@app.get("/", response_class=HTMLResponse)
async def serve_home():
    # [PAGE_ROUTER: HOME]
    # Serves the primary landing/login experience.
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

@app.get("/verified", response_class=HTMLResponse)
async def serve_verified():
    return HTML_PAGES_CACHE.get("verified", "Page missing.")

@app.post("/update-password")
async def update_password(data: ResetPasswordSchema, auth: HTTPAuthorizationCredentials = Depends(security)):
    # [ACCOUNT_STATE_MUTATION]
    # Validates the recovery token and updates the user's password.
    try:
        # 1. Verify Identity: Use the admin client to validate the provided JWT.
        # This is the standard procedure for verifying Supabase tokens in a custom backend.
        res = await run_sync(supabase_admin.auth.get_user, auth.credentials)
        user = getattr(res, 'user', None) or (res.get('user') if isinstance(res, dict) else None)
        
        if not user:
            logger.warning(f"PASSWORD_ROTATION_ATTEMPT_REJECTED: Invalid or expired token.")
            raise HTTPException(status_code=401, detail="Invalid session.")
        
        # 2. Perform Atomic Update: Use administrative privileges to rotate the password.
        await run_sync(supabase_admin.auth.admin.update_user_by_id, user.id, {"password": data.new_password})
        
        # 3. Retrieve Workspace Context: Required for seamless frontend redirection.
        tradie_res = await run_sync(supabase_admin.table("tradies").select("slug").eq("id", user.id).single().execute)
        slug = tradie_res.data.get("slug") if tradie_res.data else None
        
        return {"status": "success", "slug": slug}
    except HTTPException: raise
    except Exception as e:
        logger.error(f"PASSWORD_ROTATION_FAILURE: {e}")
        raise HTTPException(status_code=400, detail="Failed to update password.")

@app.patch("/update-profile")
async def update_profile(data: UpdateProfileSchema, tradie: AuthenticatedTradie = Depends(get_current_user)):
    # [PROFILE_STATE_MUTATION]
    # Updates the business's public identity and branding.
    updates = {}
    if data.business_name: updates["business_name"] = data.business_name
    if data.slug:
        # Validate slug format
        if not re.match(r'^[a-z0-9-]+$', data.slug.lower()):
            raise HTTPException(status_code=400, detail="Slug can only contain letters, numbers, and hyphens.")
        
        # Check uniqueness
        check = await run_sync(supabase_admin.table("tradies").select("id").eq("slug", data.slug.lower()).neq("id", tradie.id).execute)
        if check.data:
            raise HTTPException(status_code=400, detail="This slug is already taken.")
        updates["slug"] = data.slug.lower()

    if not updates: return {"status": "no-op"}

    res = await run_sync(supabase_admin.table("tradies").update(updates).eq("id", tradie.id).execute)
    if not res.data: raise HTTPException(status_code=400, detail="Update failed.")
    return {"status": "success", "data": res.data[0]}

@app.patch("/update-account")
async def update_account(data: UpdateAccountSchema, tradie: AuthenticatedTradie = Depends(get_current_user)):
    # [ACCOUNT_SECURITY_MUTATION]
    # Updates critical user identity and access credentials.
    auth_updates = {}
    if data.email: auth_updates["email"] = data.email
    if data.password: auth_updates["password"] = data.password

    if not auth_updates: return {"status": "no-op"}

    try:
        # 1. Update Supabase Auth via Admin API (Atomic rotation)
        # Note: If email changes, Supabase might send a verification link depending on config.
        await run_sync(supabase_admin.auth.admin.update_user_by_id, tradie.id, auth_updates)
        
        # 2. Sync email to tradies table if changed to maintain relational integrity.
        if data.email:
            await run_sync(supabase_admin.table("tradies").update({"email": data.email}).eq("id", tradie.id).execute)
            
        return {"status": "success"}
    except Exception as e:
        logger.error(f"ACCOUNT_UPDATE_FAILURE: {e}")
        raise HTTPException(status_code=400, detail="Failed to update account credentials.")

@app.post("/send-delete-code")
async def send_delete_code(tradie: AuthenticatedTradie = Depends(get_current_user)):
    # [DESTRUCTIVE_ACTION_MFA]
    # Issues a confirmation code via SMS before allowing account deletion.
    # Acts as a critical high-friction security gate.
    res = await run_sync(supabase_admin.table("tradies").select("phone_number").eq("id", tradie.id).single().execute)
    if not res.data: raise HTTPException(status_code=404, detail="Profile not found.")
    
    phone = res.data["phone_number"]
    code = str(random.randint(100000, 999999))
    verification_codes[f"DEL_{tradie.id}"] = code
    
    if twilio_client:
        try:
            await run_sync(twilio_client.messages.create, 
                messaging_service_sid=TEFLON_SERVICE_SID,
                body=f"Your Tradsiee account deletion code: {code}. If you didn't request this, ignore it.",
                to=phone
            )
        except Exception as e:
            logger.error(f"DELETION_SMS_FAILURE: {e}")
            raise HTTPException(status_code=500, detail="SMS failed.")
    return {"status": "success"}

@app.delete("/delete-account/{slug}")
async def delete_account(slug: str, code: str, tradie: AuthenticatedTradie = Depends(get_current_user)):
    # [ACCOUNT_TERMINATION_LOGIC]
    if verification_codes.get(f"DEL_{tradie.id}") != code:
        raise HTTPException(status_code=400, detail="Invalid code.")
    
    try:
        # 1. Delete Business Profile (RLS ensures we can only delete our own)
        # Note: Slug check is implicit because RLS only allows deleting id = auth.uid()
        await run_sync(tradie.supabase.table("tradies").delete().eq("id", tradie.id).execute)
        
        # 2. Delete Auth Record (Requires Admin)
        await run_sync(supabase_admin.auth.admin.delete_user, tradie.id)
        
        if f"DEL_{tradie.id}" in verification_codes:
            del verification_codes[f"DEL_{tradie.id}"]
        return {"status": "success"}
    except Exception as e:
        logger.error(f"DELETION_FAILURE: {e}")
        raise HTTPException(status_code=500, detail="Deletion failed.")

@app.get("/ops-tomas-99", response_class=HTMLResponse)
async def admin_page():
    # [SECRET_OPS_PORTAL]
    # Secret route for system management.
    return HTML_PAGES_CACHE.get("admin", "Page missing.")

@app.get("/admin-data")
async def get_admin_data(tradie: AuthenticatedTradie = Depends(get_current_user)):
    # [PRIVACY_AWARE_ADMIN_FETCH]
    if tradie.user.email != "tomas.gorjux@gmail.com":
        raise HTTPException(status_code=403, detail="Admin access denied.")

    # Use admin client to see ALL tradies (Admin override)
    tradies_res = await run_sync(
        supabase_admin.table("tradies")
        .select("id, business_name, email, credits, slug, created_at")
        .order("created_at", desc=True).execute
    )
    return tradies_res.data

@app.post("/admin/update-credits")
async def update_credits(data: dict, tradie: AuthenticatedTradie = Depends(get_current_user)):
    # [ADMIN_ACTION: CREDIT_MODIFICATION]
    if tradie.user.email != "tomas.gorjux@gmail.com":
        raise HTTPException(status_code=403, detail="Admin access denied.")

    tradie_id = data.get("tradie_id")
    new_credits = data.get("credits")

    if tradie_id is None or new_credits is None:
        raise HTTPException(status_code=400, detail="Missing data.")

    await run_sync(supabase_admin.table("tradies").update({"credits": new_credits}).eq("id", tradie_id).execute)
    return {"status": "success"}
@app.get("/health")
def health(): 
    # [SYSTEM_STATUS_PROBE]
    # Simple heartbeat endpoint for health checks and deployment verification.
    return {"status": "online", "version": "1.2.0"}

def send_lead_notifications(tradie_phone: str, customer_phone: str, description: str, biz_name: str):
    # [COMMUNICATIONS_WORKER]
    # Orchestrates SMS delivery for new lead events. 
    # Dispatched as a background task to keep API response times low.
    if not twilio_client: return
    t_phone, c_phone = format_phone(tradie_phone), format_phone(customer_phone)
    try:
        # Outbound Alert: Tradie
        twilio_client.messages.create(messaging_service_sid=TEFLON_SERVICE_SID, to=t_phone,
            body=f"TRADSIEE: New lead! {c_phone}\nDesc: {description[:30]}...\nView: {FRONTEND_URL}/portal.html")
        # Outbound Confirmation: Customer
        twilio_client.messages.create(messaging_service_sid=TEFLON_SERVICE_SID, to=c_phone,
            body=f"Sent! {biz_name} has received your video.")
    except Exception as e: logger.error(f"NOTIFICATION_PIPELINE_FAILURE: {e}")

