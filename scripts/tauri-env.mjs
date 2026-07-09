import { existsSync } from "node:fs";
import { spawn } from "node:child_process";
import { join } from "node:path";

const rcCandidates = [
  "C:\\Program Files (x86)\\Windows Kits\\10\\bin\\10.0.26100.0\\arm64\\rc.exe",
  "C:\\Program Files (x86)\\Windows Kits\\10\\bin\\10.0.26100.0\\x64\\rc.exe",
  "C:\\Program Files (x86)\\Windows Kits\\10\\bin\\10.0.26100.0\\x86\\rc.exe",
];

const env = { ...process.env };
env.SJTU_MONITOR_ROOT ??= process.cwd();
if (!env.RC) {
  const rc = rcCandidates.find((candidate) => existsSync(candidate));
  if (rc) env.RC = rc;
}

const args = process.argv.slice(2);
const bin = process.platform === "win32"
  ? join("node_modules", ".bin", "tauri.cmd")
  : join("node_modules", ".bin", "tauri");
const command = process.platform === "win32" ? "cmd.exe" : bin;
const commandArgs = process.platform === "win32"
  ? ["/d", "/c", bin, ...args]
  : args;

const child = spawn(command, commandArgs, {
  stdio: "inherit",
  shell: false,
  env,
});

child.on("exit", (code, signal) => {
  if (signal) {
    process.kill(process.pid, signal);
  } else {
    process.exit(code ?? 1);
  }
});
