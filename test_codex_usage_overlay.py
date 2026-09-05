import datetime as dt
import importlib.util
import json
import pathlib
import unittest
from unittest import mock


MODULE_PATH = pathlib.Path(__file__).with_name("codex_usage_overlay.py")
SPEC = importlib.util.spec_from_file_location("codex_usage_overlay", MODULE_PATH)
overlay = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(overlay)


class UsageParsingTests(unittest.TestCase):
    def test_reserve_view_prioritizes_exhausted_five_hour_window(self):
        five = {"used_percent": 100, "resets_at": 1788593770}
        week = {"used_percent": 100, "resets_at": 1789007110}
        data = {"five_hour": five, "weekly": week, "reserve": {"used_percent": 24}}
        self.assertIs(overlay.exhausted_window(data), five)
        self.assertEqual(overlay.usage_fields(data), ("备用 76%", "常规恢复"))
        self.assertEqual(overlay.usage_reset_fields(data)[1], dt.datetime.fromtimestamp(five["resets_at"]).strftime("%m/%d %H:%M"))

    def test_week_only_exhaustion_and_missing_reserve(self):
        week = {"used_percent": 100, "resets_at": 1789007110}
        data = {"weekly": week}
        self.assertIs(overlay.exhausted_window(data), week)
        self.assertEqual(overlay.usage_fields(data), ("备用 --", "常规恢复"))

    def test_rounded_zero_is_not_exhaustion(self):
        self.assertIsNone(overlay.exhausted_window({"five_hour": {"used_percent": 99.6}}))

    def test_parses_codex_five_hour_and_weekly_windows(self):
        payload = {
            "timestamp": "2026-07-26T07:33:11.381Z",
            "payload": {
                "rate_limits": {
                    "limit_id": "codex",
                    "primary": {
                        "used_percent": 49.0,
                        "window_minutes": 300,
                        "resets_at": 1788593770,
                    },
                    "secondary": {
                        "used_percent": 17.0,
                        "window_minutes": 10080,
                        "resets_at": 1789007110,
                    },
                }
            },
        }

        parsed = overlay.parse_rate_limit_line(json.dumps(payload))

        self.assertEqual(parsed["five_hour"]["used_percent"], 49.0)
        self.assertEqual(parsed["weekly"]["used_percent"], 17.0)

    def test_ignores_reserve_bucket(self):
        payload = {
            "timestamp": "2026-09-05T02:50:00Z",
            "payload": {
                "rate_limits": {
                    "limit_id": "base_model_inference",
                    "limit_name": "gpt-reserve",
                    "primary": {
                        "used_percent": 0.0,
                        "window_minutes": 10080,
                        "resets_at": 1789181969,
                    },
                }
            },
        }

        self.assertIsNone(overlay.parse_rate_limit_line(json.dumps(payload)))

    def test_ignores_non_rate_limit_lines(self):
        self.assertIsNone(overlay.parse_rate_limit_line('{"type":"message"}'))

    def test_formats_both_remaining_windows(self):
        data = {
            "five_hour": {
                "used_percent": 49.0,
                "window_minutes": 300,
                "resets_at": dt.datetime(2026, 9, 5, 15, 36).timestamp(),
            },
            "weekly": {
                "used_percent": 17.0,
                "window_minutes": 10080,
                "resets_at": dt.datetime(2026, 9, 10, 10, 25).timestamp(),
            },
        }

        self.assertEqual(overlay.usage_fields(data), ("5小时 51%", "本周 83%"))

    def test_prefers_codex_bucket_from_live_response(self):
        result = {
            "rateLimits": {
                "limitId": "base_model_inference",
                "primary": {
                    "usedPercent": 0.0,
                    "windowDurationMins": 10080,
                    "resetsAt": 1789181969,
                },
            },
            "rateLimitsByLimitId": {
                "codex": {
                    "limitId": "codex",
                    "primary": {
                        "usedPercent": 49.0,
                        "windowDurationMins": 300,
                        "resetsAt": 1788593770,
                    },
                    "secondary": {
                        "usedPercent": 17.0,
                        "windowDurationMins": 10080,
                        "resetsAt": 1789007110,
                    },
                },
                "base_model_inference": {
                    "limitId": "base_model_inference",
                    "primary": {
                        "usedPercent": 0.0,
                        "windowDurationMins": 10080,
                        "resetsAt": 1789181969,
                    },
                },
            },
        }

        parsed = overlay.normalize_live_rate_limits(result)

        self.assertEqual(overlay.usage_fields(parsed), ("5小时 51%", "本周 83%"))
        self.assertEqual(parsed["source"], "account/rateLimits/read")

    def test_manual_reset_replaces_previous_usage_cycle(self):
        before = {
            "timestamp": "2026-07-26T13:03:47.593Z",
            "five_hour": {
                "used_percent": 57.0,
                "window_minutes": 300,
                "resets_at": 1785640176,
            },
            "weekly": {
                "used_percent": 23.0,
                "window_minutes": 10080,
                "resets_at": 1785640176,
            },
        }
        after = {
            "timestamp": "2026-07-27T11:50:36.270Z",
            "five_hour": {
                "used_percent": 0.0,
                "window_minutes": 300,
                "resets_at": 1785755708,
            },
            "weekly": {
                "used_percent": 23.0,
                "window_minutes": 10080,
                "resets_at": 1785640176,
            },
        }

        self.assertNotEqual(
            overlay.rate_limit_snapshot(before),
            overlay.rate_limit_snapshot(after),
        )
        self.assertEqual(overlay.remaining_percent(before["five_hour"]), 43)
        self.assertEqual(overlay.remaining_percent(after["five_hour"]), 100)


class InteractionTests(unittest.TestCase):
    def setUp(self):
        self.rect = overlay.RECT(100, 100, 900, 700)

    def test_profile_click_opens_menu(self):
        self.assertTrue(
            overlay.menu_state_after_click(False, 130, 675, self.rect)
        )

    def test_second_profile_click_closes_menu(self):
        self.assertFalse(
            overlay.menu_state_after_click(True, 130, 675, self.rect)
        )

    def test_click_inside_menu_keeps_it_open(self):
        self.assertTrue(
            overlay.menu_state_after_click(True, 130, 500, self.rect)
        )

    def test_click_outside_menu_closes_it(self):
        self.assertFalse(
            overlay.menu_state_after_click(True, 600, 500, self.rect)
        )


class ThemeTests(unittest.TestCase):
    def test_blends_light_sidebar_background(self):
        self.assertEqual(overlay._blend("#ffffff", "#0d0d0d", 0.03), "#f8f8f8")


if __name__ == "__main__":
    unittest.main()
