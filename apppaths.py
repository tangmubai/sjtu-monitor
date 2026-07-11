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

import os
import sys
from pathlib import Path

# 仓库根 = 本文件所在目录。冻结后 __file__ 指向临时解包目录,所以冻结分支不用它。
REPO_ROOT = Path(__file__).resolve().parent

APP_DIR_NAME = "sjtu-monitor"


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
