from fastapi import APIRouter, Query

from app.charger.lux_model import lux_pv_model

router = APIRouter(prefix="/api")


@router.get("/lux-model")
def get_lux_model():
    return lux_pv_model.get_model_data()


@router.get("/lux-model/predict")
def predict_pv(lux: float = Query(..., ge=0)):
    predicted = lux_pv_model.predict_max_pv(lux)
    return {
        "lux": lux,
        "predicted_max_pv": predicted,
        "model_ready": lux_pv_model.ready,
    }


@router.post("/lux-model/refresh")
def refresh_model():
    lux_pv_model._last_refresh = 0  # force refresh
    lux_pv_model.refresh_if_needed()
    return lux_pv_model.get_model_data()
