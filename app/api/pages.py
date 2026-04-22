import os
from fastapi import APIRouter, Request, Response, HTTPException, Depends
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from app.core.config import (
    LEAD_LIMITS_ENABLED, API_BASE_URL, HTML_PAGES_CACHE, 
    WIDGET_TEMPLATE_CACHE, logger
)
from app.core.dependencies import get_base_url

router = APIRouter(tags=["Pages"])

@router.get("/config.js")
async def get_config_js():
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

@router.get("/widget-bundle.js")
async def get_widget_bundle():
    path = os.path.join("web", "static", "widget-bundle.js")
    if os.path.exists(path):
        return FileResponse(
            path, 
            media_type="application/javascript",
            headers={"Cache-Control": "public, max-age=31536000, immutable"}
        )
    return Response(content="// widget-bundle.js not found", media_type="application/javascript")

@router.get("/loader.js")
async def loader_js(request: Request, slug: str):
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

@router.get("/widget/{slug}", response_class=HTMLResponse)
async def get_widget_ui(slug: str):
    import app.core.config as config
    content = config.WIDGET_TEMPLATE_CACHE or "Template missing."
    c_name = os.getenv("CLOUDINARY_NAME", "MISSING")
    logger.info(f"WIDGET_SERVED: slug={slug} | cloudinary={c_name}")
    return content.replace('[[SLUG_PLACE_HOLDER]]', slug)

@router.get("/", response_class=HTMLResponse)
async def serve_home():
    return HTML_PAGES_CACHE.get("login", "Page missing.")

@router.get(os.getenv("PATH_LOGIN", "/login"), response_class=HTMLResponse)
async def serve_login():
    return HTML_PAGES_CACHE.get("login", "Page missing.")

@router.get(os.getenv("PATH_SIGNUP", "/signup"), response_class=HTMLResponse)
async def serve_signup():
    return HTML_PAGES_CACHE.get("signup", "Page missing.")

@router.get(os.getenv("PATH_PORTAL", "/portal"), response_class=HTMLResponse)
async def serve_portal():
    return HTML_PAGES_CACHE.get("portal", "Page missing.")

@router.get(os.getenv("PATH_UPDATE_PWD", "/update-password"), response_class=HTMLResponse)
async def serve_update_password():
    return HTML_PAGES_CACHE.get("update-password", "Page missing.")

@router.get(os.getenv("PATH_PREVIEW", "/preview"), response_class=HTMLResponse)
async def serve_preview():
    return HTML_PAGES_CACHE.get("preview", "Page missing.")

@router.get("/verified", response_class=HTMLResponse)
async def serve_verified():
    return HTML_PAGES_CACHE.get("verified", "Page missing.")
