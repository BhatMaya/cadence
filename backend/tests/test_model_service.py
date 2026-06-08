import unittest

import numpy as np

from backend.model_service import CadenceModelService


class FakeResult:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, rows):
        self.rows = rows
        self.filters = []
        self.limit_value = None

    def select(self, *_args):
        return self

    def eq(self, column, value):
        self.filters.append(("eq", column, value))
        return self

    def neq(self, column, value):
        self.filters.append(("neq", column, value))
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, value):
        self.limit_value = value
        return self

    def execute(self):
        rows = list(self.rows)
        for operator, column, value in self.filters:
            if operator == "eq":
                rows = [row for row in rows if row.get(column) == value]
            elif operator == "neq":
                rows = [row for row in rows if row.get(column) != value]
        if self.limit_value is not None:
            rows = rows[: self.limit_value]
        return FakeResult(rows)


class FakeSupabase:
    def __init__(self, rows):
        self.rows = rows

    def table(self, name):
        if name != "login_attempts":
            raise AssertionError(f"unexpected table: {name}")
        return FakeQuery(self.rows)


class RecordingChunkService(CadenceModelService):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._mean = np.zeros(3, dtype="float32")
        self._std = np.ones(3, dtype="float32")
        self._feature_order = ["hold_time", "flight_time", "down_down"]
        self.calls = []

    def score_sample_pairs(self, current_sample, enrollment_samples):
        self.calls.append(
            (
                np.asarray(current_sample).copy(),
                [np.asarray(sample).copy() for sample in enrollment_samples],
            )
        )
        return float(len(current_sample)) / 10.0


def raw_keystrokes(length):
    keystrokes = []
    for index in range(length):
        keystrokes.append(
            {
                "hold_time": 80 + index % 5,
                "flight_time": None if index == 0 else 40 + index % 7,
                "down_down": None if index == 0 else 120 + index % 9,
                "up_up": None if index == 0 else 120 + index % 11,
            }
        )
    return {"keystrokes": keystrokes}


class CadenceModelServiceTest(unittest.TestCase):
    def test_loads_base_weights_for_multiple_password_lengths(self):
        service = CadenceModelService()

        for length in (6, 11, 14):
            model = service.model_for_length(length)
            self.assertEqual(model.input_shape[0][1:], (length, 3))

        self.assertEqual(service.health()["loaded_lengths"], [6, 11, 14])

    def test_scores_login_attempt_against_successful_enrollment_samples(self):
        service = CadenceModelService()
        raw_data = raw_keystrokes(8)
        supabase = FakeSupabase(
            [
                {
                    "login_attempt_id": "old-success",
                    "username": "alice",
                    "successful_login": True,
                    "raw_data": raw_data,
                },
                {
                    "login_attempt_id": "old-failed",
                    "username": "alice",
                    "successful_login": False,
                    "raw_data": raw_keystrokes(8),
                },
            ]
        )

        score = service.score_login_attempt(
            supabase,
            "alice",
            raw_data,
            login_attempt_id="current",
        )

        self.assertIsInstance(score, float)
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 1.0)
        self.assertAlmostEqual(score, 1.0, places=5)

    def test_chunks_long_samples_before_scoring(self):
        service = RecordingChunkService(chunk_length=4)
        current = np.asarray(
            [[80, 0, 0], [81, 41, 121], [82, 42, 122],
             [83, 43, 123], [84, 44, 124], [85, 45, 125]],
            dtype="float32",
        )
        enrolled = [current.copy(), current.copy()]

        score = service.score_against_enrollment(current, enrolled)

        self.assertEqual(score, 0.4)
        self.assertEqual(len(service.calls), 2)
        self.assertEqual([call[0].shape[0] for call in service.calls], [4, 4])
        np.testing.assert_allclose(service.calls[0][0], current[0:4])
        np.testing.assert_allclose(service.calls[1][0][0], [82, 0, 0])

    def test_short_samples_use_whole_sample_scoring(self):
        service = RecordingChunkService(chunk_length=11)
        current = np.asarray([[80, 0, 0], [81, 41, 121]], dtype="float32")

        score = service.score_against_enrollment(current, [current.copy()])

        self.assertEqual(score, 0.2)
        self.assertEqual(len(service.calls), 1)
        self.assertEqual(service.calls[0][0].shape[0], 2)

    def test_ignores_enrollment_samples_with_different_lengths(self):
        service = RecordingChunkService(chunk_length=11)
        current = np.asarray([[80, 0, 0], [81, 41, 121]], dtype="float32")
        wrong_length = np.asarray([[80, 0, 0]], dtype="float32")

        with self.assertRaisesRegex(ValueError, "match current sample length"):
            service.score_against_enrollment(current, [wrong_length])

        self.assertEqual(service.calls, [])

    def test_chunk_ranges_cover_tail_without_padding(self):
        service = CadenceModelService(chunk_length=11)

        self.assertEqual(service.chunk_ranges(8), [(0, 8)])
        self.assertEqual(service.chunk_ranges(16), [(0, 11), (5, 16)])
        self.assertEqual(
            service.chunk_ranges(22),
            [(0, 11), (5, 16), (10, 21), (11, 22)],
        )

    def test_converts_capture_events_to_timing_features(self):
        service = CadenceModelService()
        sample = service.raw_data_to_sample(
            {
                "events": [
                    {"type": "down", "code": "KeyA", "t": 10},
                    {"type": "up", "code": "KeyA", "t": 60},
                    {"type": "down", "code": "KeyB", "t": 90},
                    {"type": "up", "code": "KeyB", "t": 150},
                ]
            }
        )

        np.testing.assert_allclose(
            sample,
            np.asarray(
                [
                    [50.0, 0.0, 0.0],
                    [60.0, 30.0, 80.0],
                ],
                dtype="float32",
            ),
        )

    def test_returns_none_without_prior_successful_enrollment(self):
        service = CadenceModelService()
        score = service.score_login_attempt(
            FakeSupabase([]),
            "alice",
            raw_keystrokes(8),
            login_attempt_id="current",
        )

        self.assertIsNone(score)


if __name__ == "__main__":
    unittest.main()
