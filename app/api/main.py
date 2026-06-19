"""
FastAPI application setup and middleware configuration
"""
import os
import uuid
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from dotenv import load_dotenv

from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler

from app.core.config import get_settings
from app.core.logger import setup_logging, get_logger
from app.core.rate_limit import limiter
from app.services.scheduler_service import initialize_scheduler_from_config, stop_scheduler


logger = get_logger(__name__)

# Ensure server/.env is loaded for all routes/services (SharePoint, SMTP, scheduler).
ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(dotenv_path=ENV_PATH)


# --- 1. THE UNIFIED LIFESPAN MANAGER ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- PHASE A: Verify Environment Variables ---
    logger.info("\n" + "="*50)
    logger.info("VERIFYING ENVIRONMENT VARIABLES")
    logger.info("="*50)

    smtp_server   = os.getenv("SMTP_SERVER", "smtp.gmail.com (Default)")
    smtp_port     = os.getenv("SMTP_PORT", "587 (Default)")
    email_from    = os.getenv("EMAIL_FROM", "[MISSING]")
    operator_mail = os.getenv("OPERATOR_EMAIL", "[MISSING]")
    report_mail   = os.getenv("REPORT_EMAIL", "[MISSING]")

    azure_client  = os.getenv("AZURE_CLIENT_ID", "[MISSING]")
    azure_tenant  = os.getenv("AZURE_TENANT_ID", "[MISSING]")
    session_secret = os.getenv("SESSION_SECRET", "[MISSING]")

    email_pwd = os.getenv("EMAIL_PASSWORD")
    pwd_status = f"[SET] (Length: {len(email_pwd)})" if email_pwd else "[MISSING]"

    logger.info(f"SMTP_SERVER   : {smtp_server}")
    logger.info(f"SMTP_PORT     : {smtp_port}")
    logger.info(f"EMAIL_FROM    : {email_from}")
    logger.info(f"OPERATOR_MAIL : {operator_mail}")
    logger.info(f"REPORT_MAIL   : {report_mail}")
    logger.info(f"EMAIL_PASSWORD: {pwd_status}")
    logger.info(f"AZURE_CLIENT_ID : {azure_client}")
    logger.info(f"SESSION_SECRET  : {'[SET]' if session_secret != '[MISSING]' else '[MISSING]'}")

    if not email_pwd or email_from == "[MISSING]" or report_mail == "[MISSING]":
        logger.error("CRITICAL: Core email variables are missing. Automated emails WILL fail.")
    elif azure_client == "[MISSING]" or session_secret == "[MISSING]":
        logger.error("CRITICAL: Core authentication variables are missing. Logins WILL fail.")
    else:
        logger.info("SUCCESS: All mail and auth variables loaded successfully.")

    # --- Meter OCR Engine env-var check ---
    meter_ocr_key      = os.getenv("AZURE_OPENAI_API_KEY", "[MISSING]")
    meter_ocr_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "[MISSING]")
    meter_ocr_deploy   = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", "[MISSING]")
    meter_sp_list      = os.getenv("SHAREPOINT_LIST_ID", "[MISSING]")
    logger.info("--- Meter OCR Engine ---")
    logger.info(f"AZURE_OPENAI_ENDPOINT        : {meter_ocr_endpoint}")
    logger.info(f"AZURE_OPENAI_DEPLOYMENT_NAME : {meter_ocr_deploy}")
    logger.info(f"AZURE_OPENAI_API_KEY         : {'[SET]' if meter_ocr_key != '[MISSING]' else '[MISSING]'}")
    logger.info(f"SHAREPOINT_LIST_ID           : {meter_sp_list}")
    if "[MISSING]" in (meter_ocr_key, meter_ocr_endpoint, meter_ocr_deploy, meter_sp_list):
        logger.error("WARNING: One or more Meter OCR env-vars are missing. meter_process.py will fail at runtime.")

    logger.info("="*50 + "\n")

    # --- PHASE B: Start the Scheduler ---
    logger.info("Starting background scheduler...")
    initialize_scheduler_from_config()

    # --- YIELD TO FASTAPI (Server is now running) ---
    yield

    # --- PHASE C: Shutdown Logic ---
    logger.info("Shutting down background scheduler...")
    stop_scheduler(disable_auto_start=False)
    logger.info("Server shutting down...")


# --- 2. FASTAPI APP CREATION ---
def create_app() -> FastAPI:
    """
    Create and configure the FastAPI application
    """
    settings = get_settings()

    setup_logging(settings.log_level)

    app = FastAPI(
        title=settings.app_name,
        description="API for Energy Consumption Dashboard - Noida Campus",
        version=settings.app_version,
        debug=settings.debug,
        lifespan=lifespan,
    )

    # ──────────────────────────────────────────────────────────────────────────
    # Rate Limiting Setup (FIX RL-01: Protect auth endpoints from DoS)
    # ──────────────────────────────────────────────────────────────────────────
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


    #  CRITICAL FOR AUTH: allow_credentials MUST be True, and
    # allowed_origins_list MUST NOT be ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def log_requests(request, call_next):
        req_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        is_health = request.url.path in ("/health", "/")
        log = logger.debug if is_health else logger.info
        log(f"[{req_id}] {request.method} {request.url.path}")
        response = await call_next(request)
        log(f"[{req_id}] Status: {response.status_code}")
        response.headers["X-Request-ID"] = req_id
        return response

    @app.get("/")
    async def root_health_check():
        return {"status": "running", "message": "Energy Dashboard API"}

    @app.get("/health")
    async def health_check():
        """Shallow health check endpoint — public, used by load balancers."""
        return {"status": "healthy", "service": settings.app_name}
        
    @app.post("/dev/trigger-scraper")
    async def trigger_scraper():
        import subprocess
        result = subprocess.run(
            ["python", "app/scripts/scrape_to_sharepoint.py"],
            capture_output=True, text=True
        )
        return {"stdout": result.stdout, "stderr": result.stderr, "exit_code": result.returncode}

    # FIX R5: Deep health check now requires authentication.
    # Previously this endpoint was public and leaked raw exception messages
    # (up to 120 chars of internal error detail including file paths, SharePoint
    # URLs, and config details) to any unauthenticated caller.
    # Now only logged-in users can reach it, and errors are sanitized for the
    # response while the full detail is still logged server-side.
    @app.get("/api/health/deep")
    async def deep_health(
        current_user: dict = Depends(_get_current_user_dependency()),
    ):
        """Deep health check — tests real SharePoint connectivity. Requires auth."""
        results = {}
        overall = "ok"

        try:
            from app.services.sharepoint_data_service import get_service
            sp = get_service()
            df = sp.fetch_sheet_data("master_data")
            results["sharepoint"] = "ok" if df is not None else "error"
        except Exception as e:
            # Log the full error server-side but return only a generic message
            # to the client so internal paths/config are never exposed.
            logger.error(f"Deep health check — SharePoint error: {e}", exc_info=True)
            results["sharepoint"] = "error: connection failed"

        if any("error" in str(v) for v in results.values()):
            overall = "degraded"

        status_code = 200 if overall == "ok" else 503
        return JSONResponse(status_code=status_code, content={"status": overall, **results})

    # Include routers
    try:
        from app.routes import data, kpis, export, scheduler, mail, auth, weather, temperature

        app.include_router(auth.router, prefix="/api/auth", tags=["Authentication"])
        app.include_router(data.router, prefix="/api")
        app.include_router(kpis.router, prefix="/api")
        app.include_router(mail.router, prefix="/api")
        app.include_router(export.router, prefix="/api")
        app.include_router(scheduler.router, prefix="/api")
        app.include_router(weather.router, prefix="/api")
        app.include_router(temperature.router, prefix="/api")
        logger.info("All routers loaded successfully")
    except ImportError as e:
        logger.error(f"Failed to import routers: {e}", exc_info=True)
        raise
    except Exception as e:
        logger.error(f"Unexpected error loading routers: {type(e).__name__}: {e}", exc_info=True)
        raise

    return app


def _get_current_user_dependency():
    """
    Lazily import get_current_user to avoid circular import at module level.
    This is called once during create_app(), after all modules are loaded.
    """
    from app.routes.auth import get_current_user
    return get_current_user


def get_app() -> FastAPI:
    """Get the FastAPI application instance"""
    return create_app()


app = create_app()