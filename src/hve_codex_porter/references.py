"""Markdown dependency discovery, retaining literal examples untouched."""
from __future__ import annotations
import posixpath
import re
from urllib.parse import unquote, urlsplit
from markdown_it import MarkdownIt

MARKDOWN = MarkdownIt("commonmark")
FILE = re.compile(r"#file:([^\s`<>\]\)]+)")
ILLUSTRATIVE_CONTEXT = re.compile(r"\b(?:paths?\s+and\s+code|code\s+and\s+paths)\b.{0,80}\billustrative\b", re.IGNORECASE)


def _inline_code_spans(text: str) -> list[tuple[int, int]]:
    """Return CommonMark-style code-span ranges in source character offsets."""
    spans = []
    i = 0
    while i < len(text):
        if text[i] != "`":
            i += 1
            continue
        opening_end = i + 1
        while opening_end < len(text) and text[opening_end] == "`":
            opening_end += 1
        backslashes = 0
        before = i - 1
        while before >= 0 and text[before] == "\\":
            backslashes += 1
            before -= 1
        if backslashes % 2:
            i = opening_end
            continue

        delimiter_length = opening_end - i
        search = opening_end
        closed = False
        while search < len(text):
            closing = text.find("`", search)
            if closing < 0:
                break
            closing_end = closing + 1
            while closing_end < len(text) and text[closing_end] == "`":
                closing_end += 1
            if closing_end - closing == delimiter_length:
                spans.append((i, closing_end))
                i = closing_end
                closed = True
                break
            search = closing_end
        if not closed:
            i = opening_end
    return spans


def _span_contains(spans: list[tuple[int, int]], offset: int) -> bool:
    return any(start <= offset < end for start, end in spans)


def _illustrative_lines(text: str) -> set[int]:
    """Find a prose lead-in and the immediately following illustrative block."""
    lines = text.splitlines()
    result = set()
    for i, line in enumerate(lines):
        if not ILLUSTRATIVE_CONTEXT.search(line):
            continue
        result.add(i)
        heading = next((lines[j] for j in range(i - 1, -1, -1) if re.match(r"\s{0,3}#{1,6}(?:\s|$)", lines[j])), "")
        section_example = bool(re.search(r"\bmodel segment message\b", heading, re.IGNORECASE))
        following = i + 1
        while following < len(lines) and not lines[following].strip():
            following += 1
        while following < len(lines) and (section_example or lines[following].strip()):
            if re.match(r"\s{0,3}#{1,6}(?:\s|$)", lines[following]):
                break
            result.add(following)
            following += 1
    return result


def _replace_outside_inline_code(text: str, old: str, new: str) -> str:
    if not old:
        return text
    spans = _inline_code_spans(text)
    matches = [
        match
        for match in re.finditer(re.escape(old), text)
        if not _span_contains(spans, match.start())
    ]
    for match in reversed(matches):
        text = text[:match.start()] + new + text[match.end():]
    return text


def _resolve_reference(path: str, source: str, known_paths: set[str]) -> tuple[str | None, str]:
    root_relative = posixpath.normpath(path.lstrip("/"))
    source_relative = posixpath.normpath(posixpath.join(posixpath.dirname(source), path))
    candidates = [root_relative, source_relative] if path.startswith("/") else [source_relative, root_relative]
    for candidate in dict.fromkeys(candidates):
        if candidate in known_paths:
            return candidate, "resolved"
        if not posixpath.splitext(candidate)[1]:
            extensionless_matches = [
                possible
                for possible in (candidate + ".md", candidate + "/README.md", candidate + "/index.md")
                if possible in known_paths
            ]
            if len(extensionless_matches) == 1:
                return extensionless_matches[0], "resolved"
            if len(extensionless_matches) > 1:
                return None, "unresolved"
        directory_matches = [p for p in known_paths if p.startswith(candidate.rstrip("/") + "/")]
        if directory_matches:
            return candidate, "directory"
    return None, "unresolved"


def _mapped_directory(target: str, mapping: dict[str, str]) -> str | None:
    """Map a source directory to the corresponding installed directory."""
    target = target.rstrip("/")
    if target == ".github/skills":
        skill_manifests = [
            destination
            for source, destination in mapping.items()
            if source.startswith(".github/skills/")
            and source.endswith("/SKILL.md")
            and destination.endswith("/SKILL.md")
        ]
        if skill_manifests:
            return posixpath.commonpath([posixpath.dirname(path) for path in skill_manifests])
    direct_manifest = mapping.get(target + "/SKILL.md")
    if direct_manifest and direct_manifest.endswith("/SKILL.md"):
        return posixpath.dirname(direct_manifest)

    bases = []
    prefix = target + "/"
    for source, destination in mapping.items():
        if not source.startswith(prefix):
            continue
        suffix = source[len(prefix):]
        if destination.endswith("/" + suffix):
            bases.append(destination[:-(len(suffix) + 1)])
    if bases:
        return posixpath.commonpath(bases)
    return None


def operative_lines(text: str) -> set[int]:
    excluded = set()
    for token in MARKDOWN.parse(text):
        if token.type in {"fence", "code_block", "html_block"} and token.map:
            excluded.update(range(*token.map))
    return set(range(len(text.splitlines()))) - excluded

def references(text: str, source: str, known_paths: set[str]) -> list[dict]:
    targets = []
    illustrative = _illustrative_lines(text)
    for token in MARKDOWN.parse(text):
        token_lines = range(*(token.map or (0, 0)))
        if token.type == "inline" and not illustrative.intersection(token_lines):
            for child in token.children or []:
                if child.type == "link_open":
                    targets.append((child.attrGet("href") or "", "link"))
                elif child.type == "image":
                    targets.append((child.attrGet("src") or "", "image"))
    active = operative_lines(text)
    inline_code = _inline_code_spans(text)
    offset = 0
    for i, line_with_ending in enumerate(text.splitlines(keepends=True)):
        line = line_with_ending.rstrip("\r\n")
        if i in active and i not in illustrative:
            targets.extend(
                (match.group(1).rstrip(",.;:"), "file")
                for match in FILE.finditer(line)
                if not _span_contains(inline_code, offset + match.start())
                and not re.search(r"\b[Ee]xample\s*:", line)
            )
            for match in re.finditer(r"`([^`\n]+)`", line):
                raw = match.group(1)
                candidate = posixpath.normpath(posixpath.join(posixpath.dirname(source), raw))
                if raw in known_paths or candidate in known_paths:
                    targets.append((raw, "resource"))
        offset += len(line_with_ending)
    result = []
    for raw, kind in targets:
        if raw.startswith("#file:"):
            raw = raw[6:]
        parsed = urlsplit(raw)
        if parsed.scheme or raw.startswith(("#", "//")):
            continue
        path = unquote(parsed.path)
        if not path:
            continue
        selected, status = _resolve_reference(path, source, known_paths)
        if selected is None and (
            re.search(r"\b(?:written|generated|saved|created)\s+to\s+\[[^\]]*\]\(" + re.escape(raw) + r"\)", text, re.IGNORECASE)
            or any(x in path for x in ("*", "<", "${", "{"))
            or any(
                any(part in {".copilot-tracking", ".hve"} for part in posixpath.normpath(candidate).split("/"))
                or posixpath.normpath(candidate).startswith("path/to/")
                for candidate in (path, posixpath.join(posixpath.dirname(source), path))
            )
        ):
            status = "runtime-path"
        result.append({"raw": raw, "kind": kind, "target": selected, "status": status})
    return sorted({(r["raw"], r["kind"]): r for r in result}.values(), key=lambda r: (r["raw"], r["kind"]))

def rewrite_links(text: str, source: str, output: str, mapping: dict[str, str], known_paths: set[str]) -> str:
    """Rewrite only operative reference syntax, preserving fenced examples."""
    refs = references(text, source, known_paths)
    lines = text.splitlines(keepends=True)
    inline_code = _inline_code_spans(text)
    line_offsets = []
    offset = 0
    for line in lines:
        line_offsets.append(offset)
        offset += len(line)
    for i in sorted(operative_lines(text)):
        line = lines[i]
        line_offset = line_offsets[i]
        # Apply #file replacements against the original line so multiple
        # references and protected inline examples do not shift one another.
        replacements = []
        for ref in refs:
            destination = mapping.get(ref["target"])
            if destination is None and ref["status"] == "directory":
                destination = _mapped_directory(ref["target"], mapping)
            if ref["kind"] == "file" and destination is not None:
                relative = posixpath.relpath(destination, posixpath.dirname(output))
                fragment = urlsplit(ref["raw"]).fragment
                if fragment:
                    relative += "#" + fragment
                elif urlsplit(ref["raw"]).path.endswith("/"):
                    relative += "/"
                for match in FILE.finditer(line):
                    candidate = match.group(1).rstrip(",.;:")
                    if candidate != ref["raw"] or _span_contains(inline_code, line_offset + match.start()):
                        continue
                    end = match.start(1) + len(candidate)
                    replacements.append((match.start(), end, "`" + relative + "`"))
        for start, end, replacement in reversed(replacements):
            line = line[:start] + replacement + line[end:]

        for ref in refs:
            if ref["kind"] == "file":
                continue
            destination = mapping.get(ref["target"])
            if destination is None and ref["status"] == "directory":
                destination = _mapped_directory(ref["target"], mapping)
            if destination is None:
                continue
            relative = posixpath.relpath(destination, posixpath.dirname(output))
            fragment = urlsplit(ref["raw"]).fragment
            if fragment:
                relative += "#" + fragment
            elif urlsplit(ref["raw"]).path.endswith("/"):
                relative += "/"
            raw = ref["raw"]
            if ref["kind"] == "resource":
                line = line.replace("`" + raw + "`", "`" + relative + "`")
            else:
                line = _replace_outside_inline_code(line, "](" + raw + ")", "](" + relative + ")")
        lines[i] = line
    return "".join(lines)
