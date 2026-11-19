import unittest
from datetime import datetime, timezone

from app.features.stats import period_start


class ListeningStatsTests(unittest.TestCase):
    def test_period_start_boundaries(self):
        now = datetime(2026, 11, 2, 18, 30, tzinfo=timezone.utc)

        self.assertEqual(period_start("today", now), "2026-11-02T00:00:00+00:00")
        self.assertEqual(period_start("week", now), "2026-11-02T00:00:00+00:00")
        self.assertEqual(period_start("month", now), "2026-11-01T00:00:00+00:00")
        self.assertEqual(period_start("year", now), "2026-01-01T00:00:00+00:00")
        self.assertIsNone(period_start("all_time", now))

    def test_unknown_period_is_reported(self):
        with self.assertRaisesRegex(ValueError, "Unknown listening statistics period"):
            period_start("forever")


if __name__ == "__main__":
    unittest.main()
