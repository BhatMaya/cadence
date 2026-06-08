#!/usr/bin/env python3
import argparse
import csv
import json
from pathlib import Path


DEFAULT_INPUT = "datasets/keyrecs/raw/fixed-text.csv"
DEFAULT_OUTPUT = "datasets/keyrecs/processed/fixed-text.features.json"


def parse_args():
    parser = argparse.ArgumentParser(
        description="Convert KeyRecs fixed-text CSV rows to Cadence feature JSON."
    )
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    return parser.parse_args()


def seconds_to_ms(value):
    return float(value) * 1000.0


def mean(values):
    return sum(values) / len(values) if values else 0.0


def std(values):
    if not values:
        return 0.0
    avg = mean(values)
    return (sum((value - avg) ** 2 for value in values) / len(values)) ** 0.5


def split_feature_name(name):
    parts = name.strip().split(".")
    if len(parts) != 3:
        return None
    return parts


def infer_key_sequence(header):
    sequence = []
    hold_indices = []
    for index, name in enumerate(header):
        parsed = split_feature_name(name)
        if not parsed:
            continue
        prefix, key_a, key_b = parsed
        if prefix == "DU" and key_a == key_b:
            sequence.append(key_a)
            hold_indices.append(index)
    return sequence, hold_indices


def feature_index_between(header, start, end, prefix):
    for index in range(start + 1, end):
        parsed = split_feature_name(header[index])
        if parsed and parsed[0] == prefix:
            return index
    raise ValueError(f"missing {prefix} transition between columns {start} and {end}")


def row_to_feature(header, row, sequence, hold_indices):
    keystrokes = []
    for index, key in enumerate(sequence):
        hold = seconds_to_ms(row[hold_indices[index]])
        if index == 0:
            flight = None
            down_down = None
            up_up = None
        else:
            previous_hold_index = hold_indices[index - 1]
            current_hold_index = hold_indices[index]
            down_down = seconds_to_ms(
                row[feature_index_between(header, previous_hold_index, current_hold_index, "DD")]
            )
            flight = seconds_to_ms(
                row[feature_index_between(header, previous_hold_index, current_hold_index, "UD")]
            )
            up_up = seconds_to_ms(
                row[feature_index_between(header, previous_hold_index, current_hold_index, "UU")]
            )

        keystrokes.append(
            {
                "code": key,
                "hold_time": hold,
                "flight_time": flight,
                "down_down": down_down,
                "up_up": up_up,
            }
        )

    holds = [key["hold_time"] for key in keystrokes]
    flights = [
        key["flight_time"] for key in keystrokes if key["flight_time"] is not None
    ]
    down_downs = [
        key["down_down"] for key in keystrokes if key["down_down"] is not None
    ]
    total_duration = seconds_to_ms(row[-1])

    participant = row[0]
    session = int(row[1])
    repetition = int(row[2])
    return {
        "keystrokes": keystrokes,
        "aggregates": {
            "mean_hold": mean(holds),
            "std_hold": std(holds),
            "mean_flight": mean(flights),
            "std_flight": std(flights),
            "mean_down_down": mean(down_downs),
            "std_down_down": std(down_downs),
            "total_duration": total_duration,
            "typing_speed": len(keystrokes) / (total_duration / 1000.0)
            if total_duration > 0
            else 0.0,
            "keystroke_count": len(keystrokes),
        },
        "meta": {
            "session_id": f"keyrecs-fixed-{participant}-session-{session}-rep-{repetition}",
            "source": "keyrecs-fixed-text",
            "password": "".join(sequence),
            "user_id": participant,
            "session_index": session,
            "sample_index": repetition,
            "quality_score": 1.0,
            "flags": [],
            "password_length": len(sequence),
        },
    }


def main():
    args = parse_args()
    input_path = Path(args.input)
    output_path = Path(args.output)

    with input_path.open(newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)
        sequence, hold_indices = infer_key_sequence(header)
        if not sequence:
            raise SystemExit("could not infer fixed-text key sequence")
        features = [
            row_to_feature(header, row, sequence, hold_indices)
            for row in reader
            if row
        ]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(features, indent=2) + "\n", encoding="utf-8")

    users = {feature["meta"]["user_id"] for feature in features}
    print("Converted KeyRecs fixed-text data")
    print(f"  input: {input_path}")
    print(f"  output: {output_path}")
    print(f"  samples: {len(features)}")
    print(f"  users: {len(users)}")
    print(f"  password: {''.join(sequence)}")
    print(f"  password length: {len(sequence)}")


if __name__ == "__main__":
    main()
