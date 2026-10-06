"""Putting a skill INTO the library this service serves — the library's own verb.

A skill is a directory holding a `SKILL.md` plus whatever it references, and this
library scans a root for `*/SKILL.md` — so installing is exactly "fetch the files
and put the directory there". Nobody else gets to write into the library: the
root belongs to the component that serves it (`server.BUNDLED_ROOT`, or the
`--root` this process was handed), and a host-side installer writing into an
installed component's version directory is how two copies of one library start
disagreeing.

The source is a plain https tree (a GitHub raw directory URL, for instance), so
an install needs no git and no credentials. Loopback `http` is allowed as well:
nothing crosses a wire there, and a preview server on the same machine is how an
operator checks a skill before publishing it.

What is written is also recorded: `PROVENANCE.json` in the skill directory names
the source, the moment and each file's sha256, which is what makes an install
auditable after the fact (and is not a skill file itself — the scan only looks
for `SKILL.md`).

Nothing in this file knows about argv; `cli.py` is the only caller.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from typing import Any

from . import server, skills
from .skills import SkillError

TIMEOUT = 60.0  # a skill file is prose; a minute of silence is a dead source
MAX_SOURCE_BYTES = skills.MAX_FILE_BYTES
DEFAULT_FILES: tuple[str, ...] = (skills.DEFAULT_SKILL_FILE,)
PROVENANCE = "PROVENANCE.json"

# Hosts `http://` is allowed for: the request never leaves the machine, so the
# reason for demanding https (nobody else reads what we fetch) does not apply.
LOOPBACK = ("127.0.0.1", "localhost", "::1")


def install(
    root: Path | str,
    name: str,
    source: str,
    files: Iterable[str] = DEFAULT_FILES,
    *,
    opener: Callable[..., Any] = urlopen,
) -> dict:
    """Fetch one skill tree into `root`, and answer the library's own payload.

    The payload is `server.catalog_payload`'s (root + skills) with the install's
    own facts added — so a caller parses the answer the same way it parses
    `list`, and "what does the library offer now" is answered by the same scan
    the next request will do. `opener` is the seam a test replaces; the real one
    is urllib's.
    """
    base = skills.checked_root(root)
    where = _checked_source(source)
    skill = _checked_name(name)
    directory = base / skill
    # The path rule is the READER's (`skills.contained`): a file that could not
    # be read out of a skill directory is not one we write into either.
    targets = [(rel, skills.contained(directory, rel)) for rel in (_checked_file(rel) for rel in files)]
    # Every byte arrives before any of them is written, so a source that dies
    # halfway leaves the library exactly as it was, not a half-installed skill.
    fetched = [(rel, target, _fetch(f"{where}/{rel}", opener)) for rel, target in targets]

    directory.mkdir(parents=True, exist_ok=True)
    written: list[dict] = []
    for rel, target, body in fetched:
        before = target.read_bytes() if target.is_file() else b""
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
        written.append(
            {
                "file": skills.relative(directory, target),
                "bytes": len(body),
                "sha256": hashlib.sha256(body).hexdigest(),
                "state": "unchanged" if before == body else "installed",
            }
        )

    (directory / PROVENANCE).write_text(
        json.dumps(
            {
                "skill": skill,
                "source": where,
                "installed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "files": {w["file"]: {"sha256": w["sha256"], "bytes": w["bytes"]} for w in written},
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    payload = server.catalog_payload(base)
    payload.update({"skill": skill, "source": where, "files": written})
    return payload


def _checked_source(source: str) -> str:
    """The source root to fetch under, or a refusal naming what is wrong with it."""
    text = (source or "").strip()
    parts = urlsplit(text)
    host = (parts.hostname or "").lower()
    if host and (parts.scheme == "https" or (parts.scheme == "http" and host in LOOPBACK)):
        return text.rstrip("/")
    raise SkillError("bad-source", f"a source must be an https directory URL: {text or '(empty)'}")


def _checked_name(name: str) -> str:
    """One directory name — the library's unit — never a path into the root."""
    text = (name or "").strip()
    if not text or text in (".", "..") or "/" in text or "\\" in text:
        raise SkillError("bad-name", f"a skill name is one directory name, not a path: {name or '(empty)'}")
    return text


def _checked_file(rel: str) -> str:
    """One relative file path inside the skill; containment itself is settled by
    `skills.contained`, so this only rejects what has no relative spelling."""
    text = (rel or "").strip().replace("\\", "/")
    if not text or text.startswith("/"):
        raise SkillError("bad-path", f"a file is a relative path inside the skill: {rel or '(empty)'}")
    return text


def _fetch(url: str, opener: Callable[..., Any]) -> bytes:
    """One source file, or a refusal carrying the reason (never a stack trace:
    a 404 and a dead host are both things the caller has to read)."""
    request = Request(url, headers={"User-Agent": "clutch-skills"})
    try:
        with opener(request, timeout=TIMEOUT) as response:
            body = response.read(MAX_SOURCE_BYTES + 1)
    except HTTPError as err:
        raise SkillError("download-failed", f"{url} answered {err.code}") from None
    except URLError as err:
        raise SkillError("download-failed", f"{url} could not be reached: {err.reason}") from None
    except OSError as err:
        raise SkillError("download-failed", f"{url} could not be reached: {err}") from None
    if len(body) > MAX_SOURCE_BYTES:
        raise SkillError("too-large", f"{url} is larger than {MAX_SOURCE_BYTES} bytes")
    return body
