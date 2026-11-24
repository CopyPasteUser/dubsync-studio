"""Cache admission must have the same contract as a persistent read."""
import unittest

from dubsync.core.sample_validation import validate_samples


class SampleValidationTests(unittest.TestCase):
    def test_round_trip_accepts_tuples_and_json_lists(self):
        good = [(0, 0), (1, 1), (2, 2), (3, 2**64 - 1)]
        self.assertEqual(validate_samples(good, 4, 1), good)
        self.assertEqual(validate_samples([list(x) for x in good], 4, 1), good)
        self.assertIsNot(validate_samples(good, 4, 1), good)

    def test_invalid_samples_are_never_admitted(self):
        good = [(0, 0), (1, 1), (2, 2), (3, 3)]
        for invalid in ([], good[:3], good + [(3, 4)], good + [(2, 4)],
                        good + [(float('nan'), 4)], good + [(float('inf'), 4)],
                        good + [(4, -1)], good + [(4, 2**64)],
                        good + [(4, True)], good + [(8, 4)],
                        good + [('4', 4)], good + [(4,)],
                        [(0, 0), (1, 1), (2, 2), (2.5, 3)]):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_samples(invalid, 5, 1)

    def test_invalid_sampling_grid(self):
        for duration, interval in ((0, 1), (4, 0), (4, -1), (float('nan'), 1), (4, float('inf'))):
            with self.subTest(duration=duration, interval=interval), self.assertRaises(ValueError):
                validate_samples([(0, 0), (1, 1), (2, 2), (3, 3)], duration, interval)
