import argparse
import json
import math
import os
from collections import defaultdict
from pathlib import Path

import numpy as np

from util import create_pairs


CMU_FEATURES_PATH = "datasets/cmu/features.json"
KEYRECS_FIXED_FEATURES_PATH = "datasets/keyrecs/processed/fixed-text.features.json"
DEFAULT_FEATURE_PATHS = [CMU_FEATURES_PATH, KEYRECS_FIXED_FEATURES_PATH]
FEATURES_PATH = ",".join(DEFAULT_FEATURE_PATHS)
MODEL_PATH = "cadence_base_model.keras"
_TF = None


def tensorflow():
    global _TF
    if _TF is None:
        import tensorflow as tf

        _TF = tf
    return _TF


def configure_tensorflow(device="gpu", memory_growth=True, mixed_precision=False):
    if device == "cpu":
        os.environ["CUDA_VISIBLE_DEVICES"] = "-1"

    tf = tensorflow()
    gpus = tf.config.list_physical_devices("GPU")

    if device == "gpu" and not gpus:
        raise RuntimeError(
            "GPU training requested, but TensorFlow does not see any GPU. "
            "Install a CUDA-enabled TensorFlow build or run with --device cpu."
        )

    if device in {"gpu", "auto"} and gpus and memory_growth:
        for gpu in gpus:
            try:
                tf.config.experimental.set_memory_growth(gpu, True)
            except RuntimeError:
                # TensorFlow raises if devices have already been initialized.
                pass

    if mixed_precision and device in {"gpu", "auto"} and gpus:
        tf.keras.mixed_precision.set_global_policy("mixed_float16")

    logical_gpus = tf.config.list_logical_devices("GPU")
    print(f"TensorFlow: {tf.__version__}")
    print(f"GPU devices: {[device.name for device in gpus]}")
    print(f"Logical GPU devices: {[device.name for device in logical_gpus]}")
    if device == "cpu" or not gpus:
        print("Training device: CPU")
    else:
        print("Training device: GPU")
    return tf


def keystroke_to_vector(keystroke):
    return [
        float(keystroke["hold_time"]),
        float(keystroke["flight_time"] or 0.0),
        float(keystroke["down_down"] or 0.0),
    ]


def normalize_dataset_id(value):
    normalized = "".join(
        character if character.isalnum() else "_"
        for character in str(value).strip().lower()
    ).strip("_")
    return normalized or "dataset"


def dataset_id_for_path(path):
    path = Path(path)
    parts = path.parts
    if "datasets" in parts:
        index = parts.index("datasets")
        if index + 1 < len(parts):
            return normalize_dataset_id(parts[index + 1])
    return normalize_dataset_id(path.stem)


def expand_feature_paths(paths):
    values = DEFAULT_FEATURE_PATHS if paths is None else paths
    if isinstance(values, (str, Path)):
        values = [values]

    expanded = []
    for value in values:
        for item in str(value).split(","):
            item = item.strip()
            if item:
                expanded.append(item)

    if not expanded:
        raise ValueError("at least one feature path is required")
    return expanded


def load_feature_file(path):
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
    metas = []
    for item in raw_data:
        meta = item.get("meta", {})
        user_id = meta.get("user_id")
        if not user_id:
            continue

        keystrokes = item.get("keystrokes", [])
        if not keystrokes:
            continue

        samples.append(np.asarray([keystroke_to_vector(k) for k in keystrokes]))
        metas.append(dict(meta))

    if not samples:
        raise ValueError(f"No usable training samples found in {path}")

    return samples, metas


def load_feature_data(paths=FEATURES_PATH):
    feature_paths = expand_feature_paths(paths)
    namespace_user_ids = len(feature_paths) > 1

    samples = []
    user_ids = []
    metas = []
    for path in feature_paths:
        dataset_id = dataset_id_for_path(path)
        file_samples, file_metas = load_feature_file(path)
        for sample, meta in zip(file_samples, file_metas):
            original_user_id = str(meta["user_id"])
            meta["dataset_id"] = dataset_id
            meta["dataset_path"] = str(path)
            meta["original_user_id"] = original_user_id
            samples.append(sample)
            user_ids.append(
                f"{dataset_id}:{original_user_id}"
                if namespace_user_ids
                else original_user_id
            )
            metas.append(meta)

    return samples, np.asarray(user_ids), metas


def source_counts(metas):
    counts = defaultdict(int)
    for meta in metas:
        counts[meta.get("dataset_id", "unknown")] += 1
    return dict(sorted(counts.items()))


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
    tf = tensorflow()
    return tf.keras.preprocessing.sequence.pad_sequences(
        samples, dtype="float32", padding="post"
    )


def user_index_map(user_ids, indices):
    by_user = defaultdict(list)
    for index in indices:
        by_user[user_ids[index]].append(index)
    return by_user


def dataset_index_map(metas, indices):
    by_dataset = defaultdict(list)
    for index in indices:
        by_dataset[metas[index].get("dataset_id", "default")].append(index)
    return by_dataset


def choose_indices(rng, candidates, limit):
    candidates = np.asarray(candidates)
    if limit is None or limit <= 0 or len(candidates) <= limit:
        return candidates.tolist()
    return rng.choice(candidates, size=limit, replace=False).tolist()


def create_grouped_pairs(
    samples,
    user_ids,
    metas,
    indices,
    positives_per_sample=1,
    negatives_per_sample=1,
    seed=42,
):
    left_parts = []
    right_parts = []
    label_parts = []

    for offset, (dataset_id, dataset_indices) in enumerate(
        sorted(dataset_index_map(metas, indices).items())
    ):
        dataset_users = {user_ids[index] for index in dataset_indices}
        if len(dataset_users) < 2:
            continue

        left, right, labels = create_pairs(
            samples,
            user_ids,
            indices=np.asarray(dataset_indices),
            positives_per_sample=positives_per_sample,
            negatives_per_sample=negatives_per_sample,
            seed=seed + offset,
        )
        left_parts.append(left)
        right_parts.append(right)
        label_parts.append(labels)

    if not label_parts:
        raise ValueError("no training pairs were created")

    return (
        np.concatenate(left_parts, axis=0),
        np.concatenate(right_parts, axis=0),
        np.concatenate(label_parts, axis=0),
    )


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


def create_grouped_login_attempt_pairs(
    samples,
    user_ids,
    metas,
    enrollment_indices,
    probe_indices,
    enrollment_samples_per_user=10,
    max_probes_per_user=None,
    impostor_attempts_per_user=100,
    seed=42,
):
    enrollment_by_dataset = dataset_index_map(metas, enrollment_indices)
    probe_by_dataset = dataset_index_map(metas, probe_indices)
    left_parts = []
    right_parts = []
    pair_label_parts = []
    attempt_label_parts = []
    attempt_ranges = []
    pair_offset = 0

    for offset, dataset_id in enumerate(
        sorted(set(enrollment_by_dataset) & set(probe_by_dataset))
    ):
        try:
            (
                left,
                right,
                pair_labels,
                attempt_labels,
                ranges,
            ) = create_login_attempt_pairs(
                samples,
                user_ids,
                enrollment_indices=np.asarray(enrollment_by_dataset[dataset_id]),
                probe_indices=np.asarray(probe_by_dataset[dataset_id]),
                enrollment_samples_per_user=enrollment_samples_per_user,
                max_probes_per_user=max_probes_per_user,
                impostor_attempts_per_user=impostor_attempts_per_user,
                seed=seed + offset,
            )
        except ValueError:
            continue

        left_parts.append(left)
        right_parts.append(right)
        pair_label_parts.append(pair_labels)
        attempt_label_parts.append(attempt_labels)
        for start, end in ranges:
            attempt_ranges.append((start + pair_offset, end + pair_offset))
        pair_offset += len(pair_labels)

    if not pair_label_parts:
        raise ValueError("no validation attempts were created")

    return (
        np.concatenate(left_parts, axis=0),
        np.concatenate(right_parts, axis=0),
        np.concatenate(pair_label_parts, axis=0),
        np.concatenate(attempt_label_parts, axis=0),
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
    parser.add_argument(
        "--features-path",
        action="append",
        default=None,
        help=(
            "Feature JSON path. May be repeated or comma-separated. "
            f"Default: {FEATURES_PATH}"
        ),
    )
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
    parser.add_argument(
        "--device",
        choices=["gpu", "auto", "cpu"],
        default="gpu",
        help="Training device. Default requires a TensorFlow-visible GPU.",
    )
    parser.add_argument(
        "--no-gpu-memory-growth",
        action="store_true",
        help="Do not enable TensorFlow GPU memory growth.",
    )
    parser.add_argument(
        "--mixed-precision",
        action="store_true",
        help="Enable TensorFlow mixed_float16 policy for GPU training.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    tf = configure_tensorflow(
        device=args.device,
        memory_growth=not args.no_gpu_memory_growth,
        mixed_precision=args.mixed_precision,
    )
    from model import build_cadence_model

    feature_paths = expand_feature_paths(args.features_path)

    samples, user_ids, metas = load_feature_data(feature_paths)
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
            "feature_order": ["hold_time", "flight_time", "down_down"],
        }

    padded_samples = pad_samples(normalized_samples)
    left_X, right_X, pair_labels = create_grouped_pairs(
        padded_samples,
        user_ids,
        metas,
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
    ) = create_grouped_login_attempt_pairs(
        padded_samples,
        user_ids,
        metas,
        enrollment_indices=train_indices,
        probe_indices=validation_indices,
        enrollment_samples_per_user=args.eval_enrollment_samples,
        max_probes_per_user=args.eval_max_probes_per_user,
        impostor_attempts_per_user=args.eval_impostor_attempts_per_user,
        seed=args.pair_seed + 1,
    )

    model = build_cadence_model(input_shape=(padded_samples.shape[1], 3))
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
        "features_path": feature_paths[0] if len(feature_paths) == 1 else feature_paths,
        "features_paths": feature_paths,
        "model_path": args.model_path,
        "training": {
            "device": args.device,
            "tensorflow_version": tf.__version__,
            "gpu_devices": [
                device.name for device in tf.config.list_physical_devices("GPU")
            ],
            "gpu_memory_growth": not args.no_gpu_memory_growth,
            "mixed_precision": args.mixed_precision,
        },
        "samples": len(samples),
        "users": int(len(set(user_ids.tolist()))),
        "source_counts": source_counts(metas),
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
            "negative_scope": "within_dataset",
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
