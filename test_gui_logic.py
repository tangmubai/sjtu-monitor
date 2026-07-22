import unittest
import errno
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import apppaths
import config
from gui_backend import (
    availability,
    course_summary,
    merge_priority_ids,
    normalized_rating,
    parse_swap_records,
    teacher_names,
)
from bootstrap import xsxx_query_variants


class MergePriorityIdsTests(unittest.TestCase):
    def test_new_target_is_inserted_before_currently_chosen_course(self):
        self.assertEqual(
            merge_priority_ids(["target-a", "held"], ["target-b"], {"held"}),
            ["target-a", "target-b", "held"],
        )

    def test_duplicate_addition_preserves_existing_priority(self):
        self.assertEqual(
            merge_priority_ids(["a", "b", "held"], ["b", "a"], {"held"}),
            ["a", "b", "held"],
        )

    def test_all_currently_chosen_courses_stay_at_bottom(self):
        self.assertEqual(
            merge_priority_ids(["held-a", "target"], ["held-b"], {"held-a", "held-b"}),
            ["target", "held-a", "held-b"],
        )


class CoursePresentationTests(unittest.TestCase):
    def test_teacher_names_extracts_names_from_jsxx(self):
        self.assertEqual(
            teacher_names({"jsxx": "1/张老师/教授;2/李老师/副教授"}),
            "张老师、李老师",
        )

    def test_course_summary_uses_compact_fields(self):
        summary = course_summary(
            {
                "kch": "TEST100",
                "jsxx": "1/张老师/教授",
                "sksj": "星期一第1-2节",
                "jxdd": "上院100",
            }
        )
        self.assertIn("张老师", summary)
        self.assertIn("星期一", summary)
        self.assertIn("上院100", summary)

    def test_availability_from_capacity(self):
        self.assertEqual(availability({"jxbxzrs": "9", "jxbrl": "10"}), "open")
        self.assertEqual(availability({"jxbxzrs": "10", "jxbrl": "10"}), "full")
        self.assertEqual(availability({}), "unknown")

    def test_nested_rating_is_normalized(self):
        rating = normalized_rating(
            {
                "rating": {
                    "teacher": "张老师",
                    "semester": "2025-2026-3",
                    "rating": {"score": 9.1, "count": 12},
                }
            }
        )
        self.assertEqual(rating["status"], "rated")
        self.assertEqual(rating["score"], 9.1)
        self.assertEqual(rating["count"], 12)

    def test_zero_rating_values_are_not_dropped(self):
        rating = normalized_rating(
            {"rating": {"rating": {"score": 0, "count": 0}}}
        )
        self.assertEqual(rating["status"], "empty")
        self.assertEqual(rating["score"], 0)
        self.assertEqual(rating["count"], 0)

    def test_rating_text_uses_one_decimal_place(self):
        from gui_backend import rating_text

        self.assertEqual(
            rating_text(
                {
                    "rating": {
                        "rating": {"score": 9.12345, "count": 12},
                    }
                }
            ),
            "9.1 / 12评",
        )

    def test_empty_not_found_and_failed_ratings(self):
        self.assertEqual(
            normalized_rating({"rating": {"rating": None}})["status"], "empty"
        )
        self.assertEqual(
            normalized_rating({"rating_error": {"reason": "not_found"}})["status"],
            "not_found",
        )
        self.assertEqual(
            normalized_rating(
                {"rating_error": {"reason": "fetch_failed", "message": "timeout"}}
            )["status"],
            "failed",
        )


class SwapLogTests(unittest.TestCase):
    def test_parse_swap_records(self):
        records = parse_swap_records(
            [
                '2026-07-09 {"kind":"swap_result","group":"Test","ok":true}',
                "not json",
            ]
        )
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["group"], "Test")


class BootstrapIdentityTests(unittest.TestCase):
    def test_xsxx_query_variants_fall_back_to_config_term(self):
        variants = xsxx_query_variants(("", ""))
        self.assertEqual(variants[0], {"xnm": "", "xqm": "", "kzlx": "ck"})
        self.assertIn({"xnm": "2026", "xqm": "3", "kzlx": "ck"}, variants)

    def test_xsxx_query_variants_deduplicate_term_hint(self):
        variants = xsxx_query_variants(("2026", "3"))
        self.assertEqual(
            variants.count({"xnm": "2026", "xqm": "3", "kzlx": "ck"}),
            1,
        )


class ReleaseInitializationTests(unittest.TestCase):
    def test_release_defaults_have_no_builtin_courses_or_groups(self):
        self.assertEqual(config.default_settings()["courses"], {})
        self.assertEqual(config.default_priority_groups(), {})
        self.assertFalse(config.default_settings()["onboarding"]["completed"])

    def test_prerelease_data_is_cleared_only_once(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ("user_settings.json", "catalog.json", "state.json", ".env", "secrets.local.json"):
                (root / name).write_text("test", "utf-8")
            (root / "captcha_debug").mkdir()
            (root / "captcha_debug" / "captcha.png").write_bytes(b"test")

            with patch.dict(os.environ, {"SJTU_MONITOR_RELEASE": "1"}):
                apppaths.prepare_release_data(root)
                self.assertTrue((root / apppaths.RELEASE_DATA_MARKER).exists())
                self.assertFalse((root / "catalog.json").exists())
                self.assertFalse((root / "secrets.local.json").exists())
                self.assertFalse((root / "captcha_debug").exists())

                (root / "state.json").write_text("keep", "utf-8")
                apppaths.prepare_release_data(root)
                self.assertEqual((root / "state.json").read_text("utf-8"), "keep")


class CrossDeviceReplaceTests(unittest.TestCase):
    """os.replace 在被迁到别的盘/被虚拟化的 %APPDATA% 上会抛 WinError 17
    (ERROR_NOT_SAME_DEVICE) / POSIX EXDEV。replace_atomic 必须退化为复制,
    prepare_release_data 更不能因此在后端启动时崩溃。"""

    @staticmethod
    def _winerror(code):
        err = OSError("系统无法将文件移到不同的磁盘驱动器。")
        err.winerror = code
        return err

    def test_replace_atomic_falls_back_on_cross_device_error(self):
        for label, exc in (
            ("winerror17", self._winerror(17)),
            ("exdev", OSError(errno.EXDEV, "cross-device link")),
        ):
            with self.subTest(label=label):
                with tempfile.TemporaryDirectory() as temp:
                    root = Path(temp)
                    src = root / "state.json.tmp"
                    src.write_text("payload", "utf-8")
                    dst = root / "state.json"
                    with patch("apppaths.os.replace", side_effect=exc):
                        apppaths.replace_atomic(src, dst)
                    self.assertEqual(dst.read_text("utf-8"), "payload")
                    self.assertFalse(src.exists())

    def test_replace_atomic_reraises_unrelated_oserror(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            src = root / "x.tmp"
            src.write_text("payload", "utf-8")
            with patch("apppaths.os.replace", side_effect=OSError(errno.EACCES, "denied")):
                with self.assertRaises(OSError):
                    apppaths.replace_atomic(src, root / "x.json")

    def test_prepare_release_data_survives_broken_filesystem(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "catalog.json").write_text("old", "utf-8")
            with patch.dict(os.environ, {"SJTU_MONITOR_RELEASE": "1"}), \
                 patch("apppaths.os.replace", side_effect=self._winerror(17)), \
                 patch("apppaths.shutil.copyfile", side_effect=OSError(errno.EIO, "io error")):
                # 即使原子替换和复制兜底都失败,也绝不能抛出(否则后端启动即崩溃)。
                result = apppaths.prepare_release_data(root)
            self.assertEqual(result, root)
            self.assertFalse((root / "catalog.json").exists())


if __name__ == "__main__":
    unittest.main()
