"""独立发行版的 Python 后端统一入口(PyInstaller 冻结成 sjtu-backend 可执行文件)。

Tauri 壳在源码模式下是 `python <script.py> <args...>`;打包模式下改为
`sjtu-backend <script.py> <args...>`,由本文件按脚本名分发到既有模块的 main:

    sjtu-backend gui_backend.py snapshot        -> gui_backend.main(["snapshot"])
    sjtu-backend monitor.py --once              -> monitor.main()   (读 sys.argv)
    sjtu-backend bootstrap.py --fetch-ratings-all -> bootstrap.main()

保持既有模块不改:monitor/bootstrap 用 argparse 读 sys.argv,这里先重写 sys.argv
再调用它们的 main,行为与直接 `python monitor.py --once` 完全一致。

另有一个 selfcheck 子命令,用于离线验证冻结产物是否完整(尤其 ddddocr/onnxruntime
这类带原生库、易漏打包的依赖),CI 和本机都可在不联网的情况下确认。
"""
from __future__ import annotations

import sys
from pathlib import Path


def _stem(target: str) -> str:
    return Path(target).name.lower().removesuffix(".py")


def _selfcheck() -> int:
    """导入并实例化关键依赖,确认冻结产物可独立运行(不联网)。"""
    import importlib

    for name in ("requests", "bs4", "PIL", "dotenv", "config", "gui_backend"):
        importlib.import_module(name)
    import ddddocr

    ddddocr.DdddOcr(beta=True, show_ad=False)
    print("selfcheck ok")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("usage: sjtu-backend <script.py|selfcheck> [args...]", file=sys.stderr)
        return 2

    target, rest = argv[0], argv[1:]
    stem = _stem(target)

    if stem == "selfcheck":
        return _selfcheck()

    if stem == "gui_backend":
        import gui_backend

        return gui_backend.main(rest)

    if stem in ("monitor", "bootstrap"):
        module = __import__(stem)
        # 让 argparse 看到与源码模式一致的 argv,再复用模块自身的 main。
        sys.argv = [f"{stem}.py", *rest]
        result = module.main()
        return int(result) if isinstance(result, int) else 0

    print(f"unknown backend target: {target}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
