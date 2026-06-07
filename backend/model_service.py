import json
import logging
import os
import sys
from pathlib import Path

# Local demo machines may have GPUs newer than the installed TensorFlow build.
# If TensorFlow attempts to JIT unsupported GPU kernels, scoring fails and the
# auth flow falls back to a zero confidence score. Keep inference on CPU unless
# a deployment explicitly opts into GPU visibility before importing this module.
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "-1")

import numpy as np
import tensorflow as tf


logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL_PATH = REPO_ROOT / "models" / "cadence_base_model.keras"
DEFAULT_METRICS_PATH = REPO_ROOT / "models" / "cadence_base_model.metrics.json"
DEFAULT_ENROLLMENT_LIMIT = 10


class CadenceModelService:
    def __init__(
        self,
        model_path=None,
        metrics_path=None,
        enrollment_limit=None,
    ):
        self.model_path = Path(
            model_path or os.getenv("CADENCE_MODEL_PATH", DEFAULT_MODEL_PATH)
        )
        self.metrics_path = Path(
            metrics_path
            or os.getenv("CADENCE_MODEL_METRICS_PATH", DEFAULT_METRICS_PATH)
        )
        self.enrollment_limit = int(
            enrollment_limit
            or os.getenv("CADENCE_ENROLLMENT_LIMIT", DEFAULT_ENROLLMENT_LIMIT)
        )
        self._models = {}
        self._mean = None
        self._std = None
        self._feature_order = None

    def health(self):
        return {
            "model_path": str(self.model_path),
            "metrics_path": str(self.metrics_path),
            "model_exists": self.model_path.exists(),
            "metrics_exists": self.metrics_path.exists(),
            "loaded_lengths": sorted(self._models.keys()),
        }

    def score_login_attempt(
        self,
        supabase,
        user_id,
        raw_data,
        login_attempt_id=None,
    ):
        try:
            current_sample = self.raw_data_to_sample(raw_data)
            enrollment_samples = self.fetch_enrollment_samples(
                supabase, user_id, login_attempt_id
            )
            if not enrollment_samples:
                return None
            return self.score_against_enrollment(current_sample, enrollment_samples)
        except Exception:
            logger.exception("Model scoring failed for user_id=%s", user_id)
            return None

    def score_against_enrollment(self, current_sample, enrollment_samples):
        target_length = int(len(current_sample))
        model = self.model_for_length(target_length)
        current = self.prepare_sample(current_sample, target_length)
        enrolled = [
            self.prepare_sample(sample, target_length) for sample in enrollment_samples
        ]
        left = np.repeat(current[np.newaxis, :, :], len(enrolled), axis=0)
        right = np.asarray(enrolled, dtype="float32")
        result = model(
            [
                tf.convert_to_tensor(left),
                tf.convert_to_tensor(right),
            ],
            training=False,
        )
        scores = result.numpy().reshape(-1)
        return float(np.mean(scores))

    def fetch_enrollment_samples(self, supabase, user_id, login_attempt_id=None):
        query = (
            supabase.table("login_attempts")
            .select("login_attempt_id, raw_data")
            .eq("user_id", user_id)
            .eq("successful_login", True)
            .order("login_number", desc=True)
            .limit(self.enrollment_limit)
        )
        if login_attempt_id is not None:
            query = query.neq("login_attempt_id", login_attempt_id)

        result = query.execute()
        samples = []
        for row in result.data or []:
            raw_data = row.get("raw_data")
            if raw_data:
                samples.append(self.raw_data_to_sample(raw_data))
        return samples

    def load_normalization(self):
        if self._mean is not None and self._std is not None:
            return

        metrics = json.loads(self.metrics_path.read_text(encoding="utf-8"))
        normalization = metrics["normalization"]
        self._mean = np.asarray(normalization["mean"], dtype="float32")
        self._std = np.asarray(normalization["std"], dtype="float32")
        self._feature_order = normalization.get("feature_order") or [
            "hold_time",
            "flight_time",
            "down_down",
        ]
        if len(self._feature_order) != len(self._mean):
            raise ValueError("normalization feature_order length must match mean/std")

    def feature_order(self):
        self.load_normalization()
        return self._feature_order

    def model_for_length(self, input_length):
        if input_length <= 0:
            raise ValueError("input_length must be positive")
        if input_length in self._models:
            return self._models[input_length]

        if str(REPO_ROOT) not in sys.path:
            sys.path.insert(0, str(REPO_ROOT))
        from model import build_cadence_model

        self.load_normalization()
        model = build_cadence_model(input_shape=(input_length, len(self._mean)))
        model.load_weights(self.model_path)
        self._models[input_length] = model
        return model

    def prepare_sample(self, sample, target_length):
        self.load_normalization()
        sample = np.asarray(sample, dtype="float32")
        if sample.ndim != 2 or sample.shape[1] != len(self._mean):
            raise ValueError(
                f"typing sample must have shape (timesteps, {len(self._mean)})"
            )

        sample = (sample - self._mean) / self._std
        sample = sample[:target_length]

        padded = np.zeros((target_length, len(self._mean)), dtype="float32")
        padded[: len(sample)] = sample
        return padded

    def raw_data_to_sample(self, raw_data):
        if isinstance(raw_data, list):
            return self.keystrokes_to_sample(raw_data)

        if not isinstance(raw_data, dict):
            raise ValueError("raw_data must be a dict or list")

        if "keystrokes" in raw_data:
            return self.keystrokes_to_sample(raw_data["keystrokes"])
        if "events" in raw_data:
            return self.events_to_sample(raw_data["events"])

        raise ValueError("raw_data must contain keystrokes or events")

    def keystrokes_to_sample(self, keystrokes):
        sample = []
        feature_order = self.feature_order()
        for key in keystrokes:
            sample.append([float(key.get(feature) or 0.0) for feature in feature_order])
        if not sample:
            raise ValueError("keystrokes cannot be empty")
        return np.asarray(sample, dtype="float32")

    def events_to_sample(self, events):
        pressed = {}
        paired = []
        for event in events:
            event_type = event.get("type")
            code = event.get("code")
            timestamp = event.get("t", event.get("t_raw"))
            if event_type is None or code is None or timestamp is None:
                continue
            timestamp = float(timestamp)
            if event_type == "down":
                pressed[code] = timestamp
            elif event_type == "up" and code in pressed:
                paired.append(
                    {
                        "code": code,
                        "down_t": pressed.pop(code),
                        "up_t": timestamp,
                    }
                )

        paired.sort(key=lambda item: (item["down_t"], item["up_t"]))
        sample = []
        for index, current in enumerate(paired):
            previous = paired[index - 1] if index > 0 else None
            hold_time = current["up_t"] - current["down_t"]
            flight_time = (
                current["down_t"] - previous["up_t"] if previous else 0.0
            )
            down_down = (
                current["down_t"] - previous["down_t"] if previous else 0.0
            )
            up_up = current["up_t"] - previous["up_t"] if previous else 0.0
            features = {
                "hold_time": hold_time,
                "flight_time": flight_time,
                "down_down": down_down,
                "up_up": up_up,
            }
            sample.append([features[feature] for feature in self.feature_order()])

        if not sample:
            raise ValueError("events did not contain any complete key pairs")
        return np.asarray(sample, dtype="float32")
