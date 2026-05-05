from collections import defaultdict

import numpy as np


def create_pairs(
    samples,
    user_ids,
    indices=None,
    positives_per_sample=1,
    negatives_per_sample=1,
    seed=42,
):
    samples = np.asarray(samples, dtype="float32")
    user_ids = np.asarray(user_ids)

    if len(samples) != len(user_ids):
        raise ValueError("samples and user_ids must have the same length")

    if indices is None:
        indices = np.arange(len(samples))
    else:
        indices = np.asarray(indices)

    by_user = defaultdict(list)
    for index in indices:
        user_id = user_ids[index]
        by_user[user_id].append(index)

    users = sorted(by_user.keys())
    if len(users) < 2:
        raise ValueError("at least two users are required to create negative pairs")

    rng = np.random.default_rng(seed)
    positives_per_sample = max(1, positives_per_sample)
    negatives_per_sample = max(1, negatives_per_sample)
    left = []
    right = []
    labels = []

    for user_id in users:
        user_indices = by_user[user_id]
        if len(user_indices) < 2:
            continue

        other_users = [candidate for candidate in users if candidate != user_id]
        for sample_index in user_indices:
            positive_candidates = [
                candidate for candidate in user_indices if candidate != sample_index
            ]

            for _ in range(positives_per_sample):
                positive_index = rng.choice(positive_candidates)

                left.append(samples[sample_index])
                right.append(samples[positive_index])
                labels.append(1.0)

            for _ in range(negatives_per_sample):
                negative_user = rng.choice(other_users)
                negative_index = rng.choice(by_user[negative_user])

                left.append(samples[sample_index])
                right.append(samples[negative_index])
                labels.append(0.0)

    if not labels:
        raise ValueError("no pairs were created; each user needs at least two samples")

    return (
        np.asarray(left, dtype="float32"),
        np.asarray(right, dtype="float32"),
        np.asarray(labels, dtype="float32"),
    )
