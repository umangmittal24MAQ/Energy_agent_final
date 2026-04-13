"""
FastAPI application setup and middleware configuration
"""
from dotenv import load_dotenv
load_dotenv()  # Load environment variables from .env file

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError

from app.core.config import get_settings
from app.core.logger import setup_logging, get_logger
from app.services.scheduler_service import initialize_scheduler_from_config, stop_scheduler

logger = get_logger(__name__)

# Start the background scheduler when Azure boots the app
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting background scheduler...")
    initialize_scheduler_from_config()
    yield
    logger.info("Shutting down background scheduler...")
    stop_scheduler()


def create_app() -> FastAPI:
    """
    Create and configure the FastAPI application

    Returns:
        Configured FastAPI app instance
    """
    settings = get_settings()

    # Setup logging
    setup_logging(settings.log_level)

    # Create FastAPI app with lifespan attached
    app = FastAPI(
        title=settings.app_name,
        description="API for Energy Consumption Dashboard - Noida Campus",
        version=settings.app_version,
        debug=settings.debug,
        lifespan=lifespan  # <-- Attach the lifecycle manager here
    )

    # Add CORS middleware
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

    # Health check endpoint
    @app.get("/health")
    async def health_check():
        """Health check endpoint"""
        return {"status": "healthy", "service": settings.app_name}

    # Include routers
    try:
        from app.routes import data, kpis, export, scheduler
        app.include_router(data.router, prefix="/api")
        app.include_router(kpis.router, prefix="/api")
        # Add this line near your other app.include_router calls
        from app.routes import mail
        app.include_router(mail.router, prefix="/api")
        app.include_router(export.router, prefix="/api")
        app.include_router(scheduler.router, prefix="/api")  # add prefix here
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