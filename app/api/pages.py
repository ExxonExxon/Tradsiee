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
async def get_config_js(request: Request):
    from app.core.config import SMS_AUTH_ENABLED, API_BASE_URL

    # Detect origin dynamically
    host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
    origin = f"{scheme}://{host}"

    js_content = f"""
/**
 * Tradsiee Global Configuration (Dynamically Generated)
 */
const TRADSIEE_ENV = {{
    isLocal: window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1',
    leadLimitsEnabled: {str(LEAD_LIMITS_ENABLED).lower()},
    smsAuthEnabled: {str(SMS_AUTH_ENABLED).lower()},
    apiBase: "{API_BASE_URL}",
    dynamicOrigin: "{origin}",
    get API_BASE() {{
        if (this.isLocal) return this.dynamicOrigin;
        return this.apiBase;
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
    from app.core.dependencies import run_sync
    from app.core.config import supabase_admin

    # Detect origin dynamically
    host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
    origin = f"{scheme}://{host}"
    
    # Fetch business phone for fallback
    try:
        res = await run_sync(supabase_admin.table("tradies").select("phone_number").eq("slug", slug).single().execute)
        biz_phone = res.data.get("phone_number") if res.data else ""
    except Exception:
        biz_phone = ""
    
    clean_phone = biz_phone.replace(" ", "")

    js = f"""
(function() {{
    window.TRADSIEE_SLUG = "{slug}";
    var origin = "{origin}";
    var bizPhone = "{biz_phone}";
    var cleanPhone = "{clean_phone}";
    
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
    iframe.style.cssText = 'border:none;width:100%;height:500px;background:transparent;overflow:hidden;transition:height 0.3s cubic-bezier(0.2,0,0,1);';
    iframe.scrolling = 'no';
    iframe.loading = 'lazy';

    var hasLoaded = false;
    var timeout = setTimeout(function() {{
        if (!hasLoaded) {{
            var fallback = document.createElement('div');
            fallback.style.cssText = 'padding:28px; background:#fff; border-radius:32px; border:1px solid rgba(0,0,0,0.05); box-shadow:0 4px 24px rgba(0,0,0,0.06); text-align:center; font-family:"Plus Jakarta Sans", sans-serif; max-width:420px; margin:0 auto;';
            fallback.innerHTML = '<div style="width:64px;height:64px;background:#FFF5F5;border-radius:50%;display:flex;align-items:center;justify-content:center;margin:0 auto 20px;"><svg width="30" height="30" fill="none" stroke="#FF3B30" viewBox="0 0 24 24"><path d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5"/></svg></div>' +
                               '<h3 style="font-size:18px;font-weight:800;margin-bottom:8px;color:#1C1C1E;">Oh no!</h3>' +
                               '<p style="font-size:13px;color:#8E8E93;line-height:1.5;margin:0;">Looks like Tradsiee is having some issues. Please <a href="tel:'+cleanPhone+'" style="color:#007AFF;text-decoration:none;font-weight:700;">call us instead</a> at <strong>'+bizPhone+'</strong></p>';
            if (iframe.parentNode) {{
                iframe.parentNode.replaceChild(fallback, iframe);
            }}
        }}
    }}, 8000);

    window.addEventListener('message', function(e) {{
        if (e.data && e.data.type === 'tradsiee-resize') {{
            hasLoaded = true;
            clearTimeout(timeout);
            // Use 40px buffer for card shadows and bottom margins
            iframe.style.height = (e.data.height + 40) + 'px';
        }}
    }});

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
    from app.core.dependencies import run_sync
    from app.core.config import supabase_admin

    content = config.WIDGET_TEMPLATE_CACHE or "Template missing."
    c_name = os.getenv("CLOUDINARY_NAME", "MISSING")
    
    # Fetch business name and phone based on slug
    try:
        res = await run_sync(supabase_admin.table("tradies").select("business_name, phone_number").eq("slug", slug).single().execute)
        biz_name = res.data.get("business_name") if res.data else "a Professional"
        biz_phone = res.data.get("phone_number") if res.data else ""
    except Exception:
        biz_name = "a Professional"
        biz_phone = ""
        
    biz_initial = biz_name[0].upper() if biz_name else "T"

    logger.info(f"WIDGET_SERVED: slug={slug} | cloudinary={c_name} | biz_name={biz_name}")
    
    # Inject variables
    content = content.replace('[[SLUG_PLACEHOLDER]]', slug)
    content = content.replace('[[BUSINESS_NAME]]', biz_name)
    content = content.replace('[[BUSINESS_PHONE]]', biz_phone)
    content = content.replace('[[BUSINESS_INITIAL]]', biz_initial)
    
    return HTMLResponse(
        content=content,
        headers={
            "Cross-Origin-Resource-Policy": "cross-origin",
            "X-Frame-Options": "ALLOWALL"
        }
    )

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

@router.get("/email-changed", response_class=HTMLResponse)
async def serve_email_changed():
    return HTML_PAGES_CACHE.get("email-changed", "Page missing.")
