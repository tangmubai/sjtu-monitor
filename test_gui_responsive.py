import unittest

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QBoxLayout

from gui import MonitorWindow


class ResponsiveWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MonitorWindow()

    def tearDown(self):
        self.window.close()
        self.app.processEvents()

    def apply_width(self, width, height=720):
        self.window.resize(width, height)
        self.window.show()
        self.app.processEvents()
        self.window._apply_responsive_layout(width)

    def metric_position(self, key):
        index = self.window.metrics_layout.indexOf(self.window.metrics[key])
        row, column, _row_span, _column_span = (
            self.window.metrics_layout.getItemPosition(index)
        )
        return row, column

    def test_wide_layout(self):
        self.apply_width(1320)
        self.assertEqual(self.window._layout_mode, "wide")
        self.assertEqual(self.window.nav.count(), 6)
        self.assertFalse(hasattr(self.window, "command_output"))
        self.assertTrue(hasattr(self.window, "account_user_edit"))
        self.assertEqual(self.window.course_table.columnCount(), 3)
        self.assertEqual(self.window.sidebar.width(), 220)
        self.assertEqual(
            self.window.course_splitter.orientation(), Qt.Orientation.Horizontal
        )
        self.assertEqual(self.metric_position("swap"), (0, 5))

    def test_medium_layout(self):
        self.apply_width(1024)
        self.assertEqual(self.window._layout_mode, "medium")
        self.assertEqual(self.window.sidebar.width(), 76)
        self.assertEqual(
            self.window.course_splitter.orientation(), Qt.Orientation.Horizontal
        )
        self.assertEqual(self.metric_position("swap"), (1, 2))

    def test_narrow_layout(self):
        self.apply_width(760)
        self.assertEqual(self.window._layout_mode, "narrow")
        self.assertEqual(
            self.window.course_splitter.orientation(), Qt.Orientation.Vertical
        )
        self.assertEqual(
            self.window.overview_actions.direction(),
            QBoxLayout.Direction.TopToBottom,
        )
        self.assertEqual(self.metric_position("swap"), (2, 1))
        self.assertTrue(self.window.course_table.isColumnHidden(2))
        self.assertTrue(self.window.snapshot_table.isColumnHidden(3))

    def test_course_filter_returns_every_matching_class(self):
        rows = [
            {
                "jxb_id": f"J{index}",
                "kcmc": "测试课程",
                "kch": "TEST100",
                "jxbmc": f"TEST100-{index:02d}",
                "jsxx": "1/张老师/教授",
                "sksj": f"星期{index}第1-2节",
                "jxdd": "上院100",
                "availability": "open",
                "category": "任选",
            }
            for index in range(1, 8)
        ]
        self.window._course_rows = rows
        self.window._course_by_id = {row["jxb_id"]: row for row in rows}
        self.window.course_filter.setText("张老师")
        self.window._refresh_course_table()
        self.assertEqual(self.window.course_table.rowCount(), 7)
        self.assertGreaterEqual(self.window.course_table.rowHeight(0), 52)
        self.assertEqual(self.window.course_table.item(0, 0).toolTip(), "")


if __name__ == "__main__":
    unittest.main()
