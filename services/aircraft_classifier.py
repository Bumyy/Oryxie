import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)
MODEL_FILE = Path(__file__).resolve().parent / "aircraft_models.json"


def load_aircraft_models():
    """Load the fleet registration-to-model mapping."""
    try:
        with MODEL_FILE.open("r", encoding="utf-8") as file:
            data = json.load(file)
        mapping = data.get("aircraft_models", data) if isinstance(data, dict) else {}
        if not isinstance(mapping, dict):
            logger.error("Invalid aircraft_models.json format.")
            return {}
        return {
            str(registration).strip().upper(): str(model).strip().upper()
            for registration, model in mapping.items()
            if isinstance(registration, str) and isinstance(model, str)
        }
    except FileNotFoundError:
        logger.warning("Aircraft mapping file not found: %s", MODEL_FILE)
        return {}
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Could not read aircraft mapping file: %s", exc)
        return {}


def get_aircraft_model(registration, model_code=None):
    """Return the model code for a registration, or an explicit supplied code."""
    code = str(model_code or "").strip().upper()
    if code:
        return code
    key = str(registration or "").strip().upper()
    return load_aircraft_models().get(key, "UNKNOWN")


def classify_aircraft(registration, model_code=None):
    """Return 'Airbus', 'Boeing', or 'Unknown'."""
    model = get_aircraft_model(registration, model_code)
    if model.startswith("A") and len(model) > 1 and model[1].isdigit():
        return "Airbus"
    if model.startswith("777") or model.startswith("787"):
        return "Boeing"
    return "Unknown"
