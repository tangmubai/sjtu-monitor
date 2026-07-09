import type { CourseRow, PriorityGroup } from "./api";

export type CourseSort = "catalog" | "name" | "rating";

export function formatRatingScore(score: number | null | undefined): string {
  return typeof score === "number" && Number.isFinite(score) ? score.toFixed(1) : "-";
}

export function sortCourses(courses: CourseRow[], sort: CourseSort): CourseRow[] {
  if (sort === "catalog") return courses;
  return [...courses].sort((left, right) => {
    if (sort === "rating") {
      const leftScore = left.rating.status === "rated" ? left.rating.score : null;
      const rightScore = right.rating.status === "rated" ? right.rating.score : null;
      if (leftScore !== rightScore) {
        if (leftScore === null) return 1;
        if (rightScore === null) return -1;
        return rightScore - leftScore;
      }
    }
    return left.title.localeCompare(right.title, "zh-CN", {
      numeric: true,
      sensitivity: "base",
    }) || left.jxb_id.localeCompare(right.jxb_id);
  });
}

const weekdayNumbers: Record<string, number> = {
  一: 1,
  二: 2,
  三: 3,
  四: 4,
  五: 5,
  六: 6,
  日: 7,
  天: 7,
};

const numberWeekdays: Record<number, string> = {
  1: "一",
  2: "二",
  3: "三",
  4: "四",
  5: "五",
  6: "六",
  7: "日",
};

function expandRange(spec: string): number[] {
  const values = new Set<number>();
  for (const rawToken of spec.split(",")) {
    const token = rawToken.trim();
    if (!token) continue;
    const [rawStart, rawEnd] = token.split("-", 2);
    const start = Number(rawStart);
    const end = rawEnd === undefined ? start : Number(rawEnd);
    if (!Number.isInteger(start) || !Number.isInteger(end)) continue;
    for (let value = start; value <= end; value += 1) values.add(value);
  }
  return [...values];
}

function expandWeeks(spec: string): number[] {
  const values = new Set<number>();
  for (const rawToken of spec.split(",")) {
    const match = rawToken.trim().match(/^(\d+)(?:-(\d+))?周(?:\((单|双)\))?$/);
    if (!match) continue;
    const start = Number(match[1]);
    const end = match[2] ? Number(match[2]) : start;
    const parity = match[3];
    for (let week = start; week <= end; week += 1) {
      if (parity === "单" && week % 2 === 0) continue;
      if (parity === "双" && week % 2 === 1) continue;
      values.add(week);
    }
  }
  return [...values];
}

export function parseSchedule(schedule?: string | null): Set<string> | null {
  const text = String(schedule || "").trim();
  if (!text || ["--", "不排教室", "待定"].includes(text)) return null;
  const slots = new Set<string>();
  const pattern = /星期([一二三四五六日天])第([\d,-]+)节(?:\{([^}]*)\})?/g;
  for (const segment of text.split(/<br\s*\/?>|\r?\n/i)) {
    pattern.lastIndex = 0;
    const match = pattern.exec(segment.trim());
    if (!match) continue;
    const day = weekdayNumbers[match[1]];
    const periods = expandRange(match[2]);
    const weeks = match[3] ? expandWeeks(match[3]) : [];
    for (const week of weeks) {
      for (const period of periods) slots.add(`${day}:${week}:${period}`);
    }
  }
  return slots.size > 0 ? slots : null;
}

export function scheduleConflict(
  first?: string | null,
  second?: string | null,
): { conflict: boolean; detail?: string } | null {
  const firstSlots = parseSchedule(first);
  const secondSlots = parseSchedule(second);
  if (!firstSlots || !secondSlots) return null;
  const sortedSlots = [...firstSlots].sort((left, right) => {
    const leftParts = left.split(":").map(Number);
    const rightParts = right.split(":").map(Number);
    return leftParts[0] - rightParts[0]
      || leftParts[1] - rightParts[1]
      || leftParts[2] - rightParts[2];
  });
  for (const slot of sortedSlots) {
    if (!secondSlots.has(slot)) continue;
    const [day, week, period] = slot.split(":").map(Number);
    return {
      conflict: true,
      detail: `周${numberWeekdays[day] || day} 第${period}节 (第${week}周)`,
    };
  }
  return { conflict: false };
}

function rawSchedule(course: CourseRow | undefined): string | null {
  if (!course) return null;
  return course.sksj || course.schedule.join("\n") || null;
}

export function conflictWarning(
  addedIds: string[],
  targetGroup: string,
  groups: PriorityGroup[],
  courses: CourseRow[],
): string | null {
  const courseById = new Map(courses.map((course) => [course.jxb_id, course]));
  const conflicts: string[] = [];
  const unknowns: string[] = [];
  const seenPairs = new Set<string>();
  for (const addedId of addedIds) {
    const added = courseById.get(addedId);
    for (const group of groups) {
      if (group.name === targetGroup) continue;
      for (const otherId of group.priority) {
        if (otherId === addedId) continue;
        const pair = [addedId, otherId].sort().join("\0");
        if (seenPairs.has(pair)) continue;
        seenPairs.add(pair);
        const other = courseById.get(otherId);
        const verdict = scheduleConflict(rawSchedule(added), rawSchedule(other));
        const pairLabel = `${added?.title || addedId} 与“${group.name}”组的 ${other?.title || otherId}`;
        if (verdict === null) unknowns.push(pairLabel);
        else if (verdict.conflict) conflicts.push(`${pairLabel}　${verdict.detail}`);
      }
    }
  }
  if (conflicts.length === 0 && unknowns.length === 0) return null;
  return [
    ...(conflicts.length > 0
      ? ["确定存在时间冲突：", ...conflicts.map((line) => `　${line}`)]
      : []),
    ...(unknowns.length > 0
      ? ["以下缺少时间数据，无法判断是否冲突：", ...unknowns.map((line) => `　${line}`)]
      : []),
  ].join("\n");
}
