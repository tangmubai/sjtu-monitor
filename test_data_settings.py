import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dotenv import dotenv_values

import bootstrap
import config
import notifier
from gui import (
    _course_detail_text,
    _course_search_text,
    _course_summary,
    _course_title,
    _parse_swap_records,
    _split_info_lines,
    _teacher_details,
)


class CatalogSanitizingTests(unittest.TestCase):
    def test_catalog_keeps_availability_but_drops_counts(self):
        row = bootstrap._slim_class(
            {
                "jxb_id": "A",
                "jxbmc": "课程-01",
                "jxbxzrs": "9",
                "yxzrs": "9",
                "jxbrl": "10",
            }
        )
        self.assertEqual(row["availability"], "open")
        self.assertNotIn("jxbxzrs", row)
        self.assertNotIn("yxzrs", row)
        self.assertNotIn("jxbrl", row)

    def test_unknown_when_capacity_is_missing(self):
        self.assertEqual(
            bootstrap.availability_from_row({"jxbxzrs": "9"}), "unknown"
        )


class CoursePresentationTests(unittest.TestCase):
    def setUp(self):
        self.course = {
            "jxb_id": "J1",
            "jxbmc": "(2025-2026-3)-NIS1336-03",
            "kch": "NIS1336",
            "kcmc": "计算机编程实践",
            "jsxx": "64630/马融/助理工程师;10444/陈雨亭/研究员",
            "sksj": "星期二第7-10节{1-4周}<br/>星期五第7-10节{1-4周}",
            "jxdd": "上院111<br/>上院111",
            "availability": "open",
            "category": "任选",
        }

    def test_teacher_and_html_line_parsing(self):
        self.assertEqual(
            _teacher_details(self.course),
            [("马融", "助理工程师"), ("陈雨亭", "研究员")],
        )
        self.assertEqual(
            _split_info_lines(self.course["sksj"]),
            ["星期二第7-10节{1-4周}", "星期五第7-10节{1-4周}"],
        )
        self.assertEqual(_split_info_lines("--<br/>不排教室"), [])

    def test_title_summary_and_detail_are_complete(self):
        self.assertEqual(
            _course_title(self.course), "计算机编程实践 · 马融、陈雨亭"
        )
        summary = _course_summary(self.course)
        self.assertIn("另 1 个时段", summary)
        self.assertIn("NIS1336", summary)
        self.assertIn("03班", summary)
        detail = _course_detail_text(self.course, "编程")
        self.assertIn("马融（助理工程师）", detail)
        self.assertIn("星期五第7-10节", detail)
        self.assertIn("教学班 ID：J1", detail)

    def test_same_teacher_different_time_has_distinct_summary(self):
        other = {**self.course, "sksj": "星期一第1-2节{1-4周}", "jxbmc": "X-04"}
        self.assertEqual(_course_title(self.course), _course_title(other))
        self.assertNotEqual(_course_summary(self.course), _course_summary(other))

    def test_search_text_includes_teacher_time_place_and_ids(self):
        haystack = _course_search_text(self.course)
        for value in ("马融", "星期五", "上院111", "nis1336", "j1"):
            self.assertIn(value.casefold(), haystack)


class EnvironmentSettingsTests(unittest.TestCase):
    def test_atomic_update_preserves_unknown_keys_and_quotes_secrets(self):
        original = {
            key: getattr(config, key)
            for key in ("JACCOUNT_PASS", "POLL_MIN", "POLL_MAX")
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("# keep\nUNKNOWN=value\nPOLL_MIN=60\n", "utf-8")
            try:
                config.save_env_settings(
                    {
                        "JACCOUNT_PASS": "p'a\\ss",
                        "POLL_MIN": "15",
                        "POLL_MAX": "30",
                    },
                    path,
                )
                values = dotenv_values(path)
                self.assertEqual(values["UNKNOWN"], "value")
                self.assertEqual(values["JACCOUNT_PASS"], "p'a\\ss")
                self.assertEqual(values["POLL_MIN"], "15")
                self.assertFalse(path.with_suffix(".env.tmp").exists())
            finally:
                for key, value in original.items():
                    setattr(config, key, value)
                    os.environ[key] = str(value)


class SeatDetailCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.old_catalog = config.CATALOG_FILE
        self.old_details = config.SEAT_DETAILS_FILE
        config.CATALOG_FILE = root / "catalog.json"
        config.SEAT_DETAILS_FILE = root / "seat_details.json"
        config.CATALOG_FILE.write_text(
            json.dumps(
                {
                    "courses": [
                        {
                            "kch": "C1",
                            "kch_id": "KC1",
                            "kklxdm": "01",
                            "endpoint": "zzxk",
                            "classes": [{"jxb_id": "J1"}],
                        }
                    ]
                }
            ),
            "utf-8",
        )

    def tearDown(self):
        config.CATALOG_FILE = self.old_catalog
        config.SEAT_DETAILS_FILE = self.old_details
        self.temp.cleanup()

    def test_successful_refresh_writes_plan_only_counts(self):
        with patch.object(
            bootstrap.zzxk,
            "fetch_seats",
            return_value={"J1": {"jxbxzrs": "3", "jxbrl": "5"}},
        ):
            details = bootstrap.refresh_seat_details(object(), ["J1"])
        self.assertEqual(details["classes"]["J1"]["availability"], "open")
        self.assertEqual(details["classes"]["J1"]["jxbxzrs"], "3")

    def test_failure_preserves_old_cache(self):
        config.SEAT_DETAILS_FILE.write_text(
            json.dumps(
                {
                    "classes": {
                        "J1": {
                            "jxbxzrs": "2",
                            "jxbrl": "5",
                            "availability": "open",
                        }
                    },
                    "errors": {},
                }
            ),
            "utf-8",
        )
        with patch.object(
            bootstrap.zzxk, "fetch_seats", side_effect=RuntimeError("offline")
        ):
            details = bootstrap.refresh_seat_details(object(), ["J1"])
        self.assertEqual(details["classes"]["J1"]["jxbxzrs"], "2")
        self.assertIn("J1", details["errors"])


class LogCompatibilityTests(unittest.TestCase):
    def test_old_and_new_swap_records(self):
        records = _parse_swap_records(
            [
                '2026-01-01T00:00:00 {"kind":"swap_result","ok":true}',
                '2026-01-02T00:00:00 {"kind":"swap_result","ok":false,"dry_run":true}',
                '2026-01-03T00:00:00 {"kind":"changed"}',
            ]
        )
        self.assertEqual(len(records), 2)
        self.assertNotIn("dry_run", records[0])
        self.assertTrue(records[1]["dry_run"])

    def test_disabled_email_does_not_connect(self):
        old = config.EMAIL_ENABLED
        config.EMAIL_ENABLED = False
        try:
            with patch("notifier.smtplib.SMTP_SSL") as smtp:
                notifier._email("subject", ["body"])
                smtp.assert_not_called()
        finally:
            config.EMAIL_ENABLED = old


if __name__ == "__main__":
    unittest.main()
