//! Fork updates are manual. Never consult or install the upstream feed.
use serde_json::Value;
use tauri::AppHandle;
use tauri_plugin_opener::OpenerExt;

pub const RELEASES: &str = "https://github.com/brucetoo/DigUp/releases";

#[derive(Default)]
pub struct Updates;

impl Updates {
    pub fn snapshot(&self) -> Value { Value::Null }
    pub fn menu_text(&self) -> Option<(String, bool)> { None }
}

pub fn installs_itself() -> bool { false }
pub fn start(_: &AppHandle) {}

pub async fn check(app: &AppHandle, asked: bool) {
    if asked { offer(app); }
}

pub fn offer(app: &AppHandle) {
    let _ = app.opener().open_url(RELEASES, None::<&str>);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_manual_fork_updates() {
        assert!(!installs_itself());
        assert!(Updates.snapshot().is_null());
        assert!(Updates.menu_text().is_none());
        assert_eq!(RELEASES, "https://github.com/brucetoo/DigUp/releases");
    }
}
