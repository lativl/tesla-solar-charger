import asyncio
import logging

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.dashboard import router as dashboard_router
from app.api.lux_model import router as lux_model_router
from app.api.settings import router as settings_router
from app.api.tesla import router as tesla_router
from app.api.ws import router as ws_router
from app.charger.worker import run_worker, stop_worker
from app.database import init_db
from app.mqtt.client import mqtt_client
from app.tesla.manager import transport_manager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting Tesla Solar Charger...")
    init_db()
    transport_manager.initialize()
    mqtt_client.start()
    worker_task = asyncio.create_task(run_worker())
    logger.info("All services started")
    yield
    # Shutdown
    logger.info("Shutting down...")
    stop_worker()
    mqtt_client.stop()
    worker_task.cancel()


app = FastAPI(
    title="Tesla Solar Charger",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(dashboard_router)
app.include_router(lux_model_router)
app.include_router(settings_router)
app.include_router(tesla_router)
app.include_router(ws_router)
app.mount("/", StaticFiles(directory="app/static", html=True), name="static")
