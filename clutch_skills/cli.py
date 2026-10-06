"""Flag wiring: argparse -> the skills service -> stdout.

  clutch-skills [--json] [--root DIR] <command> [flags]

- human (default): readable text on stdout, `ERROR: <reason>` on stderr
- --json: ONE compact JSON object on stdout for both outcomes, so callers parse
  stdout plus the exit code, never prose. The object is exactly what the service
  answered (its contract), which is also what the offline path builds.
- --facts: the HOST's question, not ours — with `list`, one JSON array of
  `{"name", "description"}` on stdout, the shape the host reads a published fact
  in (its `component.json` says which line asks for it). Read from the filesystem
  in this process, never from a service: the host asks while it is assembling a
  prompt, and starting a daemon to answer it would be absurd.
- `install` is the library's own write verb: it fetches a skill's files from an
  https tree (a loopback http one is allowed too, for a preview on this machine)
  and puts them under `--root`, then answers the same catalog shape `list` does.
  It is strictly local — a service elsewhere serves a different library — so it
  takes neither `--url` nor `--port`.
- Every other call rides the service for the root: a live daemon is reused,
  otherwise one is lazily started and discovery-keyed. `--no-server` answers the
  reads (`list`, `show`) from the filesystem in this one process; `health` and
  `root` are refused there, because the answer IS daemon state.

Exit codes: 0 ok, 1 the library refused (unknown skill, bad or unreadable file,
bad root), 2 usage error or transport trouble. Nothing is retried locally after
a failed call: a service that died mid-request is not a service that said no.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

from . import __version__, client, server, skills
from .install import DEFAULT_FILES, install
from .skills import SkillError

EXIT_OK = 0
EXIT_REFUSED = 1  # the library said no: unknown skill, bad file, bad root
EXIT_TRANSPORT = 2  # usage error, unreachable service, protocol nonsense


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="clutch-skills",
        description="Skill library CLI: list a catalog, read one skill, point a service at another root.",
        allow_abbrev=False,
    )
    parser.add_argument("--json", action="store_true", help="emit one JSON object on stdout instead of human text")
    parser.add_argument(
        "--facts",
        action="store_true",
        help="answer a HOST fact: with `list`, one JSON array of {name, description} on stdout",
    )
    parser.add_argument(
        "--envelope",
        action="store_true",
        help="emit the host envelope {content, code} (one object, the model-facing text inside)",
    )
    parser.add_argument(
        "--root",
        default=str(server.BUNDLED_ROOT),
        metavar="DIR",
        help=f"skills root to serve (default: the bundled {server.BUNDLED_ROOT})",
    )
    parser.add_argument("--url", default="", metavar="URL", help="talk to this service instead of discovering one")
    parser.add_argument("--port", type=int, default=0, metavar="N", help="talk to a service on this localhost port")
    parser.add_argument(
        "--no-server",
        action="store_true",
        help="answer list/show from the filesystem; never start a service",
    )
    parser.add_argument("--version", action="version", version=f"clutch-skills {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>", required=True)

    sub.add_parser("list", help="the catalog: name + description per skill")

    show = sub.add_parser("show", help="one skill file's text (default SKILL.md)")
    show.add_argument("name", help="skill name or its directory name")
    show.add_argument("--file", default="", metavar="REL", help="file inside the skill directory (default SKILL.md)")

    install_cmd = sub.add_parser("install", help="fetch a skill from an https tree into the served root")
    install_cmd.add_argument("name", help="skill name: one directory name, never a path")
    install_cmd.add_argument(
        "source",
        help="https directory URL holding the files (loopback http is allowed for a local preview)",
    )
    install_cmd.add_argument(
        "--file",
        action="append",
        default=[],
        metavar="REL",
        help="file to fetch, relative to the skill (repeatable; default SKILL.md)",
    )

    root = sub.add_parser("root", help="print the served root, or re-point the service at DIR")
    root.add_argument("dir", nargs="?", default="", metavar="DIR")

    sub.add_parser("health", help="is a service answering, on which root, with how many skills")
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError, ValueError):
            pass
    parser = build_parser()
    args = parser.parse_args(argv)  # usage errors exit 2 straight from here
    if args.facts and (args.json or args.envelope):
        # One answer shape at a time: the host parses stdout as its own JSON, so
        # a second shape mixed in would be a silent misread rather than an error.
        parser.error("--facts is the host's answer shape: it cannot be combined with --json or --envelope")
    try:
        payload, text = _run(args)
    except SkillError as err:  # a refusal, library-side
        _emit_error(err.message, args, EXIT_REFUSED)
        return EXIT_REFUSED
    except client.ProtocolError as err:  # a refusal, service-side (reason included)
        _emit_error(err.message, args, EXIT_REFUSED, err.available)
        return EXIT_REFUSED
    except client.ServiceUnavailable as err:
        _emit_error(str(err), args, EXIT_TRANSPORT)
        return EXIT_TRANSPORT
    except KeyboardInterrupt:
        _emit_error("interrupted", args, 130)
        return 130
    except Exception as err:  # noqa: BLE001 - the one catch-all: name it a bug
        traceback.print_exc()
        _emit_error(f"internal error: {err}", args, EXIT_TRANSPORT)
        return EXIT_TRANSPORT
    _emit(payload, text, args)
    return EXIT_OK


def _run(args: argparse.Namespace) -> tuple[dict, str]:
    """(wire payload, human text) for one command."""
    root = str(Path(args.root).expanduser())
    if not (args.url or args.port):
        # A root we must find (or start) a service for is checked here, so a bad
        # root is the library refusal the exit codes promise — not a service that
        # died during startup, which is what a daemon pointed at a missing
        # directory looks like from the outside.
        skills.checked_root(root)
    if args.facts:
        # The host's own question: which skills are here. Answered from the
        # filesystem in this process — the host asks it while assembling a prompt,
        # before any call exists — and only `list` has such an answer.
        if args.command != "list":
            raise SkillError("bad-command", f"--facts answers `list` only, not `{args.command}`")
        return server.catalog_payload(skills.checked_root(root)), ""
    if args.command == "install":
        # Writing into a library is that library's own business, and the library
        # is the root we serve: `install` is local, so a service elsewhere is not
        # a second way to do it but a different library.
        if args.url or args.port:
            raise SkillError("bad-command", "install writes to --root: it takes neither --url nor --port")
        payload = install(
            skills.checked_root(root), args.name, args.source, args.file or DEFAULT_FILES
        )
        return payload, _install_text(payload)
    if args.command == "list":
        payload = server.catalog_payload(skills.checked_root(root)) if args.no_server else _client(args).catalog()
        return payload, _section(payload) or f"(no skills in {payload['root']})"
    if args.command == "show":
        payload = (
            server.skill_payload(skills.checked_root(root), args.name, args.file)
            if args.no_server
            else _client(args).skill(args.name, args.file)
        )
        return payload, payload["content"]
    if args.no_server:  # health/root describe the running service, not a folder
        raise client.ServiceUnavailable(f"--no-server cannot answer `{args.command}`: it needs the running service")
    if args.command == "health":
        payload = _client(args).health()
        return payload, f"ok root={payload['root']} pid={payload['pid']} skills={payload['skills']}"
    if args.command == "root":
        payload = _client(args).health() if not args.dir else _client(args).set_root(_absolute(args.dir))
        return payload, f"root: {payload['root']} ({payload['skills']} skills)"
    raise SkillError("bad-command", f"unknown command: {args.command}")  # unreachable: subparsers required


def _absolute(path: str) -> str:
    """A re-pointed root is resolved here so the daemon publishes one canonical
    key for it, whatever spelling the caller used."""
    return str(Path(path).expanduser().resolve())


def _client(args: argparse.Namespace) -> client.Client:
    return client.Client(client.base_url(str(Path(args.root).expanduser()), url=args.url, port=args.port))


def _section(payload: dict) -> str:
    """Render the catalog for humans straight from the wire shape (the service
    itself stays JSON, and both paths render identically)."""
    skills_here = [
        skills.Skill(
            name=s["name"],
            description=s["description"],
            directory=s["dir"],
            path=Path(payload["root"]) / s["dir"],
        )
        for s in payload["skills"]
    ]
    return skills.catalog_section(skills_here)


def _install_text(payload: dict) -> str:
    """What the install did (source, root, one line per file with its size and
    whether the bytes actually changed), then the catalog it left behind — the
    two things a caller wants in one answer."""
    lines = [
        f"installed: {payload['skill']}",
        f"source: {payload['source']}",
        f"root: {payload['root']}",
        *(f"- {f['file']}: {f['bytes']} bytes ({f['state']})" for f in payload["files"]),
    ]
    section = _section(payload)
    return "\n".join(lines) + (f"\n{section}" if section else "")


def _emit(payload: dict, text: str, args: argparse.Namespace) -> None:
    """One JSON object, the host envelope, the host's fact array, or the human
    text with exactly one trailing newline — so `show` can be piped straight into
    a file and diffed against the SKILL.md.

    `--envelope` is the mode the Clutch host drives this CLI in: exactly one
    {content, code} object per call, `content` being the model-facing text (for
    `show`, the skill file itself). `--json` stays the module's own machine
    contract (the raw wire payload). `--facts` is the third party in the room —
    the HOST's fact shape, which is why it carries only the two keys the host
    knows how to read out of our answer.
    """
    if args.facts:
        print(
            json.dumps(
                [{"name": s["name"], "description": s["description"]} for s in payload["skills"]],
                ensure_ascii=False,
            )
        )
    elif args.envelope:
        print(json.dumps({"content": text, "code": EXIT_OK}, ensure_ascii=False))
    elif args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        sys.stdout.write(text if text.endswith("\n") else text + "\n")


def _emit_error(message: str, args: argparse.Namespace, code: int, available: list[str] | None = None) -> None:
    """Failure output. The envelope carries the same one-line reason the human
    path prints, so the model never reads a JSON error object as prose."""
    if args.envelope:
        print(json.dumps({"content": f"ERROR: {message}", "code": code}, ensure_ascii=False))
        return
    if args.json:
        body: dict = {"error": message}
        if available:
            body["available"] = available
        print(json.dumps(body, ensure_ascii=False))
    else:
        print(f"ERROR: {message}", file=sys.stderr)
        if available:
            print(f"available: {', '.join(available)}", file=sys.stderr)
