import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("fork_check", ROOT / "scripts/check-fork.py")
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def fixture(root):
    for name in ("Sources/DigUpApp/Localization", "Sources/DigUpKit", "shell/ui"):
        (root / name).mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / CHECK.MODEL, root / CHECK.MODEL)
    (root / "Sources/DigUpApp/Localization/zh-Hans.json").write_text(
        json.dumps({"Hello": "你好", "Hi {0}, {1}": "你好 {0}，{1}", "inner": "内部"}),
        encoding="utf-8",
    )
    (root / "shell/ui/zh-Hans.json").write_text(
        json.dumps({"Hello": "你好", "{0} files": "{0} 文件", "{0} file": "{0} 文件",
                    "Size {0}": "大小 {0}"}), encoding="utf-8",
    )
    (root / "Sources/DigUpApp/App.swift").write_text('L10n.tr("Hello")\n', encoding="utf-8")
    (root / "shell/ui/app.js").write_text('t("Hello");\n', encoding="utf-8")


class ForkCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        fixture(self.root)

    def test_current_resources_not_snapshots(self):
        self.assertEqual(CHECK.check(self.root)[0], [])
        (self.root / "Sources/DigUpApp/Localization/zh-Hans.json").write_text("{}")
        self.assertIn("missing translation 'Hello'", CHECK.check(self.root)[0][0])

    def test_missing_swift_literal_and_nested_interpolation(self):
        source = '''// L10n.tr("ignored")
/* nested /* L10n.tr("ignored") */ comment */
L10n.tr("Hi \\(object.name("quoted")), \\(L10n.tr("inner"))")
L10n.tr("New \\(count) message")
'''
        (self.root / "Sources/DigUpApp/App.swift").write_text(source)
        errors, _ = CHECK.check(self.root)
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("'New {0} message'", errors[0])
        self.assertIn("App.swift:4", errors[0])

    def test_swift_escapes_raw_and_unicode(self):
        extracted = list(CHECK.messages(
            r'L10n.tr(#"Hi \#(name), \#(other)"#); L10n.tr("A\n\u{2026}")', True))
        self.assertEqual([key for _, key in extracted], ["Hi {0}, {1}", "A\n…"])

    def test_js_ternary_interpolation_concat_and_regex(self):
        source = '''const r = /['"`]/g;
// t("ignored")
t(`Size ${bytes(size)}`);
t(`${count} ${count === 1 ? "file" : "files"}`);
t(active ? "Hel" + "lo" : "New message");
'''
        (self.root / "shell/ui/app.js").write_text(source)
        errors, _ = CHECK.check(self.root)
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("'New message'", errors[0])
        values = json.loads((self.root / "shell/ui/zh-Hans.json").read_text())
        del values["{0} file"]
        (self.root / "shell/ui/zh-Hans.json").write_text(json.dumps(values))
        self.assertEqual(len(CHECK.check(self.root)[0]), 2)

    def test_runtime_only_calls_are_explicit(self):
        (self.root / "shell/ui/app.js").write_text("t(runtimeMessage);")
        errors, dynamic = CHECK.check(self.root)
        self.assertEqual(errors, [])
        self.assertEqual(dynamic, 1)

    def test_new_upstream_ui_literals_require_localization(self):
        path = self.root / "Sources/DigUpApp/App.swift"
        path.write_text('Text("DigUp")\nButton("New feature") {}\nText(L10n.tr("Hello"))')
        errors, _ = CHECK.check(self.root)
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("unlocalized UI literal 'New feature'", errors[0])

    def test_invalid_catalog_and_tokens(self):
        path = self.root / "shell/ui/zh-Hans.json"
        for invalid in ('{"Hello":"a","Hello":"b"}', '{"Hello":""}', '[]'):
            path.write_text(invalid)
            with self.assertRaises(ValueError):
                CHECK.check(self.root)
        path.write_text('{"Hello":"bad {9}"}')
        self.assertIn("invalid interpolation tokens", CHECK.check(self.root)[0][0])

    def test_model_changes_every_pin_field(self):
        git(self.root, "init", "-b", "main")
        git(self.root, "config", "user.name", "Test")
        git(self.root, "config", "user.email", "test@example.invalid")
        git(self.root, "add", ".")
        git(self.root, "commit", "-m", "base")
        base = git(self.root, "rev-parse", "HEAD")
        path = self.root / CHECK.MODEL
        original = path.read_text()
        self.assertEqual(CHECK.check(self.root, base)[0], [])
        for old, new in (
            ("ggml-org/embeddinggemma-2-GGUF", "example/model"),
            ("bfcd298762cc34d0357ece5ebdd31791a3a374d8", "a" * 40),
            ("embeddinggemma-2-Q8_0.gguf", "replacement.gguf"),
            ("309_855_456", "309_855_457"),
            ("2188ac1deca4b77dffefd603c2776a9d76d9d74ec01841392982ebb840b09135", "b" * 64),
        ):
            with self.subTest(field=old):
                path.write_text(original.replace(old, new))
                self.assertIn("MODEL PIN CHANGE", CHECK.check(self.root, base)[0][0])
        path.write_text(original.replace("309_855_456", "0"))
        with self.assertRaises(ValueError):
            CHECK.check(self.root, base)

    def test_missing_resource_or_bad_base_fails(self):
        (self.root / "shell/ui/zh-Hans.json").unlink()
        with self.assertRaises(OSError):
            CHECK.check(self.root)


if __name__ == "__main__":
    unittest.main()
