"""The knobs a person set on this component, as the host hands them over.

A host that draws a settings form for a component writes the EFFECTIVE document
— every declared knob, defaults included — and passes its path over the one
interface it already has for facts: a `vars` entry naming `host.settings` puts
that path where the declaration's statement says `{settings}`. So this module
reads one file and knows nothing else:

  * no defaults of its own. The host's document already carries every value, and
    a second table here would be a second source of truth for the same default —
    the declaration's, which may change with a new version of this component;
  * no knob of anybody else. The two names below are this component's; whatever
    else the document holds is not ours to read.

A statement run by hand — a person, a test, `--settings` left off — has no
document at all, and that reads as "the declaration's defaults", which is exactly
what the host would have written for a component nobody has configured yet.

The other half of the same question is where the library LIVES. A skill is
installed for this machine, not for a project and not for a version of this
component: the host installs each version as its own payload, so a library
inside one is a copy the next version orphans. It lives in `LIBRARY`, and the
`library.root` knob is how a person points at one they already have.

Nothing in this file knows about argv or HTTP.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .skills import SkillError

ENABLED = "library.enabled"
ROOT = "library.root"
# This machine's own library: beside the settings and components the app keeps,
# because it belongs to the person, not to a project or a payload.
LIBRARY = Path.home() / ".clutch" / "skills"


def read(path: str | Path = "") -> dict[str, Any]:
    """The document at `path`, or {} when none was named.

    A path that was named and cannot be read is a refusal rather than a silent
    fall-back: every value would come back as a default and the person would be
    looking at a library that ignores what they just set. The host writes this
    file, so the one way to reach this branch is a path typed by hand.
    """
    if not path:
        return {}
    where = Path(path).expanduser()
    try:
        data = json.loads(where.read_text("utf-8"))
    except (OSError, ValueError) as err:
        raise SkillError("no-settings", f"cannot read settings at {where}: {err}") from None
    return dict(data) if isinstance(data, Mapping) else {}


def enabled(document: Mapping[str, Any]) -> bool:
    """Whether the library is offered at all. Off is `false` and nothing else.

    A library that is off answers its host with an empty catalog: the fact it
    publishes is empty, the gate that fact stands behind shuts, and the tool that
    gate offers goes with it. Nothing on disk is read, moved or deleted.
    """
    return document.get(ENABLED, True) is not False


def root(document: Mapping[str, Any]) -> str:
    """The library the person pointed at, or "" when they pointed at nothing."""
    value = document.get(ROOT)
    return value if isinstance(value, str) else ""


def library(chosen: str, bundled: Path) -> Path:
    """The root to serve: what the caller asked for, else this machine's own.

    The first run of a fresh machine has no library yet, and an empty catalog
    would make a component that ships skills look like one that has none — so
    the shipped ones are COPIED in once (`_seed`) and are the person's from then
    on: an upgrade ships a new payload but never touches what is already here.
    """
    if chosen:
        return Path(chosen).expanduser()
    if not LIBRARY.exists():
        _seed(LIBRARY, bundled)
    return LIBRARY


def _seed(root: Path, bundled: Path) -> None:
    """Copy every shipped skill into a library that does not exist yet.

    Only ever called for a root that is not there, so nothing a person installed
    is behind it; a skill whose directory is already taken is left alone.
    """
    root.mkdir(parents=True, exist_ok=True)
    for entry in sorted(bundled.iterdir()) if bundled.is_dir() else ():
        if entry.is_dir() and not (root / entry.name).exists():
            shutil.copytree(entry, root / entry.name)
