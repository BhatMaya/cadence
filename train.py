import argparse
import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np
import tensorflow as tf

from model import build_cadence_model
try:
    from util import create_pairs
except ImportError:
    def create_pairs(samples, user_ids, indices, **kwargs):
        pass

FEATURES_PATH = "packages/capture/tests/capture.test.ts"
MODEL_PATH = "cadence_base_model.keras"


def keystroke_to_vector(keystroke):
    return [
        float(keystroke["hold_time"]),
        float(keystroke["flight_time"] or 0.0),
        float(keystroke["down_down"] or 0.0),
        float(keystroke["up_up"] or 0.0),
    ]


def compute_features_from_events(events):
    """
    Converts raw down/up key events from sample.json into 
    the calculated metrics format expected downstream.
    """
    keystrokes = []
    active_downs = {}
    
    down_events = [e for e in events if e.get("type") == "down"]
    all_events = sorted(events, key=lambda e: e["t"])
    
    for event in all_events:
        code = event.get("code")
        t = float(event.get("t", 0.0))
        
        if event.get("type") == "down":
            active_downs[code] = t
            
        elif event.get("type") == "up" and code in active_downs:
            down_time = active_downs.pop(code)
            hold_time = t - down_time
            
            flight_time = None
            down_down = None
            up_up = None
            
            try:
                curr_idx = next(idx for idx, de in enumerate(down_events) if de["code"] == code and float(de["t"]) == down_time)
                if curr_idx > 0:
                    prev_down_event = down_events[curr_idx - 1]
                    prev_code = prev_down_event["code"]
                    prev_down_time = float(prev_down_event["t"])
                    prev_up_event = next((e for e in all_events if e["code"] == prev_code and e["type"] == "up" and float(e["t"]) > prev_down_time), None)
                    
                    down_down = down_time - prev_down_time
                    if prev_up_event:
                        prev_up_time = float(prev_up_event["t"])
                        flight_time = down_time - prev_up_time
                        up_up = t - prev_up_time
            except (StopIteration, ValueError):
                pass

            keystrokes.append({
                "code": code,
                "hold_time": hold_time,
                "flight_time": flight_time,
                "down_down": down_down,
                "up_up": up_up
            })
            
    return keystrokes


def load_feature_data(path=FEATURES_PATH):
    with open(path, "r", encoding="utf-8") as f:
        raw_data = json.load(f)

    if isinstance(raw_data, dict):
        if "samples" in raw_data:
            raw_data = raw_data["samples"]
        elif "features" in raw_data:
            raw_data = raw_data["features"]
        else:
            raw_data = list(raw_data.values())

    samples = []
    user_ids = []
    metas = []
    for item in raw_data:
        meta = item.get("meta", {})
        user_id = meta.get("user_id")
        if not user_id:
            continue

        if "keystrokes" in item:
            keystrokes = item.get("keystrokes", [])
        elif "events" in item:
            keystrokes = compute_features_from_events(item.get("events", []))
        else:
            continue

        if not keystrokes:
            continue

        samples.append(np.asarray([keystroke_to_vector(k) for k in keystrokes]))
        user_ids.append(str(user_id))
        metas.append(meta)

    if not samples:
        raise ValueError(f"No usable training samples found in {path}")

    return samples, np.asarray(user_ids), metas


def split_by_user_session(user_ids, metas, validation_split, seed):
    by_user = defaultdict(list)
    for index, user_id in enumerate(user_ids):
        by_user[user_id].append(index)

    train_indices = []
    validation_indices = []
    rng = np.random.default_rng(seed)

    for user_id in sorted(by_user):
        indices = by_user[user_id]
        sessions = defaultdict(list)
        has_sessions = True
        for index in indices:
            session_index = metas[index].get("session_index")
            if session_index is None:
                has_sessions = False
                break
            sessions[int(session_index)].append(index)

        if has_sessions and len(sessions) > 1:
            ordered_sessions = sorted(sessions)
            validation_session_count = max(
                1, math.ceil(len(ordered_sessions) * validation_split)
            )
            validation_sessions = set(ordered_sessions[-validation_session_count:])
            for session_index, session_indices in sessions.items():
                if session_index in validation_sessions:
                    validation_indices.extend(session_indices)
                else:
                    train_indices.extend(session_indices)
            continue

        shuffled = np.asarray(indices)
        rng.shuffle(shuffled)
        validation_count = max(1, math.ceil(len(shuffled) * validation_split))
        if validation_count >= len(shuffled):
            validation_count = max(1, len(shuffled) - 1)
        validation_indices.extend(shuffled[:validation_count].tolist())
        train_indices.extend(shuffled[validation_count:].tolist())

    return np.asarray(train_indices), np.asarray(validation_indices)


def fit_normalizer(samples, indices):
    values = np.concatenate([samples[index] for index in indices], axis=0)
    mean = values.mean(axis=0)
    std = values.std(axis=0)
    std = np.where(std < 1e-6, 1.0, std)
    return mean.astype("float32"), std.astype("float32")


def apply_normalizer(samples, mean, std):
    return [(sample.astype("float32") - mean) / std for sample in samples]


def pad_samples(samples):
    return tf.keras.preprocessing.sequence.pad_sequences(
        samples, dtype="float32", padding="post"
    )


def user_index_map(user_ids, indices):
    by_user = defaultdict(list)
    for index in indices:
        by_user[user_ids[index]].append(index)
    return by_user


def choose_indices(rng, candidates, limit):
    candidates = np.asarray(candidates)
    if limit is None or limit <= 0 or len(candidates) <= limit:
        return candidates.tolist()
    return rng.choice(candidates, size=limit, replace=False).tolist()


def create_login_attempt_pairs(
    samples,
    user_ids,
    enrollment_indices,
    probe_indices,
    enrollment_samples_per_user=10,
    max_probes_per_user=None,
    impostor_attempts_per_user=100,
    seed=42,
):
    enrollment_by_user = user_index_map(user_ids, enrollment_indices)
    probe_by_user = user_index_map(user_ids, probe_indices)
    users = sorted(set(enrollment_by_user) & set(probe_by_user))
    if len(users) < 2:
        raise ValueError("login-attempt validation needs at least two users")

    rng = np.random.default_rng(seed)
    left = []
    right = []
    pair_labels = []
    attempt_labels = []
    attempt_ranges = []

    def add_attempt(probe_index, claimed_user, label):
        enrollment = choose_indices(
            rng, enrollment_by_user[claimed_user], enrollment_samples_per_user
        )
        start = len(pair_labels)
        for enrollment_index in enrollment:
            left.append(samples[probe_index])
            right.append(samples[enrollment_index])
            pair_labels.append(float(label))
        attempt_ranges.append((start, len(pair_labels)))
        attempt_labels.append(float(label))

    for user_id in users:
        probes = choose_indices(rng, probe_by_user[user_id], max_probes_per_user)
        for probe_index in probes:
            add_attempt(probe_index, user_id, 1.0)

        other_probe_indices = [
            index
            for other_user in users
            if other_user != user_id
            for index in probe_by_user[other_user]
        ]
        impostor_count = min(impostor_attempts_per_user, len(other_probe_indices))
        impostors = rng.choice(other_probe_indices, size=impostor_count, replace=False)
        for probe_index in impostors:
            add_attempt(probe_index, user_id, 0.0)

    if not pair_labels:
        raise ValueError("no validation attempts were created")

    return (
        np.asarray(left, dtype="float32"),
        np.asarray(right, dtype="float32"),
        np.asarray(pair_labels, dtype="float32"),
        np.asarray(attempt_labels, dtype="float32"),
        attempt_ranges,
    )


def aggregate_attempt_scores(pair_scores, attempt_ranges, aggregation):
    scores = []
    for start, end in attempt_ranges:
        attempt_scores = pair_scores[start:end]
        if aggregation == "max":
            scores.append(float(np.max(attempt_scores)))
        elif aggregation == "median":
            scores.append(float(np.median(attempt_scores)))
        else:
            scores.append(float(np.mean(attempt_scores)))
    return np.asarray(scores, dtype="float32")


def binary_accuracy(labels, scores, threshold):
    predictions = scores >= threshold
    return float(np.mean(predictions == labels.astype(bool)))


def roc_auc(labels, scores):
    labels = np.asarray(labels, dtype="int32")
    scores = np.asarray(scores, dtype="float64")
    positive_count = int(labels.sum())
    negative_count = int(len(labels) - positive_count)
    if positive_count == 0 or negative_count == 0:
        return None

    order = np.argsort(scores)
    ranks = np.empty(len(scores), dtype="float64")
    ranks[order] = np.arange(1, len(scores) + 1)

    sorted_scores = scores[order]
    start = 0
    while start < len(scores):
        end = start + 1
        while end < len(scores) and sorted_scores[end] == sorted_scores[start]:
            end += 1
        if end - start > 1:
            ranks[order[start:end]] = (start + 1 + end) / 2.0
        start = end

    positive_rank_sum = ranks[labels == 1].sum()
    auc = (
        positive_rank_sum
        - positive_count * (positive_count + 1) / 2.0
    ) / (positive_count * negative_count)
    return float(auc)


def threshold_metrics(labels, scores):
    labels = np.asarray(labels, dtype="int32")
    scores = np.asarray(scores, dtype="float64")
    unique_scores = np.unique(scores)
    score_range = float(np.max(unique_scores) - np.min(unique_scores)) if len(unique_scores) > 0 else 0.0
    epsilon = max(1e-7, score_range * 1e-6)
    thresholds = np.concatenate(
        (
            [unique_scores[-1] + epsilon] if len(unique_scores) > 0 else [0.5],
            unique_scores[::-1],
            [unique_scores[0] - epsilon] if len(unique_scores) > 0 else [0.5],
        )
    )

    positive_count = max(1, int(labels.sum()))
    negative_count = max(1, int(len(labels) - labels.sum()))
    best_balanced = {
        "threshold": 0.5,
        "balanced_accuracy": -1.0,
        "accuracy": 0.0,
    }
    eer = {
        "threshold": 0.5,
        "eer": 1.0,
        "far": 1.0,
        "frr": 1.0,
    }
    eer_gap = float("inf")

    for threshold in thresholds:
        predictions = scores >= threshold
        tp = int(np.sum((predictions == 1) & (labels == 1)))
        tn = int(np.sum((predictions == 0) & (labels == 0)))
        fp = int(np.sum((predictions == 1) & (labels == 0)))
        fn = int(np.sum((predictions == 0) & (labels == 1)))

        tpr = tp / positive_count
        tnr = tn / negative_count
        far = fp / negative_count
        frr = fn / positive_count
        balanced_accuracy = (tpr + tnr) / 2.0
        accuracy = (tp + tn) / len(labels)

        if balanced_accuracy > best_balanced["balanced_accuracy"]:
            best_balanced = {
                "threshold": float(threshold),
                "balanced_accuracy": float(balanced_accuracy),
                "accuracy": float(accuracy),
            }

        gap = abs(far - frr)
        if gap < eer_gap:
            eer_gap = gap
            eer = {
                "threshold": float(threshold),
                "eer": float((far + frr) / 2.0),
                "far": float(far),
                "frr": float(frr),
            }

    return best_balanced, eer


def evaluate_scores(labels, scores):
    best_balanced, eer = threshold_metrics(labels, scores)
    return {
        "accuracy_at_0_5": binary_accuracy(labels, scores, 0.5),
        "roc_auc": roc_auc(labels, scores),
        "best_balanced_accuracy": best_balanced,
        "eer": eer,
    }


def parse_args():
    parser = argparse.ArgumentParser(description="Train the Cadence Siamese model.")
    parser.add_argument("--features-path", default=FEATURES_PATH)
    parser.add_argument("--model-path", default=MODEL_PATH)
    parser.add_argument("--metrics-path", default=None)
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--validation-split", type=float, default=0.2)
    parser.add_argument("--pair-seed", type=int, default=42)
    parser.add_argument("--max-samples", type=int, default=None)
    parser.add_argument("--positives-per-sample", type=int, default=2)
    parser.add_argument("--negatives-per-sample", type=int, default=2)
    parser.add_argument("--eval-enrollment-samples", type=int, default=10)
    parser.add_argument("--eval-max-probes-per-user", type=int, default=None)
    parser.add_argument("--eval-impostor-attempts-per-user", type=int, default=100)
    parser.add_argument(
        "--eval-aggregation", choices=["mean", "median", "max"], default="mean"
    )
    parser.add_argument("--early-stopping-patience", type=int, default=5)
    parser.add_argument("--no-early-stopping", action="store_true")
    parser.add_argument("--no-normalize", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()

    samples, user_ids, metas = load_feature_data(args.features_path)
    if args.max_samples is not None:
        samples = samples[: args.max_samples]
        user_ids = user_ids[: args.max_samples]
        metas = metas[: args.max_samples]

    train_indices, validation_indices = split_by_user_session(
        user_ids, metas, args.validation_split, args.pair_seed
    )
    if len(train_indices) == 0 or len(validation_indices) == 0:
        raise ValueError("training and validation splits must both be non-empty")

    if args.no_normalize:
        normalized_samples = [sample.astype("float32") for sample in samples]
        normalization = None
    else:
        mean, std = fit_normalizer(samples, train_indices)
        normalized_samples = apply_normalizer(samples, mean, std)
        normalization = {
            "mean": mean.tolist(),
            "std": std.tolist(),
            "feature_order": ["hold_time", "flight_time", "down_down", "up_up"],
        }

    padded_samples = pad_samples(normalized_samples)
    
    left_X, right_X, pair_labels = create_pairs(
        padded_samples,
        user_ids,
        indices=train_indices,
        positives_per_sample=args.positives_per_sample,
        negatives_per_sample=args.negatives_per_sample,
        seed=args.pair_seed,
    )
    (
        val_left_X,
        val_right_X,
        val_pair_labels,
        val_attempt_labels,
        val_attempt_ranges,
    ) = create_login_attempt_pairs(
        padded_samples,
        user_ids,
        enrollment_indices=train_indices,
        probe_indices=validation_indices,
        enrollment_samples_per_user=args.eval_enrollment_samples,
        max_probes_per_user=args.eval_max_probes_per_user,
        impostor_attempts_per_user=args.eval_impostor_attempts_per_user,
        seed=args.pair_seed + 1,
    )

    model = build_cadence_model(input_shape=(padded_samples.shape[1], 4))
    model.compile(optimizer=tf.keras.optimizers.Adam(learning_rate=0.0005), loss="binary_crossentropy", metrics=["accuracy"])

    callbacks = []
    if not args.no_early_stopping:
        callbacks.append(
            tf.keras.callbacks.EarlyStopping(
                monitor="val_loss",
                patience=args.early_stopping_patience,
                restore_best_weights=True,
            )
        )

    history = model.fit(
        [left_X, right_X],
        pair_labels,
        epochs=args.epochs,
        batch_size=args.batch_size,
        validation_data=([val_left_X, val_right_X], val_pair_labels),
        callbacks=callbacks,
    )
    val_losses = history.history.get("val_loss", [])
    best_epoch = int(np.argmin(val_losses)) if val_losses else None

    pair_scores = model.predict(
        [val_left_X, val_right_X],
        batch_size=args.batch_size,
        verbose=0,
    ).reshape(-1)
    attempt_scores = aggregate_attempt_scores(
        pair_scores, val_attempt_ranges, args.eval_aggregation
    )

    report = {
        "features_path": args.features_path,
        "model_path": args.model_path,
        "samples": len(samples),
        "users": int(len(set(user_ids.tolist()))),
        "split": {
            "strategy": "session_holdout_with_per_user_fallback",
            "train_samples": int(len(train_indices)),
            "validation_samples": int(len(validation_indices)),
            "validation_split": args.validation_split,
        },
        "pair_generation": {
            "train_pairs": int(len(pair_labels)),
            "validation_pairs": int(len(val_pair_labels)),
            "positives_per_sample": args.positives_per_sample,
            "negatives_per_sample": args.negatives_per_sample,
        },
        "attempt_evaluation": {
            "attempts": int(len(val_attempt_labels)),
            "enrollment_samples_per_user": args.eval_enrollment_samples,
            "impostor_attempts_per_user": args.eval_impostor_attempts_per_user,
            "aggregation": args.eval_aggregation,
        },
        "normalization": normalization,
        "history": {
            key: [float(value) for value in values]
            for key, values in history.history.items()
        },
        "best_epoch": {
            "index": best_epoch,
            "number": best_epoch + 1 if best_epoch is not None else None,
            "val_loss": (
                float(val_losses[best_epoch]) if best_epoch is not None else None
            ),
        },
        "pair_metrics": evaluate_scores(val_pair_labels, pair_scores),
        "attempt_metrics": evaluate_scores(val_attempt_labels, attempt_scores),
    }

    model.save(args.model_path)
    metrics_path = (
        Path(args.metrics_path)
        if args.metrics_path
        else Path(args.model_path).with_suffix(".metrics.json")
    )
    metrics_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    print(f"Saved model: {args.model_path}")
    print(f"Saved metrics: {metrics_path}")
    print(
        "Attempt EER: "
        f"{report['attempt_metrics']['eer']['eer']:.4f} "
        "at threshold "
        f"{report['attempt_metrics']['eer']['threshold']:.4f}"
    )
    print(
        "Attempt ROC AUC: "
        f"{report['attempt_metrics']['roc_auc']:.4f}"
    )


if __name__ == "__main__":
    main()
