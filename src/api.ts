import { invoke } from "@tauri-apps/api/core";

export const isTauri = () =>
  typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

function desktopInvoke<T>(command: string, args?: Record<string, unknown>): Promise<T> {
  if (!isTauri()) {
    return Promise.reject(
      new Error("当前是浏览器预览，Python 后端仅在 Tauri 桌面应用中可用。"),
    );
  }
  return invoke<T>(command, args);
}

export type AutoSwapState = "off" | "dry_run" | "enabled";

export interface MetricMap {
  queries: number;
  groups: number;
  snapshot: number;
  watched: number;
  open_courses: number;
  interval: string;
  auto_swap: AutoSwapState;
}

export interface SettingsPayload {
  jaccount_user: string;
  jaccount_pass: string;
  course_plus_password: string;
  poll_min: number;
  poll_max: number;
  email_enabled: boolean;
  smtp_host: string;
  smtp_port: number;
  smtp_user: string;
  smtp_pass: string;
  mail_from: string;
  mail_to: string;
}

export interface UserInfo {
  name: string;
  student_id: string;
  class_name: string;
  major: string;
  term: string;
  catalog_fetched_at?: string;
}

export interface CourseRow {
  jxb_id: string;
  title: string;
  class_name: string;
  summary: string;
  detail: string;
  teachers: string;
  schedule: string[];
  locations: string[];
  search_text: string;
  seat_text: string;
  availability: "open" | "full" | "unknown";
  availability_text: string;
  group?: string | null;
  chosen: boolean;
  category: string;
  rating_text: string;
  rating: CourseRating;
  kch?: string;
  sksj?: string | null;
}

export interface CourseRating {
  status: "rated" | "empty" | "not_found" | "failed" | "unknown";
  score: number | null;
  count: number | null;
  teacher: string | null;
  semester: string | null;
  updated_at: string | null;
  message: string | null;
}

export interface GroupMember {
  jxb_id: string;
  label: string;
  detail: string;
  chosen: boolean;
  watched: boolean;
  availability: "open" | "full" | "unknown";
}

export interface PriorityGroup {
  name: string;
  is_pe: boolean;
  priority: string[];
  held?: string | null;
  held_label: string;
  watched_count: number;
  fatal: boolean;
  members: GroupMember[];
}

export interface StateRow {
  jxb_id: string;
  watched: boolean;
  group?: string | null;
  title: string;
  summary: string;
  seat_text: string;
  open: boolean | null;
}

export interface SwapHistoryRow {
  timestamp?: string;
  dry_run?: boolean;
  group?: string;
  target?: string;
  drop?: string;
  ok?: boolean;
  status?: string;
  kcmc?: string;
}

export interface Snapshot {
  generated_at: string;
  metrics: MetricMap;
  settings: SettingsPayload;
  user: UserInfo;
  groups: PriorityGroup[];
  courses: CourseRow[];
  state_rows: StateRow[];
  swap_state: {
    completed: string[];
    fatal: string[];
    fatal_groups: string[];
  };
  swap_history: SwapHistoryRow[];
  logs: string[];
  categories: string[];
}

export interface SaveGroupsResult {
  ok: boolean;
  warnings: string[];
  duplicates: Record<string, string[]>;
  unresolved: string[];
  course_count: number;
}

export function loadSnapshot(): Promise<Snapshot> {
  return desktopInvoke<Snapshot>("load_snapshot");
}

export function saveSettings(payload: SettingsPayload): Promise<{ ok: boolean }> {
  return desktopInvoke("save_settings", { input: { payload } });
}

export function saveGroups(groups: PriorityGroup[]): Promise<SaveGroupsResult> {
  const payload = {
    groups: Object.fromEntries(
      groups.map((group) => [
        group.name,
        { is_pe: group.is_pe, priority: group.priority },
      ]),
    ),
  };
  return desktopInvoke("save_groups", { input: { payload } });
}

export function setAutoSwap(enabled: boolean, dryRun: boolean): Promise<{ ok: boolean }> {
  return desktopInvoke("set_auto_swap", { input: { enabled, dry_run: dryRun } });
}

export function startProcess(
  label: string,
  script: string,
  args: string[] = [],
  debug = false,
): Promise<void> {
  return desktopInvoke("start_process", { input: { label, script, args, debug } });
}

export function stopProcess(label: string): Promise<void> {
  return desktopInvoke("stop_process", { label });
}

export function pollProcesses(): Promise<string[]> {
  return desktopInvoke("poll_processes");
}
