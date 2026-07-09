"""Small OS credential-store wrapper for local desktop secrets."""
from __future__ import annotations

import base64
import ctypes
import json
import platform
import shutil
import subprocess
from ctypes import wintypes
from pathlib import Path

SERVICE = "com.tangmubai.sjtu-monitor"
ROOT = Path(__file__).resolve().parent
WINDOWS_SECRET_FILE = ROOT / "secrets.local.json"


class SecretStoreError(RuntimeError):
    pass


def _system() -> str:
    return platform.system().lower()


def backend_name() -> str:
    name = _system()
    if name == "windows":
        return "Windows DPAPI"
    if name == "darwin":
        return "macOS Keychain"
    if name == "linux" and shutil.which("secret-tool"):
        return "Secret Service"
    return "unavailable"


def is_available() -> bool:
    return backend_name() != "unavailable"


def get_secret(name: str) -> str:
    system = _system()
    if system == "windows":
        return _windows_get(name)
    if system == "darwin":
        return _macos_get(name)
    if system == "linux" and shutil.which("secret-tool"):
        return _linux_get(name)
    return ""


def set_secret(name: str, value: str) -> None:
    if not value:
        return
    system = _system()
    if system == "windows":
        _windows_set(name, value)
        return
    if system == "darwin":
        _macos_set(name, value)
        return
    if system == "linux" and shutil.which("secret-tool"):
        _linux_set(name, value)
        return
    raise SecretStoreError(
        "当前系统没有可用的安全凭据存储；请安装 secret-tool/libsecret，"
        "或改用系统支持的钥匙串后再保存密码。"
    )


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob_from_bytes(data: bytes) -> tuple[_DATA_BLOB, object]:
    buffer = ctypes.create_string_buffer(data)
    return _DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def _bytes_from_blob(blob: _DATA_BLOB) -> bytes:
    try:
        return ctypes.string_at(blob.pbData, blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob.pbData)


def _windows_protect(value: str) -> str:
    source, _buffer = _blob_from_bytes(value.encode("utf-8"))
    target = _DATA_BLOB()
    ok = ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(source), None, None, None, None, 0, ctypes.byref(target)
    )
    if not ok:
        raise SecretStoreError("Windows DPAPI 加密失败")
    return base64.b64encode(_bytes_from_blob(target)).decode("ascii")


def _windows_unprotect(value: str) -> str:
    source, _buffer = _blob_from_bytes(base64.b64decode(value.encode("ascii")))
    target = _DATA_BLOB()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(source), None, None, None, None, 0, ctypes.byref(target)
    )
    if not ok:
        return ""
    return _bytes_from_blob(target).decode("utf-8")


def _windows_read() -> dict[str, str]:
    if not WINDOWS_SECRET_FILE.exists():
        return {}
    try:
        data = json.loads(WINDOWS_SECRET_FILE.read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _windows_get(name: str) -> str:
    encrypted = _windows_read().get(name)
    return _windows_unprotect(encrypted) if encrypted else ""


def _windows_set(name: str, value: str) -> None:
    data = _windows_read()
    data[name] = _windows_protect(value)
    tmp = WINDOWS_SECRET_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")
    tmp.replace(WINDOWS_SECRET_FILE)


def _macos_get(name: str) -> str:
    result = subprocess.run(
        ["security", "find-generic-password", "-s", SERVICE, "-a", name, "-w"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.stdout.rstrip("\n") if result.returncode == 0 else ""


def _macos_set(name: str, value: str) -> None:
    subprocess.run(
        [
            "security",
            "add-generic-password",
            "-U",
            "-s",
            SERVICE,
            "-a",
            name,
            "-w",
            value,
        ],
        check=True,
    )


def _linux_get(name: str) -> str:
    result = subprocess.run(
        ["secret-tool", "lookup", "service", SERVICE, "account", name],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.stdout.rstrip("\n") if result.returncode == 0 else ""


def _linux_set(name: str, value: str) -> None:
    subprocess.run(
        ["secret-tool", "store", "--label", f"SJTU Monitor {name}", "service", SERVICE, "account", name],
        input=value,
        text=True,
        check=True,
    )
