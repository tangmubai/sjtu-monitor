"""通知:跨平台桌面通知 + SMTP 邮件。批量变更打包发送,失败不抛异常。"""
from __future__ import annotations

import html
import logging
import platform
import shutil
import smtplib
import subprocess
from email.mime.text import MIMEText
from email.utils import formataddr
from typing import Sequence

import config

log = logging.getLogger(__name__)


def _format_change_line(c: dict) -> str:
    """单条变更格式化:'PHY1262-03 大学物理 已选 28→29'。"""
    name = c.get("kcmc", "")
    jxb = c.get("jxbmc", "")
    kind = c["kind"]
    if kind == "spot_open":
        return f"🔥 [有空位] {jxb} {name} — {c.get('msg','')}"
    if kind == "swap_result":
        if c.get("ok"):
            return f"✅ [SWAP成功] {jxb} {name} 已抢到!"
        st = c.get("status", "")
        if st == "FATAL_LOST":
            return f"❌ [SWAP致命错误] {jxb} {name} — 旧课退了选不回!立即人工处理!"
        return f"⚠️ [SWAP失败] {jxb} {name} — {st}"
    if kind == "added":
        return f"[新增] {jxb} {name}"
    if kind == "removed":
        return f"[移除] {jxb} {name}"
    if kind == "conflict_skipped":
        return (
            f"⏭️ [跳过换课-时间冲突] {jxb} {name} — "
            f"与「{c.get('conflict_group', '?')}」组当前持有课程冲突: {c.get('detail', '')}"
        )
    if kind == "schedule_unknown_skip":
        return (
            f"⏭️ [跳过换课-时间未知] {jxb} {name} — "
            "无法确认与其他方案组是否时间冲突,已保守跳过"
        )
    labels = {
        "yxzrs": "已选", "xzzrs": "选中", "cxrs": "抽选人数",
        "jxbrs": "班人数", "jxbxzrs": "班选中",
        "syddrs": "剩余", "jxbrl": "容量", "yl": "总容量",
        "krrl": "可容", "cxrl": "抽选容量",
    }
    parts = []
    for field, (old, new) in c["changes"].items():
        label = labels.get(field, field)
        parts.append(f"{label} {old}→{new}")
    return f"[变动] {jxb} {name} " + ", ".join(parts)


def _toast(title: str, body: str) -> None:
    system = platform.system()
    if system == "Windows":
        _windows_toast(title, body)
    elif system == "Darwin":
        _macos_notification(title, body)
    elif system == "Linux":
        _linux_notification(title, body)
    else:
        log.info("当前系统不支持桌面通知: %s", system or "unknown")


def _windows_toast(title: str, body: str) -> None:
    try:
        from win11toast import toast
        toast(title, body, duration="long")
        return
    except Exception as e:
        log.info("win11toast 不可用,尝试系统 Toast: %s", e)
    script = f"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] > $null
$xmlText = '<toast><visual><binding template="ToastGeneric"><text>{html.escape(title)}</text><text>{html.escape(body)}</text></binding></visual></toast>'
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml($xmlText)
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('SJTU Monitor').Show($toast)
"""
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        log.warning("Windows toast 失败: %s", e)


def _macos_notification(title: str, body: str) -> None:
    if not shutil.which("osascript"):
        log.info("osascript 不可用,跳过 macOS 桌面通知")
        return
    def apple_string(value: str) -> str:
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    try:
        subprocess.run(
            ["osascript", "-e", f"display notification {apple_string(body)} with title {apple_string(title)}"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        log.warning("macOS 通知失败: %s", e)


def _linux_notification(title: str, body: str) -> None:
    if not shutil.which("notify-send"):
        log.info("notify-send 不可用,跳过 Linux 桌面通知")
        return
    try:
        subprocess.run(
            ["notify-send", "SJTU Monitor", title, body],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        log.warning("Linux 通知失败: %s", e)


def _send_email(subject: str, body_lines: Sequence[str], heading: str = "选课信息变更") -> None:
    """构造并通过 SMTP_SSL 发送 HTML 邮件;配置不全或发送失败时抛异常。"""
    if not (config.SMTP_HOST and config.SMTP_USER and config.SMTP_PASS):
        raise RuntimeError("SMTP 未配置(需要服务器、用户名和密码)")
    body = (
        f"<html><body><h3>{html.escape(heading)}</h3><ul>"
        + "".join(f"<li>{line}</li>" for line in body_lines)
        + "</ul></body></html>"
    )
    msg = MIMEText(body, "html", "utf-8")
    msg["Subject"] = subject
    msg["From"] = formataddr(("SJTU 选课监控", config.MAIL_FROM))
    msg["To"] = config.MAIL_TO
    with smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, timeout=20) as s:
        s.login(config.SMTP_USER, config.SMTP_PASS)
        s.sendmail(config.MAIL_FROM, [config.MAIL_TO], msg.as_string())


def _email(subject: str, body_lines: Sequence[str]) -> None:
    if not config.EMAIL_ENABLED:
        log.info("邮件通知已关闭")
        return
    if not (config.SMTP_HOST and config.SMTP_USER and config.SMTP_PASS):
        log.info("SMTP 未配置,跳过邮件")
        return
    try:
        _send_email(subject, body_lines)
        log.info("邮件已发送 -> %s", config.MAIL_TO)
    except Exception as e:
        log.warning("邮件发送失败: %s", e)


def send_test() -> str:
    """发送一封测试邮件验证 SMTP 链路,返回收件地址;失败抛异常(供 GUI 测试按钮调用)。

    与常规通知不同,测试属于用户显式动作,不受 EMAIL_ENABLED 开关限制。
    """
    from datetime import datetime

    lines = [
        "这是一封来自 SJTU 选课监控的测试邮件,收到即说明通知链路正常。",
        f"SMTP 服务器: {config.SMTP_HOST}:{config.SMTP_PORT} (SSL)",
        f"发件人: {config.MAIL_FROM}",
        f"发送时间: {datetime.now().isoformat(timespec='seconds')}",
    ]
    _send_email("[测试] SJTU 选课监控通知链路正常", lines, heading="测试邮件")
    return config.MAIL_TO


def send(changes: list[dict]) -> None:
    if not changes:
        return
    lines = [_format_change_line(c) for c in changes]
    has_fatal = any(
        c.get("kind") == "swap_result" and c.get("status") == "FATAL_LOST"
        for c in changes
    )
    has_swap_ok = any(
        c.get("kind") == "swap_result" and c.get("ok") for c in changes
    )
    has_spot = any(c["kind"] == "spot_open" for c in changes)
    if has_fatal:
        prefix = "❌ FATAL"
    elif has_swap_ok:
        prefix = "✅ 抢到了"
    elif has_spot:
        prefix = "🔥 有空位"
    else:
        prefix = "[选课变更]"
    if len(changes) == 1:
        subject = f"{prefix} {lines[0][:80]}"
        toast_body = lines[0]
    else:
        subject = f"{prefix} {len(changes)} 条变动"
        toast_body = "\n".join(lines[:3]) + (
            f"\n... 共 {len(changes)} 条" if len(changes) > 3 else ""
        )
    if has_fatal:
        title = "❌ SJTU 选课 FATAL!"
    elif has_swap_ok:
        title = "✅ SJTU 抢课成功"
    elif has_spot:
        title = "🔥 SJTU 选课有空位!"
    else:
        title = "SJTU 选课监控"
    _toast(title, toast_body)
    _email(subject, lines)
