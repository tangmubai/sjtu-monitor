// Hide the console window for release builds; keep it in dev for logs.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    sjtu_monitor_desktop_lib::run()
}
