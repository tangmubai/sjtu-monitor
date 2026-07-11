import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { completeOnboarding, saveSettings, startProcess, type Snapshot } from "./api";
import App from "./App";

const tauriWindowMock = vi.hoisted(() => ({
  closeHandlers: [] as Array<(event: { preventDefault: () => void }) => void>,
}));

const snapshot: Snapshot = {
  generated_at: "2026-07-09 12:00:00",
  metrics: {
    queries: 1,
    groups: 1,
    snapshot: 1,
    watched: 1,
    open_courses: 1,
    interval: "60-120",
    auto_swap: "off",
  },
  settings: {
    jaccount_user: "",
    jaccount_pass: "",
    course_plus_password: "",
    poll_min: 60,
    poll_max: 120,
    email_enabled: false,
    smtp_host: "",
    smtp_port: 465,
    smtp_user: "",
    smtp_pass: "",
    mail_from: "",
    mail_to: "",
  },
  onboarding: {
    completed: true,
    has_account: false,
    catalog_ready: false,
  },
  user: {
    name: "测试用户",
    student_id: "123",
    class_name: "测试班",
    major: "测试专业",
    term: "2025-2026-3",
  },
  groups: [{
    name: "大学物理",
    is_pe: false,
    priority: ["class-1"],
    held: null,
    held_label: "未持有",
    watched_count: 1,
    fatal: false,
    members: [],
  }],
  courses: [{
    jxb_id: "class-1",
    title: "大学物理",
    class_name: "大学物理-01",
    summary: "张老师 · 周一第1-2节",
    detail: "",
    teachers: "张老师",
    schedule: ["周一第1-2节"],
    locations: ["东上院101"],
    search_text: "大学物理 张老师 PHY1001",
    seat_text: "20 / 30",
    availability: "open",
    availability_text: "有空位",
    group: "大学物理",
    chosen: false,
    category: "公共基础课",
    rating_text: "9.2 / 18评",
    rating: {
      status: "rated",
      score: 9.23456,
      count: 18,
      teacher: "张老师",
      semester: "2025-2026-3",
      updated_at: "2026-07-09 11:00:00",
      message: null,
    },
    kch: "PHY1001",
    sksj: "星期一第1-2节{1-16周}",
  }, {
    jxb_id: "class-2",
    title: "线性代数",
    class_name: "线性代数-01",
    summary: "李老师 · 星期二第1-2节",
    detail: "",
    teachers: "李老师",
    schedule: ["星期二第1-2节{1-16周}"],
    locations: ["东上院102"],
    search_text: "线性代数 李老师 MATH1002",
    seat_text: "10 / 30",
    availability: "open",
    availability_text: "有空位",
    group: null,
    chosen: false,
    category: "公共基础课",
    rating_text: "8.8 / 10评",
    rating: {
      status: "rated",
      score: 8.8,
      count: 10,
      teacher: "李老师",
      semester: "2025-2026-3",
      updated_at: "2026-07-09 11:00:00",
      message: null,
    },
    kch: "MATH1002",
    sksj: "星期二第1-2节{1-16周}",
  }],
  state_rows: [],
  swap_state: { completed: [], fatal: [], fatal_groups: [] },
  swap_history: [],
  logs: ["persisted command output"],
  categories: ["公共基础课"],
};

vi.mock("@tauri-apps/api/event", () => ({
  listen: vi.fn(async () => () => {}),
}));

vi.mock("@tauri-apps/api/window", () => ({
  getCurrentWindow: () => ({
    onCloseRequested: vi.fn(async (handler) => {
      tauriWindowMock.closeHandlers.push(handler);
      return () => {};
    }),
  }),
}));

vi.mock("./api", async (importOriginal) => {
  const original = await importOriginal<typeof import("./api")>();
  return {
    ...original,
    isTauri: () => true,
    loadSnapshot: vi.fn(async () => snapshot),
    pollProcesses: vi.fn(async () => []),
    saveGroups: vi.fn(async () => ({ ok: true, warnings: [], duplicates: {}, unresolved: [], course_count: 1 })),
    saveSettings: vi.fn(async () => ({ ok: true })),
    completeOnboarding: vi.fn(async () => ({ ok: true })),
    setAutoSwap: vi.fn(async () => ({ ok: true })),
    startProcess: vi.fn(async () => undefined),
    stopProcess: vi.fn(async () => undefined),
  };
});

afterEach(() => {
  cleanup();
  snapshot.onboarding = { completed: true, has_account: false, catalog_ready: false };
  vi.clearAllMocks();
});

describe("first-run onboarding", () => {
  it("saves credentials before an explicit course-sync action", async () => {
    snapshot.onboarding = { completed: false, has_account: false, catalog_ready: false };
    const user = userEvent.setup();
    render(<App />);

    await screen.findByRole("heading", { name: "连接 JAccount" });
    expect(startProcess).not.toHaveBeenCalled();
    await user.type(screen.getByLabelText("JAccount"), "student");
    await user.type(screen.getByLabelText("JAccount 密码"), "secret");
    await user.click(screen.getByRole("button", { name: "保存并继续" }));

    await waitFor(() => expect(saveSettings).toHaveBeenCalled());
    expect(await screen.findByRole("heading", { name: "同步课程目录" })).toBeInTheDocument();
    expect(startProcess).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "开始同步课程" }));
    expect(startProcess).toHaveBeenCalledWith("bootstrap", "bootstrap.py", [], false);
  });

  it("finishes setup only after a catalog is available", async () => {
    snapshot.onboarding = { completed: false, has_account: true, catalog_ready: true };
    const user = userEvent.setup();
    render(<App />);

    await screen.findByRole("heading", { name: "创建自己的选课方案" });
    await user.click(screen.getByRole("button", { name: "进入课程方案" }));
    await waitFor(() => expect(completeOnboarding).toHaveBeenCalled());
  });
});

describe("course workspace", () => {
  it("keeps command output off the overview and in logs", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("heading", { name: "当前用户" });
    expect(screen.queryByText("近期活动")).not.toBeInTheDocument();
    expect(screen.queryByText(/persisted command output/)).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "未运行" })).toBeInTheDocument();
    expect(screen.getByText("轮询间隔")).toBeInTheDocument();
    expect(screen.getByText("调试输出")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "日志" }));
    expect(await screen.findByText(/persisted command output/)).toBeInTheDocument();
  });

  it("shows separate refresh actions, flat plan tabs, and structured rating details", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("heading", { name: "当前用户" });
    await user.click(screen.getByRole("button", { name: "课程方案" }));

    expect(screen.getByRole("button", { name: "获取全量课程" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "获取全部评价" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /大学物理/ })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /PHY1001/ }));
    await waitFor(() => expect(screen.getByText("9.2")).toBeInTheDocument());
    expect(screen.getByText("东上院101")).toBeInTheDocument();
    expect(screen.getByText("18")).toBeInTheDocument();
  });

  it("uses single click for details, explicit checkboxes for batches, and double click for quick add", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByRole("heading", { name: "当前用户" });
    await user.click(screen.getByRole("button", { name: "课程方案" }));

    const linearAlgebra = screen.getByRole("button", { name: /线性代数/ });
    const batchButton = screen.getByRole("button", { name: "批量加入当前组 (0)" });
    await user.click(linearAlgebra);
    expect(batchButton).toBeDisabled();
    expect(screen.getAllByText("MATH1002").length).toBeGreaterThan(1);

    await user.click(screen.getByRole("checkbox", { name: "批量选择 线性代数" }));
    expect(screen.getByRole("button", { name: "批量加入当前组 (1)" })).toBeEnabled();

    await user.click(screen.getByRole("checkbox", { name: "批量选择 线性代数" }));
    await user.dblClick(linearAlgebra);
    await waitFor(() => {
      expect(screen.getAllByRole("button", { name: /线性代数/ }).length).toBeGreaterThan(1);
    });
  });

  it("blocks desktop close when course-plan changes are not saved", async () => {
    const user = userEvent.setup();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<App />);
    await screen.findByRole("heading", { name: "当前用户" });
    await user.click(screen.getByRole("button", { name: "课程方案" }));
    const handlerCount = tauriWindowMock.closeHandlers.length;
    await user.click(screen.getByRole("checkbox", { name: "体育课方案" }));

    await waitFor(() => expect(tauriWindowMock.closeHandlers.length).toBeGreaterThan(handlerCount));
    const preventDefault = vi.fn();
    tauriWindowMock.closeHandlers[tauriWindowMock.closeHandlers.length - 1]?.({ preventDefault });
    expect(confirm).toHaveBeenCalledWith("选课方案还有未保存的修改。确定放弃修改并退出吗？");
    expect(preventDefault).toHaveBeenCalled();
  });

  it("toggles dark mode without affecting workspace state", async () => {
    const user = userEvent.setup();
    const { container } = render(<App />);
    await screen.findByRole("heading", { name: "当前用户" });

    expect(screen.queryByRole("button", { name: "深色" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "设置" }));
    expect(screen.getByRole("button", { name: "跟随系统" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "深色" }));
    expect(container.querySelector(".app")).toHaveClass("theme-dark");
    expect(screen.getByText("当前实际显示为深色模式。")).toBeInTheDocument();
  });
});
