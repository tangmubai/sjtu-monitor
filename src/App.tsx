import { listen } from "@tauri-apps/api/event";
import { getCurrentWindow } from "@tauri-apps/api/window";
import {
  Activity,
  BookOpen,
  Check,
  ClipboardList,
  Download,
  Eye,
  Gauge,
  Mail,
  Moon,
  Pause,
  Play,
  Plus,
  RefreshCw,
  Save,
  Search,
  Settings,
  ShieldAlert,
  Square,
  Sun,
  Trash2,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Badge as UiBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  CourseRow,
  PriorityGroup,
  SettingsPayload,
  Snapshot,
  completeOnboarding,
  isTauri,
  loadSnapshot,
  pollProcesses,
  saveGroups,
  saveSettings,
  setAutoSwap,
  startProcess,
  stopProcess,
  testEmail,
} from "./api";
import {
  conflictWarning,
  CourseSort,
  formatRatingScore,
  LogLevel,
  parseLogLine,
  sortCourses,
} from "./courseLogic";

type Page = "overview" | "courses" | "swap" | "snapshot" | "logs" | "settings";
type ThemeMode = "light" | "dark";
type ThemePreference = ThemeMode | "system";

interface RuntimeLine {
  source: string;
  text: string;
  time: string;
}

const pages: Array<{ id: Page; label: string; icon: typeof Activity }> = [
  { id: "overview", label: "总览", icon: Gauge },
  { id: "courses", label: "课程方案", icon: BookOpen },
  { id: "swap", label: "自动换课", icon: ShieldAlert },
  { id: "snapshot", label: "快照", icon: ClipboardList },
  { id: "logs", label: "日志", icon: Activity },
  { id: "settings", label: "设置", icon: Settings },
];

const emptySettings: SettingsPayload = {
  jaccount_user: "",
  jaccount_pass: "",
  course_plus_password: "",
  poll_min: 60,
  poll_max: 120,
  email_enabled: true,
  smtp_host: "",
  smtp_port: 465,
  smtp_user: "",
  smtp_pass: "",
  mail_from: "",
  mail_to: "",
};

const RELEASE_MODE = import.meta.env.VITE_SJTU_RELEASE === "1";

function nowTime() {
  return new Date().toLocaleTimeString("zh-CN", { hour12: false });
}

function cloneGroups(groups: PriorityGroup[]): PriorityGroup[] {
  return groups.map((group) => ({
    ...group,
    priority: [...group.priority],
    members: [...group.members],
  }));
}

function mergePriorityIds(existing: string[], added: string[], courses: CourseRow[]) {
  const chosenIds = new Set(courses.filter((course) => course.chosen).map((course) => course.jxb_id));
  const chosen = existing.filter((id) => chosenIds.has(id));
  const targets = existing.filter((id) => !chosenIds.has(id));
  for (const id of added) {
    if (targets.includes(id) || chosen.includes(id)) continue;
    if (chosenIds.has(id)) chosen.push(id);
    else targets.push(id);
  }
  return [...targets, ...chosen];
}

function shortId(value?: string | null) {
  if (!value) return "-";
  return value.length > 18 ? `${value.slice(0, 8)}...${value.slice(-6)}` : value;
}

function statusText(value: Snapshot["metrics"]["auto_swap"]) {
  if (value === "enabled") return "真实启用";
  if (value === "dry_run") return "演练";
  return "关闭";
}

function userValue(value?: string | null) {
  const text = String(value || "").trim();
  return text && text !== "-" ? text : "未同步";
}

function readThemePreference(): ThemePreference {
  try {
    const value = typeof window !== "undefined" ? window.localStorage?.getItem("sjtu-monitor-theme") : null;
    return value === "light" || value === "dark" || value === "system" ? value : "system";
  } catch {
    return "system";
  }
}

function persistThemePreference(theme: ThemePreference) {
  try {
    window.localStorage?.setItem("sjtu-monitor-theme", theme);
  } catch {
    // LocalStorage is unavailable in some test and embedded environments.
  }
}

function Badge({ tone, children }: { tone: string; children: React.ReactNode }) {
  const variant = tone === "danger"
    ? "destructive"
    : tone === "success"
      ? "success"
      : tone === "primary"
        ? "default"
        : "secondary";
  return <UiBadge variant={variant}>{children}</UiBadge>;
}

function BrandMark() {
  return (
    <span className="brandMark" role="img" aria-label="交我选">
      <svg viewBox="0 0 48 48" aria-hidden="true">
        <path className="brandMark-card" d="M10 12.5A4.5 4.5 0 0 1 14.5 8h19A4.5 4.5 0 0 1 38 12.5v21a4.5 4.5 0 0 1-4.5 4.5h-19A4.5 4.5 0 0 1 10 33.5v-21Z" />
        <path className="brandMark-title" d="M16 15h10" />
        <path className="brandMark-line" d="M16 20h7M16 31h7" />
        <path className="brandMark-pulse" d="M26 27h3l2-5 3 10 2-5h3" />
        <circle className="brandMark-dot" cx="37" cy="11" r="4" />
      </svg>
    </span>
  );
}

function App() {
  const [page, setPage] = useState<Page>("overview");
  const [themePreference, setThemePreference] = useState<ThemePreference>(readThemePreference);
  const [systemDark, setSystemDark] = useState(() =>
    typeof window !== "undefined" && window.matchMedia?.("(prefers-color-scheme: dark)").matches,
  );
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [groups, setGroups] = useState<PriorityGroup[]>([]);
  const [settings, setSettings] = useState<SettingsPayload>(emptySettings);
  const [selectedGroup, setSelectedGroup] = useState<string>("");
  const [selectedCourses, setSelectedCourses] = useState<Set<string>>(new Set());
  const [activeCourse, setActiveCourse] = useState("");
  const [selectedMember, setSelectedMember] = useState<string>("");
  const [courseQuery, setCourseQuery] = useState("");
  const [category, setCategory] = useState("全部");
  const [courseSort, setCourseSort] = useState<CourseSort>("catalog");
  const [onlyOpen, setOnlyOpen] = useState(false);
  const [onlyUnassigned, setOnlyUnassigned] = useState(false);
  const [logQuery, setLogQuery] = useState("");
  const [logLevel, setLogLevel] = useState<"all" | LogLevel>("all");
  const [stateFilter, setStateFilter] = useState<"watched" | "open" | "all">("watched");
  const [runtimeLines, setRuntimeLines] = useState<RuntimeLine[]>([]);
  const [running, setRunning] = useState<Set<string>>(new Set());
  const [debug, setDebug] = useState(false);
  const [status, setStatus] = useState("正在读取本地状态");
  const [busy, setBusy] = useState(false);
  const [newGroupName, setNewGroupName] = useState("");
  const [newGroupOpen, setNewGroupOpen] = useState(false);
  const [savedGroupsSignature, setSavedGroupsSignature] = useState("");
  const [onboardingStep, setOnboardingStep] = useState<1 | 2 | 3>(1);

  const groupsSignature = JSON.stringify(
    groups.map((group) => ({
      name: group.name,
      is_pe: group.is_pe,
      priority: group.priority,
    })),
  );
  const groupsDirty = Boolean(savedGroupsSignature && groupsSignature !== savedGroupsSignature);
  const theme: ThemeMode = themePreference === "system" ? (systemDark ? "dark" : "light") : themePreference;

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  useEffect(() => {
    persistThemePreference(themePreference);
  }, [themePreference]);

  useEffect(() => {
    if (!snapshot || snapshot.onboarding.completed) return;
    if (snapshot.onboarding.catalog_ready) setOnboardingStep(3);
    else if (snapshot.onboarding.has_account) setOnboardingStep(2);
    else setOnboardingStep(1);
  }, [snapshot]);

  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = (event: MediaQueryListEvent) => setSystemDark(event.matches);
    setSystemDark(media.matches);
    media.addEventListener?.("change", onChange);
    return () => media.removeEventListener?.("change", onChange);
  }, []);

  async function refresh() {
    setBusy(true);
    try {
      const data = await loadSnapshot();
      setSnapshot(data);
      const nextGroups = cloneGroups(data.groups);
      setGroups(nextGroups);
      setSavedGroupsSignature(JSON.stringify(
        nextGroups.map((group) => ({
          name: group.name,
          is_pe: group.is_pe,
          priority: group.priority,
        })),
      ));
      setSettings(data.settings);
      setSelectedGroup((current) => current || data.groups[0]?.name || "");
      setStatus(`已刷新 ${data.generated_at}`);
    } catch (error) {
      setStatus(String(error));
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    refresh();
    if (!isTauri()) return;
    const unsubs: Array<() => void> = [];
    listen<{ label: string; line: string }>("process-output", (event) => {
      setRuntimeLines((lines) => [
        ...lines.slice(-499),
        { source: event.payload.label, text: event.payload.line, time: nowTime() },
      ]);
    }).then((unsub) => unsubs.push(unsub));
    listen<{ label: string; code?: number }>("process-exit", (event) => {
      setRuntimeLines((lines) => [
        ...lines.slice(-499),
        {
          source: event.payload.label,
          text: `exit=${event.payload.code ?? "-"}`,
          time: nowTime(),
        },
      ]);
      setRunning((current) => {
        const next = new Set(current);
        next.delete(event.payload.label);
        return next;
      });
      refresh();
    }).then((unsub) => unsubs.push(unsub));
    const timer = window.setInterval(async () => {
      try {
        const labels = await pollProcesses();
        setRunning(new Set(labels));
      } catch {
        // The next explicit action will surface bridge errors.
      }
    }, 1500);
    return () => {
      window.clearInterval(timer);
      unsubs.forEach((unsub) => unsub());
    };
  }, []);

  useEffect(() => {
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (!groupsDirty) return;
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", beforeUnload);
    if (!isTauri()) return () => window.removeEventListener("beforeunload", beforeUnload);
    let unlisten: (() => void) | undefined;
    getCurrentWindow().onCloseRequested((event) => {
      if (!groupsDirty) return;
      const discard = window.confirm("选课方案还有未保存的修改。确定放弃修改并退出吗？");
      if (!discard) event.preventDefault();
    }).then((callback) => {
      unlisten = callback;
    });
    return () => {
      window.removeEventListener("beforeunload", beforeUnload);
      unlisten?.();
    };
  }, [groupsDirty]);

  const selectedGroupData = groups.find((group) => group.name === selectedGroup) || null;
  const selectedCourse = snapshot?.courses.find((course) => course.jxb_id === activeCourse);

  function createGroup() {
    const name = newGroupName.trim();
    if (!name || groups.some((group) => group.name === name)) return;
    setGroups((current) => [
      ...current,
      { name, is_pe: false, priority: [], held_label: "-", watched_count: 0, fatal: false, members: [] },
    ]);
    setSelectedGroup(name);
    setNewGroupName("");
    setNewGroupOpen(false);
    setStatus(`已新建方案 ${name}，尚未保存`);
  }

  function deleteSelectedGroup() {
    if (!selectedGroupData) return;
    const remaining = groups.filter((group) => group.name !== selectedGroupData.name);
    setGroups(remaining);
    setSelectedGroup(remaining[0]?.name || "");
    setSelectedMember("");
    setStatus(`已删除方案 ${selectedGroupData.name}，尚未保存`);
  }

  const filteredCourses = useMemo(() => {
    const query = courseQuery.trim().toLowerCase();
    const matches = (snapshot?.courses || []).filter((course) => {
      if (category !== "全部" && course.category !== category) return false;
      if (onlyOpen && course.availability !== "open") return false;
      if (onlyUnassigned && course.group) return false;
      if (query && !course.search_text.toLowerCase().includes(query)) return false;
      return true;
    });
    return sortCourses(matches, courseSort);
  }, [snapshot, courseQuery, category, onlyOpen, onlyUnassigned, courseSort]);

  const parsedLogs = useMemo(() => {
    const persisted = (snapshot?.logs || []).map((line) =>
      parseLogLine("changes", line.replace(/^\[changes\]\s*/, "")),
    );
    const runtime = runtimeLines.map((line) => parseLogLine(line.source, line.text, line.time));
    const lines = [...persisted, ...runtime];
    const query = logQuery.trim().toLowerCase();
    return query
      ? lines.filter((line) => `${line.source} ${line.message}`.toLowerCase().includes(query))
      : lines;
  }, [snapshot, runtimeLines, logQuery]);

  const logCounts = useMemo(() => {
    const counts: Record<"all" | LogLevel, number> = { all: parsedLogs.length, info: 0, warn: 0, error: 0, debug: 0 };
    for (const line of parsedLogs) counts[line.level] += 1;
    return counts;
  }, [parsedLogs]);

  const visibleLogs = useMemo(
    () => (logLevel === "all" ? parsedLogs : parsedLogs.filter((line) => line.level === logLevel)),
    [parsedLogs, logLevel],
  );

  const stateRows = useMemo(() => {
    const rows = snapshot?.state_rows || [];
    if (stateFilter === "watched") return rows.filter((row) => row.watched);
    if (stateFilter === "open") return rows.filter((row) => row.open === true);
    return rows;
  }, [snapshot, stateFilter]);

  async function run(label: string, script: string, args: string[] = []) {
    try {
      await startProcess(label, script, args, debug && !RELEASE_MODE);
      setRunning((current) => new Set(current).add(label));
      setRuntimeLines((lines) => [
        ...lines.slice(-499),
        { source: label, text: `$ python ${script} ${args.join(" ")}`, time: nowTime() },
      ]);
    } catch (error) {
      setStatus(String(error));
    }
  }

  function replaceGroup(name: string, patch: Partial<PriorityGroup>) {
    setGroups((current) =>
      current.map((group) => (group.name === name ? { ...group, ...patch } : group)),
    );
  }

  function addCoursesToCurrentGroup(ids: string[]) {
    if (!selectedGroupData || !snapshot || ids.length === 0) {
      if (!selectedGroupData) setStatus("请先选择一个课程组");
      return;
    }
    const addedIds = ids.filter((id) => !selectedGroupData.priority.includes(id));
    if (addedIds.length === 0) {
      setStatus(`所选教学班已在“${selectedGroupData.name}”中`);
      return;
    }
    const nextPriority = mergePriorityIds(
      selectedGroupData.priority,
      addedIds,
      snapshot.courses,
    );
    replaceGroup(selectedGroupData.name, { priority: nextPriority });
    setSelectedCourses(new Set());
    setStatus(`已加入 ${addedIds.length} 个教学班到“${selectedGroupData.name}”，尚未保存`);
    const warning = conflictWarning(
      addedIds,
      selectedGroupData.name,
      groups,
      snapshot.courses,
    );
    if (warning) window.alert(warning);
  }

  function addSelectedCourses() {
    addCoursesToCurrentGroup([...selectedCourses]);
  }

  function moveMember(delta: number) {
    if (!selectedGroupData || !selectedMember) return;
    const priority = [...selectedGroupData.priority];
    const index = priority.indexOf(selectedMember);
    const nextIndex = index + delta;
    if (index < 0 || nextIndex < 0 || nextIndex >= priority.length) return;
    [priority[index], priority[nextIndex]] = [priority[nextIndex], priority[index]];
    replaceGroup(selectedGroupData.name, { priority });
  }

  function setAsHeld() {
    if (!selectedGroupData || !selectedMember) return;
    const priority = selectedGroupData.priority.filter((id) => id !== selectedMember);
    priority.push(selectedMember);
    replaceGroup(selectedGroupData.name, { priority });
  }

  async function persistGroups() {
    setBusy(true);
    try {
      const result = await saveGroups(groups);
      await refresh();
      const warnings = [
        ...result.warnings,
        ...Object.entries(result.duplicates).map(([id, owners]) => `${shortId(id)} 重复: ${owners.join(", ")}`),
        ...result.unresolved.map((id) => `${shortId(id)} 无法推导查询模板`),
      ];
      setStatus(
        warnings.length
          ? `方案已保存，但有 ${warnings.length} 条提示`
          : `方案已保存，共监控 ${result.course_count} 门课程`,
      );
      if (warnings.length) {
        setRuntimeLines((lines) => [
          ...lines,
          ...warnings.map((text) => ({ source: "save-groups", text, time: nowTime() })),
        ].slice(-500));
      }
    } catch (error) {
      setStatus(String(error));
    } finally {
      setBusy(false);
    }
  }

  async function persistSettings() {
    setBusy(true);
    try {
      await saveSettings(settings);
      await refresh();
      setStatus("用户设置已保存；正在运行的监控需重启后使用新参数");
    } catch (error) {
      setStatus(String(error));
    } finally {
      setBusy(false);
    }
  }

  async function sendTestEmail() {
    setBusy(true);
    setStatus("正在保存设置并发送测试邮件...");
    try {
      await saveSettings(settings);
      const result = await testEmail();
      await refresh();
      setStatus(`测试邮件已发送至 ${result.mail_to || "收件人"}，请查收`);
    } catch (error) {
      setStatus(`测试邮件发送失败: ${String(error)}`);
    } finally {
      setBusy(false);
    }
  }

  async function saveOnboardingAccount() {
    if (!settings.jaccount_user.trim()) {
      setStatus("请输入 JAccount 账号");
      return;
    }
    if (!settings.jaccount_pass && !settings.has_jaccount_pass) {
      setStatus("请输入 JAccount 密码");
      return;
    }
    setBusy(true);
    try {
      await saveSettings(settings);
      await refresh();
      setOnboardingStep(2);
      setStatus("账号已安全保存；同步只会在你点击按钮后开始");
    } catch (error) {
      setStatus(String(error));
    } finally {
      setBusy(false);
    }
  }

  async function finishOnboarding() {
    setBusy(true);
    try {
      await completeOnboarding();
      await refresh();
      setPage("courses");
      setStatus("初始化完成，请创建你的选课方案");
    } catch (error) {
      setStatus(String(error));
    } finally {
      setBusy(false);
    }
  }

  async function toggleAutoSwap(enabled: boolean, dryRun: boolean) {
    if (RELEASE_MODE && dryRun) {
      setStatus("发行版不提供演练模式");
      return;
    }
    if (enabled && !dryRun && snapshot?.metrics.auto_swap !== "enabled") {
      const ok = window.confirm("真实自动换课会执行退课和选课。确定继续？");
      if (!ok) return;
    }
    try {
      await setAutoSwap(enabled, dryRun);
      await refresh();
      setStatus("自动换课设置已保存；重启监控后生效");
    } catch (error) {
      setStatus(String(error));
    }
  }

  if (snapshot && !snapshot.onboarding.completed) {
    const onboardingLogs = runtimeLines
      .filter((line) => line.source === "bootstrap")
      .slice(-8);
    return (
      <div className={`onboardingShell theme-${theme}`}>
        <section className="onboardingCard">
          <div className="onboardingBrand">
            <BrandMark />
            <div><strong>欢迎使用交我选</strong><p>完成基础设置后再创建自己的监控方案</p></div>
          </div>
          <div className="onboardingSteps" aria-label="初始化进度">
            {["保存账号", "同步课程", "配置方案"].map((label, index) => (
              <div className={onboardingStep >= index + 1 ? "active" : ""} key={label}>
                <span>{index + 1}</span><small>{label}</small>
              </div>
            ))}
          </div>

          {onboardingStep === 1 && (
            <div className="onboardingBody">
              <div><h1>连接 JAccount</h1><p>凭据保存在 Windows DPAPI 安全存储中。本步骤不会发起网络请求。</p></div>
              <div className="onboardingForm">
                <Field label="JAccount" value={settings.jaccount_user} onChange={(value) => setSettings({ ...settings, jaccount_user: value })} />
                <Field label="JAccount 密码" type="password" value={settings.jaccount_pass} placeholder={settings.has_jaccount_pass ? "已安全保存，留空不修改" : "请输入密码"} onChange={(value) => setSettings({ ...settings, jaccount_pass: value })} />
              </div>
              <div className="onboardingActions"><Button onClick={saveOnboardingAccount} disabled={busy}><Save />保存并继续</Button></div>
            </div>
          )}

          {onboardingStep === 2 && (
            <div className="onboardingBody">
              <div><h1>同步课程目录</h1><p>只有点击下方按钮后才会登录教务系统并获取用户信息、全量课程和当前已选课程。</p></div>
              <div className="syncNotice">
                <Download size={28} />
                <div><strong>{running.has("bootstrap") ? "正在同步课程" : "准备同步"}</strong><p>同步失败时可返回修改账号后重试，不会自动启动监控或换课。</p></div>
              </div>
              {onboardingLogs.length > 0 && <div className="onboardingConsole">{onboardingLogs.map((line, index) => <p key={`${line.time}-${index}`}>{line.text}</p>)}</div>}
              <div className="onboardingActions split">
                <Button variant="outline" onClick={() => setOnboardingStep(1)} disabled={running.has("bootstrap")}>返回修改账号</Button>
                <Button onClick={() => run("bootstrap", "bootstrap.py")} disabled={running.has("bootstrap")}><RefreshCw className={running.has("bootstrap") ? "animate-spin" : ""} />{running.has("bootstrap") ? "同步中" : "开始同步课程"}</Button>
              </div>
            </div>
          )}

          {onboardingStep === 3 && (
            <div className="onboardingBody">
              <div><h1>创建自己的选课方案</h1><p>发行版不再内置任何课程或方案。进入课程工作台后，新建方案并按从高到低排列教学班，当前已选班放在末尾。</p></div>
              <div className="setupRules">
                <p><Check size={17} /> 单击课程查看详情，双击可快速加入当前方案</p>
                <p><Check size={17} /> 自动换课只向更高优先级升级，不会降级</p>
                <p><Check size={17} /> 自动换课默认关闭，需在完成方案后手动启用</p>
              </div>
              <div className="onboardingActions"><Button onClick={finishOnboarding} disabled={busy}><BookOpen />进入课程方案</Button></div>
            </div>
          )}
          <p className="onboardingStatus">{status}</p>
        </section>
      </div>
    );
  }

  return (
    <div className={`app theme-${theme}`}>
      <aside className="sidebar">
        <div className="brand">
          <BrandMark />
          <div>
            <strong>交我选</strong>
            <small>SJTU Monitor</small>
          </div>
        </div>
        <nav>
          {pages.map((item) => {
            const Icon = item.icon;
            return (
              <button
                key={item.id}
                className={page === item.id ? "active" : ""}
                onClick={() => setPage(item.id)}
                title={item.label}
              >
                <Icon size={18} />
                <span>{item.label}</span>
              </button>
            );
          })}
        </nav>
        {!RELEASE_MODE && (
          <div className="sidebarFooter">
            <label className="checkLine">
              <input type="checkbox" checked={debug} onChange={(event) => setDebug(event.target.checked)} />
              Debug
            </label>
          </div>
        )}
      </aside>

      <main className="main">
        <header className="topbar">
          <div>
            <h1>{pages.find((item) => item.id === page)?.label}</h1>
            <p>{status}</p>
          </div>
          <div className="toolbar">
            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button variant="outline" size="icon" onClick={refresh} disabled={busy}>
                    <RefreshCw className={busy ? "animate-spin" : ""} />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>刷新本地状态</TooltipContent>
              </Tooltip>
            </TooltipProvider>
          </div>
        </header>

        {!snapshot ? (
          <section className="emptyState">正在载入本地状态...</section>
        ) : (
          <>
            {page === "overview" && (
              <section className="pageGrid">
                <section className="overviewHero widePanel">
                  <div>
                    <span className="eyebrow">本地工作台</span>
                    <h2>{userValue(snapshot.user.name)}</h2>
                    <p>{snapshot.user.term || "未设置学期"} · {userValue(snapshot.user.major)}</p>
                  </div>
                  <div className="heroStatus">
                    <Badge tone={snapshot.metrics.auto_swap === "enabled" ? "danger" : snapshot.metrics.auto_swap === "dry_run" ? "primary" : "neutral"}>
                      {statusText(snapshot.metrics.auto_swap)}
                    </Badge>
                    <span>{snapshot.generated_at}</span>
                  </div>
                </section>
                <MonitorStatusPanel
                  autoSwap={snapshot.metrics.auto_swap}
                  interval={snapshot.metrics.interval}
                  running={running}
                  debug={debug && !RELEASE_MODE}
                  releaseMode={RELEASE_MODE}
                  onRunOnce={() => run("once", "monitor.py", ["--once"])}
                  onStart={() => run("monitor", "monitor.py")}
                  onStop={() => stopProcess("monitor")}
                />
                <div className="metrics">
                  <Metric title="查询课程" value={snapshot.metrics.queries} />
                  <Metric title="方案组" value={snapshot.metrics.groups} />
                  <Metric title="快照教学班" value={snapshot.metrics.snapshot} />
                  <Metric title="当前目标" value={snapshot.metrics.watched} />
                  <Metric title="目录空位" value={snapshot.metrics.open_courses} />
                  <Metric title="自动换课" value={statusText(snapshot.metrics.auto_swap)} />
                </div>
                <section className="panel profilePanel">
                  <div className="panelHeader">
                    <h2>当前用户</h2>
                    <Badge tone={snapshot.metrics.auto_swap === "enabled" ? "danger" : "neutral"}>
                      {statusText(snapshot.metrics.auto_swap)}
                    </Badge>
                  </div>
                  <dl className="infoGrid">
                    <dt>姓名</dt><dd>{userValue(snapshot.user.name)}</dd>
                    <dt>学号</dt><dd>{userValue(snapshot.user.student_id)}</dd>
                    <dt>班级</dt><dd>{userValue(snapshot.user.class_name)}</dd>
                    <dt>专业</dt><dd>{userValue(snapshot.user.major)}</dd>
                    <dt>学期</dt><dd>{snapshot.user.term}</dd>
                    <dt>目录更新时间</dt><dd>{snapshot.user.catalog_fetched_at || "-"}</dd>
                  </dl>
                </section>
                <section className="panel">
                  <div className="panelHeader">
                    <h2>方案状态</h2>
                  </div>
                  <div className="list">
                    {snapshot.groups.map((group) => (
                      <button key={group.name} className="listRow" onClick={() => { setPage("courses"); setSelectedGroup(group.name); }}>
                        <span>{group.name}</span>
                        <small>{group.held_label} / 监控 {group.watched_count}</small>
                        {group.fatal && <Badge tone="danger">暂停</Badge>}
                      </button>
                    ))}
                  </div>
                </section>
                <section className="panel quickPanel">
                  <div className="panelHeader">
                    <h2>快捷操作</h2>
                  </div>
                  <div className="quickActions">
                    <Button onClick={() => setPage("courses")}><BookOpen />管理选课方案</Button>
                    <Button variant="outline" onClick={() => setPage("swap")}><ShieldAlert />自动换课</Button>
                    <Button variant="outline" onClick={() => setPage("logs")}><Activity />查看日志</Button>
                  </div>
                </section>
              </section>
            )}

            {page === "courses" && (
              <section className="courseWorkspace">
                <section className="courseToolbar">
                  <label className="modernSearch">
                    <Search size={16} />
                    <Input value={courseQuery} onChange={(event) => setCourseQuery(event.target.value)} placeholder="搜索课程、教师、编号、时间或地点" />
                  </label>
                  <Select value={category} onValueChange={setCategory}>
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="全部">全部类别</SelectItem>
                      {snapshot.categories.map((item) => <SelectItem key={item} value={item}>{item}</SelectItem>)}
                    </SelectContent>
                  </Select>
                  <Select value={courseSort} onValueChange={(value) => setCourseSort(value as CourseSort)}>
                    <SelectTrigger aria-label="课程排序"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="catalog">目录顺序</SelectItem>
                      <SelectItem value="name">课程名称</SelectItem>
                      <SelectItem value="rating">评分从高到低</SelectItem>
                    </SelectContent>
                  </Select>
                  <label className="modernCheck">
                    <Checkbox checked={onlyOpen} onCheckedChange={(value) => setOnlyOpen(value === true)} />
                    有空位
                  </label>
                  <label className="modernCheck">
                    <Checkbox checked={onlyUnassigned} onCheckedChange={(value) => setOnlyUnassigned(value === true)} />
                    未分配
                  </label>
                  <span className="courseCount">{filteredCourses.length} / {snapshot.courses.length}</span>
                  <Button variant="outline" onClick={() => run("bootstrap", "bootstrap.py")} disabled={running.has("bootstrap")}>
                    <Download className={running.has("bootstrap") ? "animate-pulse" : ""} />
                    {running.has("bootstrap") ? "正在获取课程" : "获取全量课程"}
                  </Button>
                  <Button variant="outline" onClick={() => run("ratings-all", "bootstrap.py", ["--fetch-ratings-all"])} disabled={running.has("ratings-all")}>
                    <RefreshCw className={running.has("ratings-all") ? "animate-spin" : ""} />
                    {running.has("ratings-all") ? "正在获取评价" : "获取全部评价"}
                  </Button>
                </section>

                <div className="courseMain">
                  <section className="courseListPanel">
                    <div className="sectionHeading">
                      <div><h2>课程目录</h2><p>单击查看详情，双击快捷加入当前课程组；复选框用于批量加入</p></div>
                    </div>
                    <ScrollArea className="courseScroll">
                      <div className="modernCourseList">
                        {filteredCourses.map((course) => (
                          <div
                            key={course.jxb_id}
                            role="button"
                            tabIndex={0}
                            className={`modernCourseRow ${selectedCourses.has(course.jxb_id) ? "selected" : ""} ${activeCourse === course.jxb_id ? "active" : ""}`}
                            onClick={() => setActiveCourse(course.jxb_id)}
                            onDoubleClick={() => addCoursesToCurrentGroup([course.jxb_id])}
                            onKeyDown={(event) => {
                              if (event.key === "Enter" || event.key === " ") {
                                event.preventDefault();
                                setActiveCourse(course.jxb_id);
                              }
                            }}
                          >
                            <span className="courseIdentity">
                              <strong>{course.title}</strong>
                              <small>{course.teachers} · {course.schedule[0] || "时间未定"}</small>
                            </span>
                            <span className="courseMeta">{course.kch || "-"}</span>
                            <Badge tone={course.chosen ? "primary" : course.availability === "open" ? "success" : "neutral"}>
                              {course.chosen ? "已选" : course.availability_text}
                            </Badge>
                            <span className={`ratingCompact ${course.rating.status}`}>{course.rating_text}</span>
                            <span className="courseSelect" onClick={(event) => event.stopPropagation()} onDoubleClick={(event) => event.stopPropagation()}>
                              <Checkbox
                                aria-label={`批量选择 ${course.title}`}
                                checked={selectedCourses.has(course.jxb_id)}
                                onCheckedChange={(checked) => {
                                  setSelectedCourses((current) => {
                                    const next = new Set(current);
                                    if (checked === true) next.add(course.jxb_id);
                                    else next.delete(course.jxb_id);
                                    return next;
                                  });
                                }}
                              />
                            </span>
                          </div>
                        ))}
                        {filteredCourses.length === 0 && <div className="listEmpty">没有符合筛选条件的课程</div>}
                      </div>
                    </ScrollArea>
                  </section>

                  <CourseInspector course={selectedCourse} />
                </div>

                <section className="planWorkspace">
                  <div className="planHeader">
                    <div><h2>选课方案</h2><p>优先级从上到下，当前持有课程固定在末尾</p></div>
                    <div className="buttonRow">
                      <Dialog open={newGroupOpen} onOpenChange={setNewGroupOpen}>
                        <DialogTrigger asChild><Button variant="outline"><Plus />新建方案</Button></DialogTrigger>
                        <DialogContent>
                          <DialogHeader>
                            <DialogTitle>新建选课方案</DialogTitle>
                            <DialogDescription>方案名称应能清晰区分课程类别或目标。</DialogDescription>
                          </DialogHeader>
                          <Input autoFocus value={newGroupName} onChange={(event) => setNewGroupName(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") createGroup(); }} placeholder="例如：大学物理" />
                          <div className="dialogActions">
                            <DialogClose asChild><Button variant="outline">取消</Button></DialogClose>
                            <Button onClick={createGroup} disabled={!newGroupName.trim()}>创建</Button>
                          </div>
                        </DialogContent>
                      </Dialog>
                      <Button onClick={persistGroups} disabled={busy}><Save />保存方案</Button>
                    </div>
                  </div>

                  {groups.length > 0 ? (
                    <Tabs value={selectedGroup} onValueChange={(value) => { setSelectedGroup(value); setSelectedMember(""); }}>
                      <ScrollArea className="planTabsScroll">
                        <TabsList className="planTabs">
                          {groups.map((group) => (
                            <TabsTrigger key={group.name} value={group.name} className="planTab">
                              <span>{group.name}</span>
                              <small>{group.held_label} · {group.watched_count} 门</small>
                              {group.fatal && <UiBadge variant="destructive">暂停</UiBadge>}
                            </TabsTrigger>
                          ))}
                        </TabsList>
                      </ScrollArea>
                    </Tabs>
                  ) : <div className="listEmpty">尚未创建选课方案</div>}

                  {selectedGroupData && (
                    <div className="planEditor">
                      <div className="planControls">
                        <label className="modernCheck">
                          <Checkbox checked={selectedGroupData.is_pe} onCheckedChange={(value) => replaceGroup(selectedGroupData.name, { is_pe: value === true })} />
                          体育课方案
                        </label>
                        <Button onClick={addSelectedCourses} disabled={selectedCourses.size === 0}>
                          <Plus />批量加入当前组 ({selectedCourses.size})
                        </Button>
                        <AlertDialog>
                          <AlertDialogTrigger asChild><Button variant="destructive"><Trash2 />删除方案</Button></AlertDialogTrigger>
                          <AlertDialogContent>
                            <AlertDialogHeader>
                              <AlertDialogTitle>删除“{selectedGroupData.name}”？</AlertDialogTitle>
                              <AlertDialogDescription>保存后该方案及其优先级配置将被移除。</AlertDialogDescription>
                            </AlertDialogHeader>
                            <AlertDialogFooter>
                              <AlertDialogCancel asChild><Button variant="outline">取消</Button></AlertDialogCancel>
                              <AlertDialogAction asChild><Button variant="destructive" onClick={deleteSelectedGroup}>删除</Button></AlertDialogAction>
                            </AlertDialogFooter>
                          </AlertDialogContent>
                        </AlertDialog>
                      </div>
                      <ScrollArea className="priorityScroll">
                        <div className="priorityList">
                          {selectedGroupData.priority.map((id, index) => {
                            const course = snapshot.courses.find((item) => item.jxb_id === id);
                            return (
                              <button key={id} className={`priorityRow ${selectedMember === id ? "selected" : ""}`} onClick={() => setSelectedMember(id)}>
                                <span className="priorityIndex">{index + 1}</span>
                                <span><strong>{course?.title || shortId(id)}</strong><small>{course?.summary || id}</small></span>
                                {course?.chosen && <Badge tone="primary">当前持有</Badge>}
                              </button>
                            );
                          })}
                          {selectedGroupData.priority.length === 0 && <div className="listEmpty">从上方课程目录选择课程并加入此方案</div>}
                        </div>
                      </ScrollArea>
                      <div className="priorityActions">
                        <Button variant="outline" onClick={() => moveMember(-1)} disabled={!selectedMember}>上移</Button>
                        <Button variant="outline" onClick={() => moveMember(1)} disabled={!selectedMember}>下移</Button>
                        <Button variant="outline" onClick={setAsHeld} disabled={!selectedMember}>设为当前持有</Button>
                        <Button variant="ghost" onClick={() => replaceGroup(selectedGroupData.name, { priority: selectedGroupData.priority.filter((id) => id !== selectedMember) })} disabled={!selectedMember}>移除</Button>
                      </div>
                    </div>
                  )}
                </section>
              </section>
            )}

            {page === "swap" && (
              <section className="pageGrid">
                <section className="panel widePanel">
                  <div className="panelHeader">
                    <h2>自动换课开关</h2>
                    <Badge tone={snapshot.metrics.auto_swap === "enabled" ? "danger" : "neutral"}>{statusText(snapshot.metrics.auto_swap)}</Badge>
                  </div>
                  <div className="buttonRow">
                    <button onClick={() => toggleAutoSwap(false, false)}><Pause size={16} /> 关闭</button>
                    {!RELEASE_MODE && <button onClick={() => toggleAutoSwap(true, true)}><Eye size={16} /> 演练</button>}
                    <button className="danger" onClick={() => toggleAutoSwap(true, false)}><ShieldAlert size={16} /> 真实启用</button>
                  </div>
                </section>
                <section className="panel widePanel">
                  <div className="panelHeader">
                    <h2>方案执行状态</h2>
                  </div>
                  <div className="dataGrid six">
                    <strong>方案</strong><strong>类型</strong><strong>当前持有</strong><strong>监控目标</strong><strong>完成</strong><strong>失败</strong>
                    {snapshot.groups.map((group) => (
                      <div className="dataRow six" key={group.name}>
                        <span>{group.name}</span>
                        <span>{group.is_pe ? "体育" : "普通"}</span>
                        <span>{group.held_label}</span>
                        <span>{group.watched_count}</span>
                        <span>{snapshot.swap_state.completed.filter((id) => group.priority.includes(id)).length}</span>
                        <span>{group.fatal ? "方案暂停" : snapshot.swap_state.fatal.filter((id) => group.priority.includes(id)).length}</span>
                      </div>
                    ))}
                  </div>
                </section>
                <section className="panel widePanel">
                  <div className="panelHeader">
                    <h2>换课记录</h2>
                  </div>
                  <div className="table swapHistoryTable">
                    {snapshot.swap_history.map((record, index) => (
                      <div className="tableRow" key={`${record.timestamp}-${index}`}>
                        <span>{record.timestamp || "-"}</span>
                        <span>{record.dry_run ? "演练" : "真实"}</span>
                        <span>{record.group || "-"}</span>
                        <span>{shortId(record.target)}</span>
                        <Badge tone={record.ok ? "success" : "danger"}>{record.ok ? "成功" : record.status || "失败"}</Badge>
                      </div>
                    ))}
                  </div>
                </section>
              </section>
            )}

            {page === "snapshot" && (
              <section className="panel">
                <div className="panelHeader">
                  <h2>监控快照</h2>
                  <select value={stateFilter} onChange={(event) => setStateFilter(event.target.value as typeof stateFilter)}>
                    <option value="watched">仅当前监控</option>
                    <option value="open">仅有空位</option>
                    <option value="all">全部</option>
                  </select>
                </div>
                <div className="table snapshotTable">
                  {stateRows.map((row) => (
                    <div className="tableRow" key={row.jxb_id}>
                      <span>{row.watched ? <Check size={16} /> : "-"}</span>
                      <span>{row.group || "-"}</span>
                      <span><strong>{row.title}</strong><small>{row.summary}</small></span>
                      <span>{row.seat_text}</span>
                      <Badge tone={row.open ? "success" : "neutral"}>{row.open ? "有空位" : row.open === false ? "已满" : "未知"}</Badge>
                    </div>
                  ))}
                </div>
              </section>
            )}

            {page === "logs" && (
              <section className="panel logsPanel">
                <div className="panelHeader">
                  <h2>合并日志</h2>
                  <div className="logToolbar">
                    <div className="logLevelTabs" role="tablist" aria-label="日志级别筛选">
                      {([
                        { id: "all", label: "全部" },
                        { id: "info", label: "信息" },
                        { id: "warn", label: "警告" },
                        { id: "error", label: "错误" },
                        { id: "debug", label: "调试" },
                      ] as const).map((item) => (
                        <button
                          key={item.id}
                          role="tab"
                          aria-selected={logLevel === item.id}
                          className={`${item.id} ${logLevel === item.id ? "active" : ""}`}
                          onClick={() => setLogLevel(item.id)}
                        >
                          {item.label}
                          <small>{logCounts[item.id]}</small>
                        </button>
                      ))}
                    </div>
                    <label className="searchBox narrow">
                      <Search size={16} />
                      <input value={logQuery} onChange={(event) => setLogQuery(event.target.value)} placeholder="筛选日志" />
                    </label>
                  </div>
                </div>
                <div className="logList">
                  {visibleLogs.slice(-350).map((line, index) => (
                    <div className="logLine" key={`${line.time}-${index}`}>
                      <span className="logTime">{line.time || "--:--:--"}</span>
                      <span className={`logBadge ${line.level}`}>{line.level.toUpperCase()}</span>
                      <span className="logSource">{line.source}</span>
                      <span className="logMsg">{line.message}</span>
                    </div>
                  ))}
                  {visibleLogs.length === 0 && <div className="listEmpty">暂无符合条件的日志</div>}
                </div>
              </section>
            )}

            {page === "settings" && (
              <section className="settingsGrid">
                <section className="panel appearancePanel">
                  <div className="panelHeader"><h2>外观</h2></div>
                  <div className="themeChoice" role="radiogroup" aria-label="主题模式">
                    <button className={themePreference === "system" ? "selected" : ""} onClick={() => setThemePreference("system")}>
                      <Settings size={16} />
                      <span>跟随系统</span>
                    </button>
                    <button className={themePreference === "light" ? "selected" : ""} onClick={() => setThemePreference("light")}>
                      <Sun size={16} />
                      <span>浅色</span>
                    </button>
                    <button className={themePreference === "dark" ? "selected" : ""} onClick={() => setThemePreference("dark")}>
                      <Moon size={16} />
                      <span>深色</span>
                    </button>
                  </div>
                  <p className="settingsHint">当前实际显示为{theme === "dark" ? "深色" : "浅色"}模式。</p>
                </section>
                <section className="panel accountPanel">
                  <div className="panelHeader"><h2>账号</h2></div>
                  <Field label="JAccount" value={settings.jaccount_user} onChange={(value) => setSettings({ ...settings, jaccount_user: value })} />
                  <Field label="JAccount 密码" type="password" value={settings.jaccount_pass} placeholder={settings.has_jaccount_pass ? "已安全保存，留空不修改" : "未保存"} onChange={(value) => setSettings({ ...settings, jaccount_pass: value })} />
                  <Field label="选课社区密码" type="password" value={settings.course_plus_password} placeholder={settings.has_course_plus_password ? "已安全保存，留空不修改" : "未保存"} onChange={(value) => setSettings({ ...settings, course_plus_password: value })} />
                  <p className="settingsHint">密码不回显；当前安全存储：{settings.secret_backend || "未知"}。</p>
                </section>
                <section className="panel pollingPanel">
                  <div className="panelHeader"><h2>轮询</h2></div>
                  <div className="twoCols">
                    <Field label="最小轮询秒数" type="number" value={String(settings.poll_min)} onChange={(value) => setSettings({ ...settings, poll_min: Number(value) })} />
                    <Field label="最大轮询秒数" type="number" value={String(settings.poll_max)} onChange={(value) => setSettings({ ...settings, poll_max: Number(value) })} />
                  </div>
                  <p className="settingsHint">持续监控会在此区间内随机等待，降低固定频率请求特征。</p>
                </section>
                <section className="panel mailPanel">
                  <div className="panelHeader"><h2>邮件通知</h2></div>
                  <label className="checkLine">
                    <input type="checkbox" checked={settings.email_enabled} onChange={(event) => setSettings({ ...settings, email_enabled: event.target.checked })} />
                    启用邮件
                  </label>
                  <div className="settingsFormGrid">
                    <Field label="SMTP Host" value={settings.smtp_host} onChange={(value) => setSettings({ ...settings, smtp_host: value })} />
                    <Field label="SMTP Port" type="number" value={String(settings.smtp_port)} onChange={(value) => setSettings({ ...settings, smtp_port: Number(value) })} />
                    <Field label="SMTP User" value={settings.smtp_user} onChange={(value) => setSettings({ ...settings, smtp_user: value })} />
                    <Field label="SMTP Pass" type="password" value={settings.smtp_pass} placeholder={settings.has_smtp_pass ? (settings.smtp_pass_fallback ? "默认复用 JAccount 密码，可单独设置" : "已安全保存，留空不修改") : "未保存"} onChange={(value) => setSettings({ ...settings, smtp_pass: value })} />
                    <Field label="发件人" value={settings.mail_from} onChange={(value) => setSettings({ ...settings, mail_from: value })} />
                    <Field label="收件人" value={settings.mail_to} onChange={(value) => setSettings({ ...settings, mail_to: value })} />
                  </div>
                  <p className="settingsHint">默认使用交大邮箱 mail.sjtu.edu.cn:465(SSL)，账号为 jAccount@sjtu.edu.cn，密码复用 JAccount 密码；改用其他邮箱时覆盖对应字段即可。</p>
                  <div className="settingsActions">
                    <button onClick={sendTestEmail} disabled={busy}><Mail size={16} /> 保存并发送测试邮件</button>
                  </div>
                </section>
                <section className="panel settingsSavePanel">
                  <div>
                    <strong>保存全局设置</strong>
                    <p>账号、轮询与邮件配置将一并保存；外观选择即时生效，正在运行的监控需重启后使用新参数。</p>
                  </div>
                  <button className="primary" onClick={persistSettings} disabled={busy}><Save size={16} /> 保存全部设置</button>
                </section>
              </section>
            )}
          </>
        )}
      </main>
    </div>
  );
}

function CourseInspector({ course }: { course?: CourseRow }) {
  if (!course) {
    return (
      <aside className="courseInspector emptyInspector">
        <BookOpen size={28} />
        <strong>选择课程查看详情</strong>
        <p>可同时选择多门课程，最后选择的课程显示在这里。</p>
      </aside>
    );
  }

  const ratingLabel = {
    rated: "课程评价",
    empty: "暂无评价",
    not_found: "社区未收录",
    failed: "评价获取失败",
    unknown: "尚未获取评价",
  }[course.rating.status];

  return (
    <aside className="courseInspector">
      <div className="inspectorHeader">
        <div>
          <span className="eyebrow">{course.category}</span>
          <h2>{course.title}</h2>
          <p>{course.class_name}</p>
        </div>
        <Badge tone={course.chosen ? "primary" : course.availability === "open" ? "success" : "neutral"}>
          {course.chosen ? "当前已选" : course.availability_text}
        </Badge>
      </div>

      <div className={`ratingHero ${course.rating.status}`}>
        <div>
          <span>{ratingLabel}</span>
          <strong>{formatRatingScore(course.rating.score)}</strong>
        </div>
        <dl>
          <div><dt>评价数</dt><dd>{course.rating.count ?? "-"}</dd></div>
          <div><dt>教师</dt><dd>{course.rating.teacher || course.teachers || "-"}</dd></div>
          <div><dt>学期</dt><dd>{course.rating.semester || "-"}</dd></div>
        </dl>
        {course.rating.message && <p>{course.rating.message}</p>}
      </div>

      <div className="detailSections">
        <DetailSection title="授课信息">
          <DetailItem label="教师" value={course.teachers || "-"} />
          <DetailItem label="时间" value={course.schedule.join("\n") || "-"} />
          <DetailItem label="地点" value={course.locations.join("\n") || "-"} />
        </DetailSection>
        <DetailSection title="课程标识">
          <DetailItem label="课程号" value={course.kch || "-"} mono />
          <DetailItem label="教学班号" value={course.jxb_id || "-"} mono />
          <DetailItem label="所属方案" value={course.group || "未分配"} />
        </DetailSection>
        <DetailSection title="容量状态">
          <DetailItem label="已选 / 容量" value={course.seat_text} />
          <DetailItem label="状态" value={course.availability_text} />
          <DetailItem label="评价更新" value={course.rating.updated_at || "-"} />
        </DetailSection>
      </div>
    </aside>
  );
}

function MonitorStatusPanel({
  autoSwap,
  interval,
  running,
  debug,
  releaseMode,
  onRunOnce,
  onStart,
  onStop,
}: {
  autoSwap: Snapshot["metrics"]["auto_swap"];
  interval: string;
  running: Set<string>;
  debug: boolean;
  releaseMode: boolean;
  onRunOnce: () => void;
  onStart: () => void;
  onStop: () => void;
}) {
  const monitorRunning = running.has("monitor");
  const onceRunning = running.has("once");
  const statusTone = monitorRunning ? "success" : onceRunning ? "primary" : "neutral";
  const statusLabel = monitorRunning ? "持续监控中" : onceRunning ? "单次检查中" : "未运行";
  const autoSwapTone = autoSwap === "enabled" ? "danger" : autoSwap === "dry_run" ? "primary" : "neutral";

  return (
    <section className="monitorPanel widePanel">
      <div className={`monitorStatusIcon ${statusTone}`}>
        <Activity size={22} />
      </div>
      <div className="monitorSummary">
        <span className="eyebrow">监控状态</span>
        <h2>{statusLabel}</h2>
        <p>{monitorRunning ? "正在按配置轮询课程余量" : onceRunning ? "正在执行一次本地监控流程" : "可以启动单次检查或持续监控"}</p>
      </div>
      <div className="monitorCards">
        <div className="monitorCard">
          <small>轮询间隔</small>
          <strong>{interval || "-"}</strong>
        </div>
        <div className="monitorCard">
          <small>自动换课</small>
          <Badge tone={autoSwapTone}>{statusText(autoSwap)}</Badge>
        </div>
        {!releaseMode && (
          <div className="monitorCard">
            <small>调试输出</small>
            <strong>{debug ? "开启" : "关闭"}</strong>
          </div>
        )}
      </div>
      <div className="monitorActions">
        <Button variant="outline" onClick={onRunOnce} disabled={onceRunning}>
          <Play />{onceRunning ? "检查中" : "单次检查"}
        </Button>
        {monitorRunning ? (
          <Button variant="destructive" onClick={onStop}><Square />停止监控</Button>
        ) : (
          <Button onClick={onStart}><Activity />开始持续监控</Button>
        )}
      </div>
    </section>
  );
}

function DetailSection({ title, children }: { title: string; children: React.ReactNode }) {
  return <section className="detailSection"><h3>{title}</h3><dl>{children}</dl></section>;
}

function DetailItem({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return <div><dt>{label}</dt><dd className={mono ? "mono" : ""}>{value}</dd></div>;
}

function Metric({ title, value }: { title: string; value: React.ReactNode }) {
  return (
    <div className="metric">
      <span>{title}</span>
      <strong>{value}</strong>
    </div>
  );
}

function Field({
  label,
  value,
  onChange,
  type = "text",
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  type?: string;
  placeholder?: string;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      <input type={type} value={value} placeholder={placeholder} onChange={(event) => onChange(event.target.value)} />
    </label>
  );
}

export default App;
