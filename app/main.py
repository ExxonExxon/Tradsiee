import os
import re
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.exceptions import HTTPException
from fastapi.staticfiles import StaticFiles

from app.core.config import (
    logger, WIDGET_TEMPLATE_CACHE, HTML_PAGES_CACHE
)
from app.api import auth, leads, admin, pages
from app.core.video_processor import process_video_queue_worker

# --- Lifespan Management ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    # [STARTUP_PROCEDURES]
    # Executed on application boot. Validates the environment and prepares the template cache.
    try:
        # Initialize the sequential processing queue
        app.state.video_queue = asyncio.Queue()
        # Start the background worker (one-by-one processing)
        asyncio.create_task(process_video_queue_worker(app.state.video_queue))

        global WIDGET_TEMPLATE_CACHE, HTML_PAGES_CACHE
        
        required_env = [
            "CLOUDINARY_NAME", "CLOUDINARY_UPLOAD_PRESET",
            "SUPABASE_URL", "SUPABASE_KEY",
            "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_MESSAGING_SERVICE_SID"
        ]
        missing = [key for key in required_env if not os.getenv(key)]
        if missing:
            logger.error(f"CRITICAL_FAILURE: Missing environment variables: {', '.join(missing)}")

        ui_paths = {
            "[[PATH_LOGIN]]": os.getenv("PATH_LOGIN", "/login"),
            "[[PATH_SIGNUP]]": os.getenv("PATH_SIGNUP", "/signup"),
            "[[PATH_PORTAL]]": os.getenv("PATH_PORTAL", "/portal"),
            "[[PATH_UPDATE_PWD]]": os.getenv("PATH_UPDATE_PWD", "/update-password"),
            "[[PATH_PREVIEW]]": os.getenv("PATH_PREVIEW", "/preview"),
        }

        def process_html(filename: str, is_widget: bool = False) -> str:
            path = os.path.join("web", "templates", filename)
            if not os.path.exists(path): return ""
            with open(path, "r") as f: content = f.read()
            content = re.sub(r'>\s+<', '><', content)
            for placeholder, value in ui_paths.items():
                content = content.replace(placeholder, value)
            
            # Inject Global Configuration
            content = content.replace('[[SUPABASE_URL]]', os.getenv("SUPABASE_URL", ""))
            content = content.replace('[[SUPABASE_ANON_KEY]]', os.getenv("SUPABASE_ANON_KEY", os.getenv("SUPABASE_KEY", "")))

            if is_widget:
                content = content.replace('[[CLOUD_NAME]]', os.getenv("CLOUDINARY_NAME", ""))
                content = content.replace('[[UPLOAD_PRESET]]', os.getenv("CLOUDINARY_UPLOAD_PRESET", ""))
            return content

        import app.core.config as config
        config.WIDGET_TEMPLATE_CACHE = process_html("index.html", is_widget=True)
        
        pages_to_cache = {
            "login": "login.html", "signup": "signup.html", "portal": "portal.html",
            "update-password": "update-password.html", "preview": "widget-preview.html",
            "admin": "admin.html", "verified": "verified.html", "email-changed": "email-changed.html"
        }
        for key, filename in pages_to_cache.items():
            config.HTML_PAGES_CACHE[key] = process_html(filename)
            
        logger.info(f"TEMPLATE_CACHE_LOADED: {', '.join(config.HTML_PAGES_CACHE.keys())}")
    except Exception as e:
        logger.error(f"STARTUP_EXCEPTION: {e}")
    yield

# --- FastAPI Initialization ---

app = FastAPI(title="Tradsiee_Engine_v1.2", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="web/static"), name="static")

# --- Middleware ---

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=".*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"]
)

# --- Exception Handlers ---

@app.exception_handler(404)
async def custom_404_handler(request: Request, exc: Exception):
    if request.url.path.endswith(".js"):
        return PlainTextResponse("// Resource not found", media_type="application/javascript", status_code=404)
    return JSONResponse(status_code=404, content={"detail": "Not found"})

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"UNHANDLED_SYSTEM_EXCEPTION: {exc}", exc_info=True)
    return JSONResponse(status_code=500, content={"detail": "Internal server error."})

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

# --- Router Inclusion ---

app.include_router(auth.router)
app.include_router(leads.router)
app.include_router(admin.router)
app.include_router(pages.router)

@app.get("/health")
def health(): 
    return {"status": "online", "version": "1.2.0"}
