#!/usr/bin/env python3
"""Bundle the models pinned by ModelFiles.swift; never publish unverified model files.

    python3 scripts/bundle-models.py --to <Models>
    python3 scripts/bundle-models.py --verify-only --to <installed/Models>

DIGUP_MODEL_SOURCE is a directory of model files (or a local HF snapshot).
HF_HUB_CACHE / HF_HOME / the default HF cache are also searched. Downloads and
license documents are cached outside the repository in DIGUP_MODEL_CACHE, or
~/.cache/digup/models. --offline requires cached/local models AND licenses.
--manifest accepts a Swift fixture for tests, not a second set of pinned values.
"""

import argparse
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from urllib.parse import quote
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent.parent
LICENSE_FILE = re.compile(r"(?i)^(licen[cs]e|copying|copyright|notice)([-_.].*)?$")


def is_license(name):
    name = name.removeprefix("UPSTREAM-")
    return bool(LICENSE_FILE.match(name)) and not name.lower().startswith(("notice", "copyright"))


def safe_name(name):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", name) or name in (".", ".."):
        raise ValueError(f"unsafe filename: {name}")
    return name


def parse_manifest(path):
    text = Path(path).read_text(encoding="utf-8")
    text = re.sub(r"//[^\n]*|/\*.*?\*/", "", text, flags=re.S)

    def scalar(key, pattern):
        values = re.findall(rf'\bstatic\s+let\s+{key}\s*=\s*"({pattern})"', text)
        if len(values) != 1:
            raise ValueError(f"ModelFiles.swift: expected one literal {key}")
        return values[0]

    repository = scalar("repository", r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
    revision = scalar("revision", r"[0-9a-f]{40}")
    entries = re.findall(
        r'\bstatic\s+let\s+(\w+)\s*=\s*File\(\s*name:\s*"([^"]+)",\s*'
        r'bytes:\s*([0-9_]+),\s*sha256:\s*"([0-9a-f]{64})"\s*\)', text
    )
    files = {}
    for key, name, size, digest in entries:
        if key in files or int(size.replace("_", "")) <= 0:
            raise ValueError("ModelFiles.swift: duplicate or invalid file")
        files[key] = {"name": safe_name(name), "bytes": int(size.replace("_", "")), "sha256": digest}
    order = re.findall(r"\bstatic\s+let\s+all\s*=\s*\[([^\]]+)\]", text)
    if len(order) != 1:
        raise ValueError("ModelFiles.swift: expected literal all array")
    keys = [key.strip() for key in order[0].split(",") if key.strip()]
    if not keys or len(set(keys)) != len(keys) or any(key not in files for key in keys):
        raise ValueError("ModelFiles.swift: unknown or duplicate all member")
    result = [files[key] for key in keys]
    if len({f["name"] for f in result}) != len(result):
        raise ValueError("ModelFiles.swift: duplicate filenames")
    return {"repository": repository, "revision": revision, "files": result}


def describe(path, name=None):
    digest = hashlib.sha256()
    size = 0
    with Path(path).open("rb") as handle:
        while chunk := handle.read(8 << 20):
            digest.update(chunk)
            size += len(chunk)
    return {"name": name or Path(path).name, "bytes": size, "sha256": digest.hexdigest()}


def valid(path, expected):
    try:
        return Path(path).stat().st_size == expected["bytes"] and describe(path, expected["name"]) == expected
    except OSError:
        return False


def atomic_file(target, writer, expected=None):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=target.name + ".", suffix=".partial", dir=target.parent)
    os.close(fd)
    temporary = Path(temporary)
    try:
        writer(temporary)
        if expected is not None and not valid(temporary, expected):
            raise ValueError(f"size/SHA256 mismatch: {target.name}")
        # mkstemp uses 0600; package resources must also be readable after installation.
        temporary.chmod(0o644)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def write_json(target, value):
    atomic_file(target, lambda path: path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8"))


def download(url, target):
    # Avoid forwarding HF credentials to the model card's external license URLs.
    headers = {"User-Agent": "DigUp-model-bundler"}
    token = os.environ.get("HF_TOKEN")
    if token and url.startswith("https://huggingface.co/"):
        headers["Authorization"] = "Bearer " + token
    with urlopen(Request(url, headers=headers), timeout=120) as response, Path(target).open("wb") as handle:
        shutil.copyfileobj(response, handle, length=8 << 20)


def hf_url(repository, revision, name):
    return f"https://huggingface.co/{repository}/resolve/{revision}/{quote(name)}"


def hf_snapshot(manifest):
    hub = Path(os.environ.get("HF_HUB_CACHE") or
               (str(Path(os.environ["HF_HOME"]) / "hub") if os.environ.get("HF_HOME") else
                str(Path.home() / ".cache/huggingface/hub")))
    return hub / ("models--" + manifest["repository"].replace("/", "--")) / "snapshots" / manifest["revision"]


def acquire_model(manifest, expected, sources, cache, offline=False):
    target = Path(cache) / expected["name"]
    if valid(target, expected):
        return target
    for source in sources:
        candidate = Path(source) / expected["name"]
        if valid(candidate, expected):
            atomic_file(target, lambda path: shutil.copyfile(candidate, path), expected)
            return target
    if offline:
        raise FileNotFoundError(f"no verified local model: {expected['name']}")
    print(f"Downloading {expected['name']}", flush=True)
    atomic_file(target, lambda path: download(
        hf_url(manifest["repository"], manifest["revision"], expected["name"]), path), expected)
    return target


class LicenseHTML(HTMLParser):
    """Read the publisher's license code block and model-card license links."""

    def __init__(self):
        super().__init__()
        self.in_pre = False
        self.blocks = []
        self.href = None
        self.label = []
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == "pre":
            self.in_pre = True
            self.blocks.append([])
        if tag == "a":
            self.href = dict(attrs).get("href")
            self.label = []

    def handle_endtag(self, tag):
        if tag == "pre":
            self.in_pre = False
        if tag == "a" and self.href:
            self.links.append((self.href, "".join(self.label).strip()))
            self.href = None

    def handle_data(self, data):
        if self.in_pre:
            self.blocks[-1].append(data)
        if self.href:
            self.label.append(data)


def acquire_licenses(manifest, sources, cache, offline=False):
    cache = Path(cache) / "licenses"
    record = cache / "license-info.json"
    if record.is_file():
        info = json.loads(record.read_text(encoding="utf-8"))
        if (info.get("repository") == manifest["repository"] and info.get("revision") == manifest["revision"]
                and info.get("documents") and any(is_license(d["name"]) for d in info["documents"])
                and all(valid(cache / safe_name(d["name"]), {k: d[k] for k in ("name", "bytes", "sha256")})
                        for d in info["documents"])):
            return cache, info["documents"]

    documents = []

    def add(name, data, source):
        safe_name(name)
        text = data.decode("utf-8").strip()
        if not text or text.lower().startswith(("<!doctype", "<html")):
            raise ValueError(f"missing or non-text license document: {source}")
        atomic_file(cache / name, lambda path: path.write_bytes(data))
        documents.append({**describe(cache / name), "source": source})

    for source in sources:
        source = Path(source)
        local = sorted(path for path in source.glob("*") if path.is_file() and LICENSE_FILE.match(path.name))
        if any(is_license(path.name) for path in local):
            for path in local:
                add(path.name, path.read_bytes(), hf_url(manifest["repository"], manifest["revision"], path.name))
            if (source / "README.md").is_file():
                add("MODEL-CARD.md", (source / "README.md").read_bytes(),
                    hf_url(manifest["repository"], manifest["revision"], "README.md"))
            write_json(record, {**manifest, "documents": documents})
            return cache, documents
    if offline:
        raise FileNotFoundError("model source license unavailable locally; run once online to cache it")

    def fetch(url):
        fd, path = tempfile.mkstemp()
        os.close(fd)
        try:
            download(url, Path(path))
            return Path(path).read_bytes()
        finally:
            Path(path).unlink()

    def repository_documents(repository, revision, prefix=""):
        metadata = json.loads(fetch(f"https://huggingface.co/api/models/{repository}/revision/{revision}"))
        if metadata.get("sha") != revision:
            raise ValueError("license metadata did not resolve to the pinned revision")
        names = [item["rfilename"] for item in metadata["siblings"]]
        card_url = hf_url(repository, revision, "README.md")
        card = fetch(card_url)
        add(prefix + "MODEL-CARD.md", card, card_url)
        found = False
        for name in names:
            if "/" not in name and LICENSE_FILE.match(name):
                url = hf_url(repository, revision, name)
                add(prefix + name, fetch(url), url)
                found |= is_license(name)
        return metadata, card.decode("utf-8"), found

    metadata, card, found = repository_documents(manifest["repository"], manifest["revision"])
    if not found:
        bases = metadata.get("cardData", {}).get("base_model", [])
        if isinstance(bases, str):
            bases = [bases]
        src_url = hf_url(manifest["repository"], manifest["revision"], ".src_sha")
        src = fetch(src_url)
        primary = re.fullmatch(r"PRIMARY=([0-9a-f]{40})\s*", src.decode("utf-8"))
        if len(bases) != 1 or not primary or not re.fullmatch(r"[\w.-]+/[\w.-]+", bases[0]):
            raise ValueError("no source license or unambiguous pinned upstream model")
        add("SOURCE-REVISION.txt", src, src_url)
        upstream, card, found = repository_documents(bases[0], primary[1], "UPSTREAM-")
        if not found:
            # This upstream ships no LICENSE either, but its card links Google's
            # actual license page. Do not substitute a guessed SPDX license text.
            parser = LicenseHTML()
            parser.feed(card)
            links = [url for url, label in parser.links if label == "Apache 2.0"
                     and url.startswith("https://ai.google.dev/")]
            if upstream.get("cardData", {}).get("license") != "apache-2.0" or len(set(links)) != 1:
                raise ValueError("upstream model has no available source license")
            parser = LicenseHTML()
            parser.feed(fetch(links[0]).decode("utf-8"))
            licenses = ["".join(block).strip() for block in parser.blocks]
            licenses = [text for text in licenses if "Apache License" in text
                        and "Version 2.0" in text and "END OF TERMS AND CONDITIONS" in text]
            if len(licenses) != 1:
                raise ValueError("publisher page has no complete license text")
            add("LICENSE.txt", (licenses[0] + "\n").encode("utf-8"), links[0])
    write_json(record, {**manifest, "documents": documents})
    return cache, documents


def verify_bundle(manifest, folder):
    folder = Path(folder)
    try:
        marker = json.loads((folder / "bundled.json").read_text(encoding="utf-8"))
        if (marker.get("schema") != 1 or marker.get("DIGUP_BUNDLED_MODELS") is not True
                or any(marker.get(key) != manifest[key] for key in ("repository", "revision", "files"))):
            raise ValueError("bundled.json does not match ModelFiles.swift")
        documents = marker["licenses"]
        if not documents or not any(is_license(d["name"]) for d in documents):
            raise ValueError("bundle has no model license")
        for entry in manifest["files"] + documents + [marker["notice"]]:
            path = folder / safe_name(entry["name"])
            expected = {key: entry[key] for key in ("name", "bytes", "sha256")}
            if path.is_symlink() or not valid(path, expected):
                raise ValueError(f"missing or invalid bundled file: {entry['name']}")
    except (OSError, KeyError, TypeError) as error:
        raise ValueError(f"invalid model bundle in {folder}: {error}") from error
    return marker


def bundle(manifest, folder, source=None, cache=None, offline=False):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    cache = Path(cache or os.environ.get("DIGUP_MODEL_CACHE") or Path.home() / ".cache/digup/models")
    cache = cache / ("models--" + manifest["repository"].replace("/", "--")) / manifest["revision"]
    # A failed rebuild must not leave a success marker from an earlier build.
    (folder / "bundled.json").unlink(missing_ok=True)
    sources = ([Path(source)] if source else []) + [hf_snapshot(manifest)]
    license_dir, documents = acquire_licenses(manifest, sources, cache, offline)
    for entry in manifest["files"]:
        cached = acquire_model(manifest, entry, sources, cache, offline)
        atomic_file(folder / entry["name"], lambda path: shutil.copyfile(cached, path), entry)
    for entry in documents:
        expected = {key: entry[key] for key in ("name", "bytes", "sha256")}
        atomic_file(folder / entry["name"], lambda path: shutil.copyfile(license_dir / entry["name"], path), expected)
    parts = [f"Models bundled for offline use: {manifest['repository']} @ {manifest['revision']}",
             "GGUF conversion; the original model cards, attribution and license follow."]
    for entry in documents:
        parts += ["", "=" * 80, f"{entry['name']}\nSource: {entry['source']}", "=" * 80,
                  (folder / entry["name"]).read_text(encoding="utf-8")]
    notice = folder / "MODEL-NOTICES.txt"
    atomic_file(notice, lambda path: path.write_text("\n".join(parts) + "\n", encoding="utf-8"))
    write_json(folder / "bundled.json", {"schema": 1, "DIGUP_BUNDLED_MODELS": True, **manifest,
                                       "licenses": documents, "notice": describe(notice)})
    try:
        verify_bundle(manifest, folder)
    except ValueError:
        (folder / "bundled.json").unlink(missing_ok=True)
        raise
    print(f"Verified offline model bundle: {folder}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--to", required=True, type=Path)
    parser.add_argument("--manifest", type=Path, default=ROOT / "Sources/DigUpKit/ModelFiles.swift")
    parser.add_argument("--source", type=Path, default=os.environ.get("DIGUP_MODEL_SOURCE"))
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    try:
        manifest = parse_manifest(args.manifest)
        if args.verify_only:
            verify_bundle(manifest, args.to)
            print(f"Verified offline model bundle: {args.to}")
        else:
            bundle(manifest, args.to, args.source, args.cache, args.offline)
    except (OSError, ValueError) as error:
        parser.exit(1, f"bundle-models: {error}\n")


if __name__ == "__main__":
    main()
