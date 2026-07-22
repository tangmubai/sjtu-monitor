"""可写数据目录解析:统一决定 state.json/.env/secrets 等运行期文件写到哪里。

三种模式,优先级从高到低:
  1. 环境变量 SJTU_MONITOR_DATA_DIR —— Tauri 壳会把它指向平台 app-data 目录;
     从源码跑 (python gui.py) 时 lib.rs 会回落到仓库根,行为与历史一致。
  2. 打包后的独立程序 (PyInstaller 冻结) —— 装在 Program Files 下不可写,
     默认改用用户级目录:Windows=%APPDATA%\\sjtu-monitor,macOS/Linux 用对应 XDG。
  3. 其它情况 (源码运行、单元测试) —— 用本文件所在的仓库根,保持既有行为不变。

secure_store.py 与 config.py 都从这里取目录,避免各自散落一套路径逻辑,
也避免 config↔secure_store 的循环导入 (本模块不依赖任何项目内模块)。
"""
from __future__ import annotations

import errno
import os
import shutil
import sys
from pathlib import Path

# 仓库根 = 本文件所在目录。冻结后 __file__ 指向临时解包目录,所以冻结分支不用它。
REPO_ROOT = Path(__file__).resolve().parent

APP_DIR_NAME = "sjtu-monitor"
RELEASE_DATA_VERSION = "1"
RELEASE_DATA_MARKER = ".release-data-version"

# 0.x 内测版只产生测试数据。首个正式初始化版本清理这些遗留项一次，之后由
# RELEASE_DATA_MARKER 保护正常用户数据，升级不会重复执行。
_PRERELEASE_FILES = (
    ".env",
    "secrets.local.json",
    "state.json",
    "swap_state.json",
    "priority_groups.json",
    "user_settings.json",
    "catalog.json",
    "zzxk_capacity.json",
    "seat_details.json",
    "ratings.json",
    "changes.log",
)
_PRERELEASE_DIRS = ("captcha_debug",)


def replace_atomic(src: Path | str, dst: Path | str) -> None:
    """将 src 重命名为 dst,尽量原子;跨卷/重解析点时退化为非原子复制。

    所有状态文件都用“写 .tmp 再替换”的方式落盘。某些用户环境把 %APPDATA%
    通过 junction 迁到别的盘,或被网盘/安全软件以占位文件方式虚拟化,导致
    os.replace() 即使在同一目录内也抛 ERROR_NOT_SAME_DEVICE(Windows
    WinError 17 / POSIX EXDEV)。原子替换不可用时退化为复制内容再删除临时源,
    保证写入仍能完成(牺牲原子性,仅在这类环境的错误分支触发)。
    """
    src = Path(src)
    dst = Path(dst)
    try:
        os.replace(src, dst)
        return
    except OSError as exc:
        cross_device = exc.errno == errno.EXDEV or getattr(exc, "winerror", None) == 17
        if not cross_device:
            raise
    shutil.copyfile(src, dst)
    try:
        os.unlink(src)
    except OSError:
        pass


def _frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _platform_app_dir() -> Path:
    if sys.platform.startswith("win"):
        base = os.getenv("APPDATA") or os.path.expanduser("~")
        return Path(base) / APP_DIR_NAME
    if sys.platform == "darwin":
        return Path(os.path.expanduser("~/Library/Application Support")) / APP_DIR_NAME
    base = os.getenv("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return Path(base) / APP_DIR_NAME


def _resolve() -> Path:
    configured = os.getenv("SJTU_MONITOR_DATA_DIR", "").strip()
    if configured:
        return Path(configured)
    if _frozen():
        return _platform_app_dir()
    return REPO_ROOT


def data_dir() -> Path:
    """返回可写数据目录,必要时创建。"""
    path = _resolve()
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        # 仓库根这类既有目录一定存在;创建失败时直接返回,交由调用方在写入时报错。
        pass
    return path


def prepare_release_data(path: Path | None = None) -> Path:
    """首次正式发行时清除 0.x 内测数据，并写入一次性版本标记。

    仅当 Tauri 明确设置 SJTU_MONITOR_RELEASE=1 时生效；源码运行与测试默认
    完全不受影响。传入 path 便于在临时目录内做离线单元测试。
    """
    root = path or data_dir()
    if os.getenv("SJTU_MONITOR_RELEASE", "").strip() != "1":
        return root

    # 清理 0.x 遗留数据 + 写版本标记只是发行首启的一次性优化。它在后端 import
    # config 时运行,任何文件系统异常都不能让整个后端启动崩溃(例如 %APPDATA%
    # 被迁到别的盘导致 os.replace 抛 WinError 17;写标记失败时下次启动会再清一次
    # 已不存在的文件,是无害的幂等操作)。
    try:
        root.mkdir(parents=True, exist_ok=True)
        marker = root / RELEASE_DATA_MARKER
        try:
            if marker.read_text("utf-8").strip() == RELEASE_DATA_VERSION:
                return root
        except (FileNotFoundError, OSError, UnicodeError):
            pass

        for name in _PRERELEASE_FILES:
            try:
                (root / name).unlink()
            except OSError:
                pass
        for name in _PRERELEASE_DIRS:
            shutil.rmtree(root / name, ignore_errors=True)

        tmp = marker.with_suffix(".tmp")
        tmp.write_text(RELEASE_DATA_VERSION + "\n", "utf-8")
        replace_atomic(tmp, marker)
    except OSError:
        pass
    return root
