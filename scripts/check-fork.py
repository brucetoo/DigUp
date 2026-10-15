#!/usr/bin/env python3
"""Check live translation resources and model pins; no downloads or snapshots."""

import argparse
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parent.parent
MODEL = "Sources/DigUpKit/ModelFiles.swift"
TOKEN = re.compile(r"\{\d+\}")
DYNAMIC = "\x00"


def catalog(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"{path}: duplicate key {key!r}")
            result[key] = value
        return result

    result = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs)
    if not isinstance(result, dict) or any(
        not isinstance(k, str) or not isinstance(v, str) or not v.strip()
        for k, v in result.items()
    ):
        raise ValueError(f"{path}: expected nonempty string translations")
    return result


def lex(text, swift=False, stop=None):
    """Small balanced lexer, preserving source offsets and interpolation bodies."""
    tokens = []
    i = 0
    balance = 1
    while i < len(text):
        if text[i].isspace():
            i += 1
            continue
        if text.startswith("//", i):
            end = text.find("\n", i)
            i = len(text) if end < 0 else end
            continue
        if text.startswith("/*", i):
            depth = 1
            i += 2
            while depth and i < len(text):
                if text.startswith("/*", i):
                    depth += 1
                    i += 2
                elif text.startswith("*/", i):
                    depth -= 1
                    i += 2
                else:
                    i += 1
            if depth:
                raise ValueError("unterminated block comment")
            continue
        start = i
        if not swift and text[i] == "/" and (
            not tokens or tokens[-1][1] in ("=", "(", ",", ":", "return", "=>", "!", "&&", "||")
        ):
            i += 1
            in_class = False
            while i < len(text):
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == "[":
                    in_class = True
                elif text[i] == "]":
                    in_class = False
                elif text[i] == "/" and not in_class:
                    i += 1
                    while i < len(text) and text[i].isalpha():
                        i += 1
                    break
                i += 1
            tokens.append(("regex", "", start, i))
            continue
        raw = re.match(r'(#+)?"', text[i:]) if swift else None
        if raw or (not swift and text[i] in "\"'`"):
            hashes = raw[1] or "" if raw else ""
            quote = '"' if raw else text[i]
            i += len(hashes)
            delimiter = '"""' if swift and text.startswith('"""', i) else quote
            i += len(delimiter)
            parts = [""]
            escape = "\\" + hashes
            closing = delimiter + hashes
            while i < len(text) and not text.startswith(closing, i):
                interpolation = escape + "(" if swift else "${"
                if (swift or quote == "`") and text.startswith(interpolation, i):
                    i += len(interpolation)
                    body_start = i
                    # Lex recursively to skip nested strings/comments while balancing.
                    end_char = ")" if swift else "}"
                    nested = lex(text[i:], swift, end_char)
                    depth = 1
                    begin_char = "(" if swift else "{"
                    for kind, value, offset, end in nested:
                        if kind == "symbol" and value == begin_char:
                            depth += 1
                        if kind == "symbol" and value == end_char:
                            depth -= 1
                            if depth == 0:
                                parts.append(text[body_start:body_start + offset])
                                parts.append("")
                                i = body_start + end
                                break
                    else:
                        raise ValueError("unterminated interpolation")
                elif text.startswith(escape, i):
                    i += len(escape)
                    if i >= len(text):
                        raise ValueError("unterminated escape")
                    char = text[i]
                    unicode_escape = re.match(r"u\{([0-9a-fA-F]+)\}" if swift else
                                              r"u([0-9a-fA-F]{4})", text[i:])
                    if unicode_escape:
                        parts[-1] += chr(int(unicode_escape[1], 16))
                        i += len(unicode_escape[0])
                    else:
                        parts[-1] += {"n": "\n", "r": "\r", "t": "\t", "0": "\0"}.get(char, char)
                        i += 1
                else:
                    parts[-1] += text[i]
                    i += 1
            if i >= len(text):
                raise ValueError("unterminated string")
            i += len(closing)
            tokens.append(("string", parts, start, i))
            continue
        word = re.match(r"[A-Za-z_$][\w$]*", text[i:])
        if word:
            i += len(word[0])
            tokens.append(("word", word[0], start, i))
        else:
            symbol = next((s for s in ("||", "&&", "??", "?.", "==", "!=", "=>")
                           if text.startswith(s, i)), text[i])
            i += len(symbol)
            tokens.append(("symbol", symbol, start, i))
            if stop:
                opening = "(" if stop == ")" else "{"
                balance += (symbol == opening) - (symbol == stop)
                if balance == 0:
                    return tokens
    return tokens


def top_level(tokens):
    depth = 0
    for i, (kind, value, _, _) in enumerate(tokens):
        if kind == "symbol":
            if value in ("(", "[", "{"):
                depth += 1
            elif value in (")", "]", "}"):
                depth -= 1
            elif depth == 0:
                yield i, value


def variants(tokens):
    """Resolve JS static branches; unknown runtime values remain wildcard slots."""
    if not tokens:
        return {""}
    if tokens[0][1] == "(" and tokens[-1][1] == ")":
        # Strip parentheses only when they enclose the entire expression.
        depth = 0
        for i, token in enumerate(tokens):
            if token[0] == "symbol":
                depth += (token[1] == "(") - (token[1] == ")")
            if depth == 0:
                break
        if i == len(tokens) - 1:
            return variants(tokens[1:-1])
    operators = list(top_level(tokens))
    question = next((i for i, op in operators if op == "?"), None)
    if question is not None:
        depth = 0
        for i, op in operators:
            if i < question:
                continue
            if op == "?":
                depth += 1
            elif op == ":":
                depth -= 1
                if depth == 0:
                    return variants(tokens[question + 1:i]) | variants(tokens[i + 1:])
    for operator in ("||", "??", "+"):
        split = next((i for i, op in operators if op == operator), None)
        if split is not None:
            left, right = variants(tokens[:split]), variants(tokens[split + 1:])
            if operator != "+":
                return left | right
            if len(left) * len(right) > 128:
                raise ValueError("too many translation expression variants")
            return {a + b for a in left for b in right}
    if len(tokens) == 1 and tokens[0][0] == "string":
        parts = tokens[0][1]
        result = {parts[0]}
        for i in range(1, len(parts), 2):
            values = variants(lex(parts[i]))
            # An optional runtime value still uses a template slot when empty.
            if DYNAMIC in values:
                values.discard("")
            if len(result) * len(values) > 128:
                raise ValueError("too many interpolation variants")
            result = {a + b + parts[i + 1] for a in result for b in values}
        return result
    return {DYNAMIC}


def messages(text, swift=False):
    tokens = lex(text, swift)
    for i, token in enumerate(tokens):
        if swift:
            call = [(t[0], t[1]) for t in tokens[i:i + 4]]
            if call != [("word", "L10n"), ("symbol", "."), ("word", "tr"), ("symbol", "(")]:
                continue
            start = i + 4
        else:
            if token[:2] != ("word", "t") or i + 1 >= len(tokens) or tokens[i + 1][1] != "(":
                continue
            if i and tokens[i - 1][1] in (".", "function"):
                continue
            start = i + 2
        depth = 1
        for end in range(start, len(tokens)):
            kind, value, _, _ = tokens[end]
            if kind == "symbol":
                depth += (value == "(") - (value == ")")
            if depth == 0:
                break
        else:
            raise ValueError("unbalanced translation call")
        argument = tokens[start:end]
        line = text.count("\n", 0, token[2]) + 1
        if swift:
            if len(argument) == 1 and argument[0][0] == "string":
                parts = argument[0][1]
                key = parts[0]
                for n in range(1, len(parts), 2):
                    key += "{" + str((n - 1) // 2) + "}" + parts[n + 1]
                yield line, key
                # Nested tr calls in interpolation still need their own keys.
                for n in range(1, len(parts), 2):
                    for _, nested in messages(parts[n], True):
                        yield line, nested
            else:
                yield line, DYNAMIC
        else:
            for key in sorted(variants(argument)):
                yield line, key


def translation_errors(root):
    errors = []
    dynamic = 0
    for folder, resource, suffix, swift in (
        ("Sources/DigUpApp", "Sources/DigUpApp/Localization/zh-Hans.json", "*.swift", True),
        ("shell/ui", "shell/ui/zh-Hans.json", "*.js", False),
    ):
        values = catalog(root / resource)
        if not (root / folder).is_dir():
            raise ValueError(f"missing source directory: {folder}")
        for key, value in values.items():
            source_tokens, translated_tokens = set(TOKEN.findall(key)), set(TOKEN.findall(value))
            # Chinese may omit a plural/type slot but must never invent one.
            if not translated_tokens <= source_tokens:
                errors.append(f"{resource}: invalid interpolation tokens for {key!r}")
        for path in sorted((root / folder).rglob(suffix)):
            try:
                extracted = list(messages(path.read_text(encoding="utf-8"), swift))
            except ValueError as error:
                raise ValueError(f"{path.relative_to(root)}: {error}") from error
            for line, key in extracted:
                if not key or key == DYNAMIC:
                    dynamic += bool(key)
                    continue
                if DYNAMIC in key:
                    expression = "^" + ".*?".join(re.escape(s) for s in key.split(DYNAMIC)) + "$"
                    present = any(re.fullmatch(expression, k, re.S) for k in values)
                else:
                    present = key in values
                if not present:
                    errors.append(f"{path.relative_to(root)}:{line}: missing translation {key.replace(DYNAMIC, '{dynamic}')!r}")
    return errors, dynamic


def model_manifest(path):
    # Use the bundler's parser, never duplicate generated pin snapshots.
    spec = importlib.util.spec_from_file_location("fork_model_bundler", ROOT / "scripts/bundle-models.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.parse_manifest(path)


def check(root, base=None):
    errors, dynamic = translation_errors(root)
    current = model_manifest(root / MODEL)
    if base:
        commit = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--verify", base + "^{commit}"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        previous = subprocess.run(
            ["git", "-C", str(root), "show", f"{commit}:{MODEL}"],
            check=True, capture_output=True, text=True,
        ).stdout
        with tempfile.TemporaryDirectory(prefix="digup-pins-") as directory:
            path = Path(directory) / "ModelFiles.swift"
            path.write_text(previous, encoding="utf-8")
            old = model_manifest(path)
        if old != current:
            errors.append("MODEL PIN CHANGE: review models, licenses, caches and index compatibility.\n"
                          + json.dumps({"base": old, "current": current}, indent=2))
    return errors, dynamic


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--base", help="Git commit/ref to compare model pins against; omitted = validate only")
    args = parser.parse_args()
    try:
        errors, dynamic = check(args.root.resolve(), args.base)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"fork-check: {error}\n")
    for error in errors:
        print(error)
    print(f"fork-check: {len(errors)} failure(s); {dynamic} runtime-only call(s) require UI review")
    return bool(errors)


if __name__ == "__main__":
    raise SystemExit(main())
