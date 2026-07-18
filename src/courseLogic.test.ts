import { describe, expect, it } from "vitest";
import type { CourseRow, PriorityGroup } from "./api";
import {
  conflictWarning,
  formatRatingScore,
  parseLogLine,
  scheduleConflict,
  sortCourses,
} from "./courseLogic";

function course(
  jxbId: string,
  title: string,
  score: number | null,
  sksj: string,
): CourseRow {
  return {
    jxb_id: jxbId,
    title,
    class_name: title,
    summary: "",
    detail: "",
    teachers: "",
    schedule: [sksj],
    sksj,
    locations: [],
    search_text: title,
    seat_text: "-",
    availability: "unknown",
    availability_text: "未知",
    chosen: false,
    category: "-",
    rating_text: "",
    rating: {
      status: score === null ? "unknown" : "rated",
      score,
      count: null,
      teacher: null,
      semester: null,
      updated_at: null,
      message: null,
    },
  };
}

describe("course sorting and rating formatting", () => {
  it("formats every numeric score with one decimal place", () => {
    expect(formatRatingScore(9.12345)).toBe("9.1");
    expect(formatRatingScore(8)).toBe("8.0");
  });

  it("sorts filtered rows by name or descending rating", () => {
    const rows = [
      course("2", "大学物理", 8.5, "星期一第1-2节{1-16周}"),
      course("1", "高等数学", 9.2, "星期二第1-2节{1-16周}"),
      course("3", "程序设计", null, "星期三第1-2节{1-16周}"),
    ];
    expect(sortCourses(rows, "name").map((row) => row.jxb_id)).toEqual(["3", "2", "1"]);
    expect(sortCourses(rows, "rating").map((row) => row.jxb_id)).toEqual(["1", "2", "3"]);
  });
});

describe("PySide-compatible schedule conflict checks", () => {
  it("handles week ranges and odd/even weeks", () => {
    expect(
      scheduleConflict(
        "星期一第1-2节{1-16周(单)}",
        "星期一第2-3节{2-16周(双)}",
      ),
    ).toEqual({ conflict: false });
    expect(
      scheduleConflict(
        "星期一第1-2节{1-16周(单)}",
        "星期一第2-3节{1-16周}",
      ),
    ).toMatchObject({ conflict: true, detail: "周一 第2节 (第1周)" });
  });

  it("warns against candidates in other groups and reports unknown data", () => {
    const courses = [
      course("new", "新课程", 9, "星期一第1-2节{1-16周}"),
      course("other", "冲突课程", 8, "星期一第2-3节{1-16周}"),
      course("unknown", "时间未知", 7, "待定"),
    ];
    const groups: PriorityGroup[] = [
      { name: "当前组", is_pe: false, priority: [], held_label: "-", watched_count: 0, fatal: false, members: [] },
      { name: "其他组", is_pe: false, priority: ["other", "unknown"], held_label: "-", watched_count: 2, fatal: false, members: [] },
    ];
    const warning = conflictWarning(["new"], "当前组", groups, courses);
    expect(warning).toContain("确定存在时间冲突");
    expect(warning).toContain("无法判断是否冲突");
  });
});

describe("parseLogLine", () => {
  it("parses python logging lines with level and logger name", () => {
    const line = parseLogLine("monitor", "2026-07-18 14:02:11,123 WARNING [monitor] 查询失败，将重试");
    expect(line.time).toBe("14:02:11");
    expect(line.level).toBe("warn");
    expect(line.source).toBe("monitor");
    expect(line.message).toBe("查询失败，将重试");
  });

  it("formats change records and maps swap failures to error", () => {
    const record = JSON.stringify({
      kind: "swap_result", ok: false, status: "FULL", kcmc: "大学物理", jxbmc: "PHY1262-03",
    });
    const line = parseLogLine("changes", `2026-07-18T09:00:00 ${record}`);
    expect(line.level).toBe("error");
    expect(line.message).toContain("换课失败");
    expect(line.message).toContain("PHY1262-03 大学物理");
  });

  it("maps spot_open records to warn with readable text", () => {
    const record = JSON.stringify({ kind: "spot_open", kcmc: "线性代数", jxbmc: "MA0301-01", msg: "剩余1" });
    const line = parseLogLine("changes", `2026-07-18T09:00:00 ${record}`);
    expect(line.level).toBe("warn");
    expect(line.message).toContain("有空位");
  });

  it("formats field-change records with chinese labels", () => {
    const record = JSON.stringify({ kind: "changed", kcmc: "高数", jxbmc: "MA01", changes: { yxzrs: [28, 29] } });
    const line = parseLogLine("changes", `2026-07-18T09:00:00 ${record}`);
    expect(line.level).toBe("info");
    expect(line.message).toContain("已选 28→29");
  });

  it("classifies runtime heuristics: command echo, exit codes, plain text", () => {
    expect(parseLogLine("monitor", "$ python monitor.py --once", "12:00:00").level).toBe("debug");
    expect(parseLogLine("monitor", "exit=0").level).toBe("info");
    expect(parseLogLine("monitor", "exit=1").level).toBe("error");
    expect(parseLogLine("monitor", "exit=-").level).toBe("warn");
    const plain = parseLogLine("bootstrap", "已保存 catalog.json", "12:00:01");
    expect(plain.level).toBe("info");
    expect(plain.time).toBe("12:00:01");
  });
});
