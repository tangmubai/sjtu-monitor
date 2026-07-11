# 交我选

### 免责声明：本项目仅用于监控上海交通大学 i.sjtu.edu.cn 推荐选课页面的教学班数据（已选/容量）并在数据发生变化时通过桌面通知和电子邮件发送告警。项目不具备也不应被用于自动抢课、批量代选、或绕过学校或第三方服务的任何访问控制或风控措施。作者已尽力提供正确的实现，但不对因使用本项目或基于本项目的二次开发所导致的任何直接、间接、附带、特殊或衍生性损失承担责任，包括但不限于数据丢失、账户封禁、校纪处分、法律责任或其他经济损失。在适用法律允许的最大范围内，作者对因使用本软件而产生的所有责任均予以否认。

## 项目简介
本项目主要实现监控上海交通大学 `i.sjtu.edu.cn` 推荐选课页面中的指定教学班。人数或容量变化时，程序会写入日志，并通过系统桌面通知和可选的 SMTP 邮件告警。

## 环境要求

- Python 3.10 或更高版本
- Windows 10/11、macOS 或 Linux（Linux 桌面通知需要系统提供 `notify-send`）
- Node.js 20 或更高版本（Tauri/React 前端）
- Rust stable 工具链（Tauri 桌面壳）
- 可正常访问交我办与 jAccount

## 安装

```powershell
git clone https://github.com/tangmubai/sjtu-monitor.git
cd sjtu-monitor

python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
npm install

Copy-Item .env.example .env
notepad .env
```

`.env` 仍可用于首次导入旧配置。通过桌面设置页保存后，密码会迁移到系统安全存储；`.env` 中的敏感键会被移除。

```dotenv
JACCOUNT_USER=your_jaccount_id
JACCOUNT_PASS=your_password
```

邮件是可选的。留空 `SMTP_HOST`、`SMTP_USER` 或 `SMTP_PASS` 时，程序会跳过邮件发送。

```dotenv
SMTP_HOST=smtp.qq.com
SMTP_PORT=465
SMTP_USER=youraddress@qq.com
SMTP_PASS=your_smtp_authorization_code
MAIL_FROM=youraddress@qq.com
MAIL_TO=youraddress@qq.com

POLL_MIN=60
POLL_MAX=120
```

`SMTP_PASS` 应填写邮箱服务商生成的 SMTP 授权码，而不是邮箱登录密码。

## 配置监控课程

课程、教学班及学期参数均在 `config.py` 中配置。这些值来自选课页面的实际请求，学期或个人信息变化后需要重新核对。

### 1. 查询课程

在 `KCH_QUERIES` 中为每门课程配置查询方式：

```python
KCH_QUERIES = {
    "MATH1206": {
        "endpoint": "display",
        "kch_id": "MA1206",
        "jxb_id": "用于查询该课程的教学班 ID",
    },
    "PE003C20": {
        "endpoint": "pe",
        "kch_id": "PE003C20",
    },
}
```

- `display`：普通课程，使用 `DISPLAY_URL`。
- `pe`：体育课程，使用 `JXB_LIST_URL`。
- `_DISPLAY_COMMON` 和 `_PE_COMMON` 中的学年、学期、专业等参数必须与当前账号一致。

可在桌面端“选课”页面刷新课程目录后查看当前已选课程及其 `jxb_id`。

### 2. 设置优先级

`PRIORITY_GROUPS` 中每组的 `priority` 按“最想要”到“当前持有”排列，最后一项是程序启动时认为已持有的教学班：

```python
PRIORITY_GROUPS = {
    "MATH": {
        "is_pe": False,
        "priority": [
            "最高优先级教学班 ID",
            "次高优先级教学班 ID",
            "当前持有教学班 ID",
        ],
    },
}
```

程序只监控当前持有项之前的教学班，因此只会向更高优先级升级。自动换班成功后，结果记录在 `swap_state.json`，后续轮询会以已成功目标作为新的持有项，并缩小监控范围。

修改优先级或实际持有课程后，应删除旧的 `swap_state.json`，否则历史成功记录可能影响监控范围。

## 运行

单次拉取，用于验证登录和接口配置：

```powershell
python monitor.py --once
```

持续轮询：

```powershell
python monitor.py
```

显示详细日志：

```powershell
python monitor.py --debug
```

图形界面：

```powershell
python gui.py
```

`gui.py` 现在是 Tauri 桌面前端的兼容启动器。它会把当前 conda 环境中的 Python 解释器传给 Tauri/Rust 桥接层，再由桥接层调用 `gui_backend.py`、`monitor.py` 和 `bootstrap.py`。开发模式也可直接运行：

```powershell
npm run tauri dev
```

Tauri/React 前端支持运行监控、查看课程快照、换课记录与合并日志，并通过“课程目录 → 选课方案 → 优先级”流程配置教学班。课程目录只保存和显示空位状态；教学班加入方案后才异步查询人数与容量，结果写入 `seat_details.json`，不会改写监控基线 `state.json`。

Windows 发行版首次启动会引导保存 JAccount、由用户显式触发课程同步，再进入课程方案页。发行包不内置测试课程、选课方案或运行缓存；0.x 内测数据只在首次正式初始化时清理一次，后续升级保留用户数据。

课程目录以“课程名称 · 教师”为主标题，时间、地点、课程号和教学班号作为摘要；选中教学班后，下方会显示可复制的完整教师、时间、地点和 ID 信息。搜索支持课程、教师、编号、时间与地点。

“用户设置”页可编辑 JAccount、轮询间隔和 SMTP 邮件参数。密码写入系统安全存储，非敏感配置写入本地 `.env`，邮件开关及其他非敏感设置写入 `user_settings.json`；已运行的监控进程需重启后才会使用新参数。

首次运行只建立 `state.json` 快照，不会发送普通变更通知。后续轮询会比较人数、容量、剩余名额以及教学班新增/移除情况；检测到 `jxbxzrs < jxbrl` 时会额外发送空位告警。若已显式开启自动换班，程序从首轮起就会检查所有当前有空位的高优先级目标，而不是只等待一次“满员变为空闲”的状态变化。

## 工作原理

1. `login.py` 访问 jAccount 登录入口，解析登录字段并使用 `ddddocr` 识别验证码。
2. `monitor.py` 按 `KCH_QUERIES` 拉取普通课和体育课教学班，并按 `jxb_id` 去重。
3. 程序将完整快照写入 `state.json`，但只对当前优先级范围内的教学班产生通知。
4. 会话失效时自动重新登录；网络错误按最长 10 分钟退避后重试。
5. 每轮成功后在 `POLL_MIN` 到 `POLL_MAX` 秒之间随机等待。

## 生成文件

| 文件或目录 | 用途 |
|---|---|
| `state.json` | 最近一次完整教学班快照 |
| `changes.log` | 检测到的变更及自动换班结果 |
| `swap_state.json` | 已完成和致命失败的自动换班目标 |
| `seat_details.json` | 仅供 GUI 方案页显示的人数/容量缓存 |
| `captcha_debug/` | 登录调试时保存的验证码图片 |

这些文件以及 `.env` 均不应提交到版本库。

## 常见问题

- **首轮没有通知**：这是预期行为，首轮只创建基准快照。
- **登录失败**：用 `python monitor.py --debug` 查看状态码和 OCR 日志，确认 jAccount 密码及验证码识别结果。
- **登录成功但课程为空**：核对 `xkxnm`、`xkxqm`、`zyh_id`、`kch_id`、查询用 `jxb_id` 和接口类型。
- **一直没有空位告警**：确认接口返回的 `jxbxzrs` 与 `jxbrl` 是当前阶段实际使用的人数和容量字段。
- **Windows 通知不显示**：检查系统通知权限；邮件和 `changes.log` 不受影响。
- **程序持续重登录**：通常表示接口重定向、参数过期或返回了非 JSON 页面，可用 `--debug` 查看响应摘要。

## 后台运行

可以在 Windows 任务计划程序中使用虚拟环境解释器启动：

```text
C:\path\to\sjtu-monitor\.venv\Scripts\pythonw.exe C:\path\to\sjtu-monitor\monitor.py
```

将“起始于”设置为项目目录，便于定位日志和状态文件。

## 前端开发与验证

Tauri 前端由 React + TypeScript 实现，Python 后端业务规则仍由 `monitor.py`、`swap.py`、`bootstrap.py`、`config.py` 和 `gui_backend.py` 负责。不要在前端重新实现自动换课决策。

常用离线检查：

```powershell
python -m py_compile config.py monitor.py swap.py bootstrap.py course_plus.py gui_backend.py gui.py
python -m unittest -v test_gui_logic.py test_gui_responsive.py
npm run typecheck
npm run build
```

`npm run tauri dev` 会启动本地桌面窗口。涉及 `i.sjtu.edu.cn` 或 `course.sjtu.plus` 的按钮仍会调用真实外部接口，开发或验证时不要擅自点击联网刷新。

## 提示

- 当前仓库中的课程参数和教学班 ID 与具体账号、学期相关，不适合作为通用默认值。
- 不要把轮询间隔设得过短；频繁请求可能触发限制或影响服务。
- 除非你知道自己在做什么，请保持```AUTO_SWAP = False```

## 许可证

本项目采用 [MIT License](LICENSE)。
您可以自由使用、修改和分发本项目代码，但必须保留原版权声明和许可证文本。
