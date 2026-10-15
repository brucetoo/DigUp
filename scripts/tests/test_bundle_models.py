import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "bundle-models.py"


class BundleModelsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("bundle_models", SCRIPT)
        cls.bundle = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.bundle)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.out = self.root / "models"
        self.cache = self.root / "cache"
        self.payloads = {"text.gguf": b"text model", "media.gguf": b"media model"}
        fields = []
        for key, (name, data) in zip(("text", "media"), self.payloads.items()):
            (self.source / name).write_bytes(data)
            fields.append(
                f'public static let {key} = File(name: "{name}", bytes: {len(data):_}, '
                f'sha256: "{hashlib.sha256(data).hexdigest()}")'
            )
        self.swift = self.root / "ModelFiles.swift"
        self.swift.write_text(
            'public static let repository = "fixture/model"\n'
            'public static let revision = "' + "a" * 40 + '"\n'
            + "\n".join(fields) + "\npublic static let all = [media, text]\n"
        )
        (self.source / "LICENSE").write_text("Real fixture license\n")
        (self.source / "NOTICE").write_text("Fixture attribution\n")
        self.manifest = self.bundle.parse_manifest(self.swift)

    def run_bundle(self):
        self.bundle.bundle(self.manifest, self.out, self.source, self.cache, offline=True)

    def test_parses_swift_values_and_all_order(self):
        self.assertEqual(self.manifest["repository"], "fixture/model")
        self.assertEqual([f["name"] for f in self.manifest["files"]], ["media.gguf", "text.gguf"])
        self.assertEqual(self.manifest["files"][0]["bytes"], len(b"media model"))

    def test_manifest_fails_closed_on_unknown_all_member(self):
        self.swift.write_text(self.swift.read_text().replace("[media, text]", "[media, missing]"))
        with self.assertRaises(ValueError):
            self.bundle.parse_manifest(self.swift)

    def test_manifest_rejects_unsafe_filename(self):
        self.swift.write_text(self.swift.read_text().replace("text.gguf", "../text.gguf"))
        with self.assertRaises(ValueError):
            self.bundle.parse_manifest(self.swift)

    def test_bundles_verified_regular_files_license_and_marker(self):
        self.run_bundle()
        marker = json.loads((self.out / "bundled.json").read_text())
        self.assertTrue(marker["DIGUP_BUNDLED_MODELS"])
        self.assertEqual(marker["files"], self.manifest["files"])
        self.assertIn("Fixture attribution", (self.out / "MODEL-NOTICES.txt").read_text())
        for name, data in self.payloads.items():
            self.assertEqual((self.out / name).read_bytes(), data)
            self.assertFalse((self.out / name).is_symlink())
        self.bundle.verify_bundle(self.manifest, self.out)

    def test_corrupt_source_fails_without_marker_or_partial_file(self):
        (self.source / "media.gguf").write_bytes(b"wrong model")
        with self.assertRaises((ValueError, FileNotFoundError)):
            self.run_bundle()
        self.assertFalse((self.out / "bundled.json").exists())
        self.assertFalse((self.out / "media.gguf").exists())
        self.assertFalse(list(self.out.glob("*.partial")))

    def test_missing_license_fails_and_invalidates_old_marker(self):
        self.out.mkdir()
        (self.out / "bundled.json").write_text("{}")
        (self.source / "LICENSE").unlink()
        with self.assertRaises((ValueError, FileNotFoundError)):
            self.run_bundle()
        self.assertFalse((self.out / "bundled.json").exists())

    def test_reuses_verified_cache_without_source_or_network(self):
        self.run_bundle()
        for path in self.source.iterdir():
            path.unlink()
        with patch.object(self.bundle, "download", side_effect=AssertionError("network used")):
            self.bundle.bundle(self.manifest, self.root / "second", None, self.cache, offline=True)
        self.bundle.verify_bundle(self.manifest, self.root / "second")

    def test_hf_snapshot_symlinks_are_copied_not_shipped(self):
        hub = self.root / "hub"
        snapshot = hub / "models--fixture--model/snapshots" / self.manifest["revision"]
        snapshot.mkdir(parents=True)
        for path in self.source.iterdir():
            (snapshot / path.name).symlink_to(path)
        with patch.dict(os.environ, {"HF_HUB_CACHE": str(hub)}):
            self.bundle.bundle(self.manifest, self.out, None, self.cache, offline=True)
        self.bundle.verify_bundle(self.manifest, self.out)
        self.assertFalse((self.out / "text.gguf").is_symlink())

    def test_verify_rejects_same_size_corruption_and_missing_license(self):
        self.run_bundle()
        (self.out / "text.gguf").write_bytes(b"x" * len(b"text model"))
        with self.assertRaises(ValueError):
            self.bundle.verify_bundle(self.manifest, self.out)
        (self.out / "text.gguf").write_bytes(b"text model")
        (self.out / "LICENSE").unlink()
        with self.assertRaises(ValueError):
            self.bundle.verify_bundle(self.manifest, self.out)

    def test_verify_compares_to_swift_not_marker_claims(self):
        self.run_bundle()
        marker = json.loads((self.out / "bundled.json").read_text())
        marker["files"] = []
        (self.out / "bundled.json").write_text(json.dumps(marker))
        with self.assertRaises(ValueError):
            self.bundle.verify_bundle(self.manifest, self.out)

    def test_failed_download_does_not_publish_cache_file(self):
        expected = self.manifest["files"][0]

        def wrong_download(url, target):
            target.write_bytes(b"bad")

        with patch.object(self.bundle, "download", side_effect=wrong_download):
            with self.assertRaises(ValueError):
                self.bundle.acquire_model(self.manifest, expected, [], self.cache, offline=False)
        self.assertFalse((self.cache / expected["name"]).exists())
        self.assertFalse(list(self.cache.glob("*.partial")))

    def test_download_stream_is_verified_before_publication(self):
        expected = self.manifest["files"][0]
        with patch.object(self.bundle, "urlopen", return_value=io.BytesIO(self.payloads[expected["name"]])):
            cached = self.bundle.acquire_model(self.manifest, expected, [], self.cache)
        self.assertEqual(cached.read_bytes(), self.payloads[expected["name"]])
        self.assertFalse(list(self.cache.glob("*.partial")))

    def test_repairs_corrupt_cached_model_from_verified_source(self):
        self.run_bundle()
        cache = self.cache / "models--fixture--model" / self.manifest["revision"]
        (cache / "text.gguf").write_bytes(b"x" * len(b"text model"))
        self.run_bundle()
        self.assertEqual((cache / "text.gguf").read_bytes(), b"text model")

    def test_notice_alone_does_not_substitute_for_a_license(self):
        (self.source / "LICENSE").unlink()
        with self.assertRaises(FileNotFoundError):
            self.run_bundle()
        self.assertFalse((self.out / "bundled.json").exists())

    def remote_license_download(self, upstream_license=True, publisher=False):
        """Tiny HF API/card responses; no real network or pinned model values."""
        def download(url, target):
            if "/api/models/" in url:
                upstream = "/upstream/model/" in url
                metadata = {
                    "sha": "b" * 40 if upstream else self.manifest["revision"],
                    "siblings": [{"rfilename": "LICENSE"}] if upstream and upstream_license else [],
                    "cardData": {"base_model": ["upstream/model"], "license": "apache-2.0"},
                }
                data = json.dumps(metadata).encode()
            elif url.endswith(".src_sha"):
                data = ("PRIMARY=" + "b" * 40 + "\n").encode()
            elif url.endswith("LICENSE"):
                data = b"Upstream real license"
            elif url.endswith("README.md"):
                data = (b'<a href="https://ai.google.dev/license">Apache 2.0</a>'
                        if publisher else b"Fixture model card")
            else:
                data = (b"<html><pre>Apache <span>License</span>\nVersion 2.0\n"
                        b"END OF TERMS AND CONDITIONS</pre></html>")
            target.write_bytes(data)
        return download

    def test_fetches_pinned_upstream_license_and_reuses_it_offline(self):
        (self.source / "LICENSE").unlink()
        with patch.object(self.bundle, "download", side_effect=self.remote_license_download()):
            self.bundle.bundle(self.manifest, self.out, self.source, self.cache)
        self.bundle.verify_bundle(self.manifest, self.out)
        self.assertEqual((self.out / "UPSTREAM-LICENSE").read_text(), "Upstream real license")
        with patch.object(self.bundle, "download", side_effect=AssertionError("network used")):
            self.bundle.bundle(self.manifest, self.root / "second", self.source, self.cache, offline=True)

    def test_uses_actual_publisher_license_link_not_spdx_placeholder(self):
        (self.source / "LICENSE").unlink()
        with patch.object(self.bundle, "download",
                          side_effect=self.remote_license_download(upstream_license=False, publisher=True)):
            self.bundle.bundle(self.manifest, self.out, self.source, self.cache)
        self.assertIn("END OF TERMS", (self.out / "LICENSE.txt").read_text())
        self.bundle.verify_bundle(self.manifest, self.out)

    def test_missing_remote_license_fails_closed(self):
        (self.source / "LICENSE").unlink()
        with patch.object(self.bundle, "download",
                          side_effect=self.remote_license_download(upstream_license=False)):
            with self.assertRaises(ValueError):
                self.bundle.bundle(self.manifest, self.out, self.source, self.cache)
        self.assertFalse((self.out / "bundled.json").exists())

    def test_notice_generation_verifies_bundle_and_includes_actual_text(self):
        self.run_bundle()
        spec = importlib.util.spec_from_file_location("notices", SCRIPT.parent / "notices.py")
        notices = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(notices)
        with patch.object(notices.subprocess, "run") as verify:
            text = notices.model_section(self.out)
        self.assertIn("Real fixture license", text)
        self.assertIn("Fixture attribution", text)
        self.assertIn("--verify-only", verify.call_args.args[0])
        self.assertIn(str(self.out), verify.call_args.args[0])

    def test_macos_notices_replace_old_download_claim(self):
        self.run_bundle()
        spec = importlib.util.spec_from_file_location("notices", SCRIPT.parent / "notices.py")
        notices = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(notices)
        out = self.root / "Acknowledgements.txt"
        with patch.object(notices.subprocess, "run"), patch("sys.argv", [
            "notices.py", "macos", str(out), str(self.out)
        ]):
            notices.main()
        text = out.read_text()
        self.assertIn("Sparkle", text)
        self.assertIn("Real fixture license", text)
        self.assertNotIn("DigUp doesn't\ninclude it", text)

    def test_linux_notices_do_not_require_bundled_models(self):
        spec = importlib.util.spec_from_file_location("notices", SCRIPT.parent / "notices.py")
        notices = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(notices)
        out = self.root / "linux-notices.txt"
        with patch.object(notices, "need", return_value="license"), \
                patch.object(notices, "build_ffmpeg_versions", return_value=("1", "2")), \
                patch.object(notices, "stb_license", return_value="stb"), \
                patch.object(notices, "miniz_license", return_value="miniz"), \
                patch.object(notices, "swift_license", return_value="swift"), \
                patch.object(notices, "rust_section", return_value="rust"), \
                patch.object(notices, "model_section", side_effect=AssertionError("Linux bundling")), \
                patch("sys.argv", ["notices.py", "linux", str(out)]):
            notices.main()
        self.assertIn("isn't part of this download", out.read_text())

    def test_windows_notices_require_and_include_bundled_models(self):
        self.run_bundle()
        spec = importlib.util.spec_from_file_location("notices", SCRIPT.parent / "notices.py")
        notices = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(notices)
        out = self.root / "windows-notices.txt"
        with patch.object(notices, "need", side_effect=lambda path: (
                path.read_text() if path.name == "MODEL-NOTICES.txt" else "license")), \
                patch.object(notices, "build_ffmpeg_versions", return_value=("1", "2")), \
                patch.object(notices, "stb_license", return_value="stb"), \
                patch.object(notices, "miniz_license", return_value="miniz"), \
                patch.object(notices, "swift_license", return_value="swift"), \
                patch.object(notices, "rust_section", return_value="rust"), \
                patch.object(notices.subprocess, "run"), \
                patch("sys.argv", ["notices.py", "windows", str(out), str(self.out)]):
            notices.main()
        text = out.read_text()
        self.assertIn("Real fixture license", text)
        self.assertNotIn("isn't part of this download", text)


if __name__ == "__main__":
    unittest.main()
