"""
FastAPI application setup and middleware configuration
"""
import os
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError

from app.core.config import get_settings
from app.core.logger import setup_logging, get_logger
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
    
    # Grab the variables
    smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com (Default)")
    smtp_port = os.getenv("SMTP_PORT", "587 (Default)")
    email_from = os.getenv("EMAIL_FROM", "[MISSING]")
    operator_mail = os.getenv("OPERATOR_MAIL", "[MISSING]")  # For Ojas
    report_mail = os.getenv("REPORT_MAIL", "[MISSING]")      # For CEO
    
    # 🚀 NEW: Check Auth Variables
    azure_client = os.getenv("AZURE_CLIENT_ID", "[MISSING]")
    azure_tenant = os.getenv("AZURE_TENANT_ID", "[MISSING]")
    session_secret = os.getenv("SESSION_SECRET", "[MISSING]")
    
    email_pwd = os.getenv("EMAIL_PASSWORD")
    if email_pwd:
        pwd_status = f"[SET] (Length: {len(email_pwd)})"
    else:
        pwd_status = "[MISSING]"

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

    # Setup logging
    setup_logging(settings.log_level)

    # Create FastAPI app with the unified lifespan attached
    app = FastAPI(
        title=settings.app_name,
        description="API for Energy Consumption Dashboard - Noida Campus",
        version=settings.app_version,
        debug=settings.debug,
        lifespan=lifespan  
    )

    # Add CORS middleware
    # 🚨 CRITICAL FOR AUTH: allow_credentials MUST be True, and allowed_origins_list MUST NOT be ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Add request/response logging middleware
    @app.middleware("http")
    async def log_requests(request, call_next):
        logger.info(f"{request.method} {request.url.path}")
        response = await call_next(request)
        logger.info(f"Status code: {response.status_code}")
        return response
    
    @app.get("/")
    async def root_health_check():
        return {"status": "running", "message": "Energy Dashboard API"}

    # Health check endpoint (Already exists in your code)
    @app.get("/health")
    async def health_check():
        """Health check endpoint"""
        return {"status": "healthy", "service": settings.app_name}
    # Health check endpoint
    @app.get("/health")
    async def health_check():
        """Health check endpoint"""
        return {"status": "healthy", "service": settings.app_name}

    # Include routers
    try:
        # 🚀 NEW: Imported 'auth'
        from app.routes import data, kpis, export, scheduler, mail, auth
        
        # 🚀 NEW: Added the auth router
        app.include_router(auth.router, prefix="/api/auth", tags=["Authentication"])
        
        app.include_router(data.router, prefix="/api")
        app.include_router(kpis.router, prefix="/api")
        app.include_router(mail.router, prefix="/api")
        app.include_router(export.router, prefix="/api")
        app.include_router(scheduler.router, prefix="/api")
        logger.info("All routers loaded successfully")
    except ImportError as e:
        logger.error(f"Failed to import routers: {e}", exc_info=True)
        raise
    except Exception as e:
        logger.error(f"Unexpected error loading routers: {type(e).__name__}: {e}", exc_info=True)
        raise

    return app

def get_app() -> FastAPI:
    """Get the FastAPI application instance"""
    return create_app()

app = create_app()