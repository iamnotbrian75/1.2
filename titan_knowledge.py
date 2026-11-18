"""Keyword-based retrieval for Markdown notes in Titan's Obsidian Brain."""
import re
from pathlib import Path


_TOKEN_RE = re.compile(r"[a-z0-9]+")
_HEADING_RE = re.compile(r"^#{1,6}\s+.+$")
_STOP_WORDS = {
    "a", "about", "all", "am", "an", "and", "any", "are", "as", "at", "be",
    "because", "been", "but", "by", "can", "could", "did", "do", "does",
    "explain", "for", "from", "give", "how", "i", "in", "is", "it", "me",
    "my", "of", "on", "or", "please", "should", "tell", "that", "the",
    "their", "this", "to", "use", "was", "what", "when", "where", "which",
    "who", "why", "will", "with", "would", "you", "your",
}


def _normalise_token(token):
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _keywords(text):
    return {
        _normalise_token(token)
        for token in _TOKEN_RE.findall(text.casefold())
        if len(token) > 1 and token not in _STOP_WORDS
    }


def _sections(content):
    sections = []
    heading = ""
    lines = []
    for line in content.splitlines():
        if _HEADING_RE.match(line):
            if heading or lines:
                sections.append((heading, "\n".join(lines).strip()))
            heading = line.strip()
            lines = []
        else:
            lines.append(line)
    if heading or lines:
        sections.append((heading, "\n".join(lines).strip()))
    return sections


def _note_paths(brain_dir):
    root = Path(brain_dir).expanduser()
    if not root.is_dir():
        return []
    notes = []
    for path in root.rglob("*.md"):
        if path.is_symlink():
            continue
        relative = path.relative_to(root)
        if len(relative.parts) == 1:
            if relative.name.casefold() != "projects.md":
                continue
        elif relative.parts[0].casefold() not in {"knowledge", "projects"}:
            continue
        notes.append(path)
    return sorted(notes, key=lambda item: item.relative_to(root).as_posix().casefold())


def search_notes(query, brain_dir, limit=3, max_chars=4200):
    """Return the best-matching note sections, bounded by note count and output size."""
    if not isinstance(query, str):
        raise TypeError("query must be a string")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
        raise ValueError("limit must be a non-negative integer")
    if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars < 0:
        raise ValueError("max_chars must be a non-negative integer")
    if not query.strip() or limit == 0 or max_chars == 0:
        return ""

    terms = _keywords(query)
    if not terms:
        return ""
    query_phrase = " ".join(
        _normalise_token(token)
        for token in _TOKEN_RE.findall(query.casefold())
        if len(token) > 1 and token not in _STOP_WORDS
    )

    root = Path(brain_dir).expanduser()
    candidates = []
    for path in _note_paths(root):
        content = path.read_text(encoding="utf-8")
        filename_terms = _keywords(path.stem)
        best_match = None
        for section_index, (heading, body) in enumerate(_sections(content)):
            if not body:
                continue
            text = "\n".join(part for part in (heading, body) if part)
            note_terms = [
                _normalise_token(token)
                for token in _TOKEN_RE.findall(text.casefold())
            ]
            matches = sum(1 for term in terms if term in note_terms)
            if matches < (2 if len(terms) >= 3 else 1):
                continue
            frequency = sum(note_terms.count(term) for term in terms)
            score = matches * 4 + min(frequency, 8)
            score += 2 * len(terms.intersection(filename_terms))
            if len(terms) > 1 and query_phrase in " ".join(note_terms):
                score += 2
            match = (score, matches, -section_index, path, heading, body or heading)
            if best_match is None or match[:3] > best_match[:3]:
                best_match = match
        if best_match is not None:
            candidates.append(best_match)

    candidates.sort(
        key=lambda item: (
            -item[0],
            -item[1],
            item[3].relative_to(root).as_posix().casefold(),
        )
    )
    results = []
    remaining = max_chars
    for _, _, _, path, heading, text in candidates[:limit]:
        relative = path.relative_to(root).as_posix()
        label = f"[{relative}]" + (f" {heading}" if heading else "")
        block = f"{label}\n{text}"
        separator = "\n\n" if results else ""
        available = remaining - len(separator)
        if available <= 0:
            break
        if len(block) > available:
            block = block[:available].rstrip()
            if len(block) < available:
                block += "…"
        results.append(block)
        remaining -= len(separator) + len(block)
        if remaining <= 0:
            break
    return "\n\n".join(results)
