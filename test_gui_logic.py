import unittest

from gui import _layout_mode, _merge_priority_ids, _metric_columns


class LayoutModeTests(unittest.TestCase):
    def test_breakpoints(self):
        self.assertEqual(_layout_mode(1320), "wide")
        self.assertEqual(_layout_mode(1200), "wide")
        self.assertEqual(_layout_mode(1024), "medium")
        self.assertEqual(_layout_mode(900), "medium")
        self.assertEqual(_layout_mode(899), "narrow")
        self.assertEqual(_layout_mode(760), "narrow")
        self.assertEqual(_metric_columns("wide"), 6)
        self.assertEqual(_metric_columns("medium"), 3)
        self.assertEqual(_metric_columns("narrow"), 2)


class MergePriorityIdsTests(unittest.TestCase):
    def test_new_target_is_inserted_before_currently_chosen_course(self):
        self.assertEqual(
            _merge_priority_ids(["target-a", "held"], ["target-b"], {"held"}),
            ["target-a", "target-b", "held"],
        )

    def test_duplicate_addition_preserves_existing_priority(self):
        self.assertEqual(
            _merge_priority_ids(["a", "b", "held"], ["b", "a"], {"held"}),
            ["a", "b", "held"],
        )

    def test_all_currently_chosen_courses_stay_at_bottom(self):
        self.assertEqual(
            _merge_priority_ids(["held-a", "target"], ["held-b"], {"held-a", "held-b"}),
            ["target", "held-a", "held-b"],
        )


if __name__ == "__main__":
    unittest.main()
