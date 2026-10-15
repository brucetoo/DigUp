use crate::settings::Language;
use std::collections::BTreeMap;
use std::sync::OnceLock;

pub fn text(language: Language, english: &str) -> String {
    if language == Language::English {
        return english.into();
    }
    static CATALOG: OnceLock<BTreeMap<String, String>> = OnceLock::new();
    let catalog = CATALOG.get_or_init(|| serde_json::from_str(include_str!("../../ui/zh-Hans.json")).expect("valid translations"));
    if let Some(translated) = catalog.get(english) {
        return translated.clone();
    }
    let mut patterns: Vec<_> = catalog.iter().filter(|(key, _)| key.contains("{0}")).collect();
    patterns.sort_by_key(|(key, _)| std::cmp::Reverse(key.len()));
    for (pattern, translated) in patterns {
        if let Some(values) = captures(pattern, english) {
            let mut result = translated.clone();
            for (index, value) in values.iter().enumerate() {
                result = result.replace(&format!("{{{index}}}"), value);
            }
            return result;
        }
    }
    english.into()
}

fn captures(pattern: &str, text: &str) -> Option<Vec<String>> {
    let mut rest = text;
    let mut pattern = pattern;
    let mut values = Vec::new();
    loop {
        let token = format!("{{{}}}", values.len());
        let Some((prefix, tail)) = pattern.split_once(&token) else {
            return (rest == pattern).then_some(values);
        };
        rest = rest.strip_prefix(prefix)?;
        let next = format!("{{{}}}", values.len() + 1);
        let suffix = tail.split_once(&next).map(|(literal, _)| literal).unwrap_or(tail);
        let end = if suffix.is_empty() { rest.len() } else { rest.find(suffix)? };
        values.push(rest[..end].into());
        rest = &rest[end..];
        pattern = tail;
    }
}

pub fn snapshot(language: Language, mut value: serde_json::Value) -> serde_json::Value {
    for key in ["line", "badge", "caveat"] {
        if let Some(english) = value[key].as_str() {
            value[key] = text(language, english).into();
        }
    }
    value["language"] = language.code().into();
    value
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn templates_preserve_values() {
        assert_eq!(captures("Indexing {0}% · {1} of {2}", "Indexing 42% · 129 of 306"),
                   Some(vec!["42".into(), "129".into(), "306".into()]));
        assert_eq!(captures("Indexing {0}%", "Starting"), None);
        assert_eq!(text(Language::English, "Starting…"), "Starting…");
        assert_ne!(text(Language::Chinese, "Starting…"), "Starting…");
    }

    #[test]
    fn snapshots_follow_language_without_changing_protocol_values() {
        let original = serde_json::json!({"line": "Indexing 42% · 129 of 306", "badge": "Indexing 42%",
            "caveat": null, "activity": "indexing", "model": {"state": "ready"}, "files": 306});
        let chinese = snapshot(Language::Chinese, original.clone());
        assert_eq!(chinese["line"], "索引进度 42% · 129 / 306");
        assert_eq!(chinese["language"], "zh-Hans");
        assert_eq!(chinese["activity"], original["activity"]);
        assert_eq!(chinese["model"], original["model"]);
        let english = snapshot(Language::English, original.clone());
        assert_eq!(english["line"], original["line"]);
        assert_eq!(english["language"], "en");
    }
}
