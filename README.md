# clutch-skills

The skill library as a standalone service. A skill is a directory holding a
`SKILL.md`; a session needs only `name` + `description` to decide what to load,
and the body only when it actually loads one. This module owns those files and
answers those three questions — catalog, one file, a re-pointed root — over
loopback HTTP, stdlib only, importing no host code.

```
clutch-skills/skills/       the bundled root (any root can be served instead)
clutch-skills/clutch_skills/
  skills.py                 the library: scan, frontmatter, containment, read
  install.py                the library's write verb: fetch a skill into the root
  server.py                 the service: HTTP skin over the library
  client.py                 thin client: find or lazily start a daemon
  discovery.py              how a caller finds the daemon for a root
  cli.py                    flags -> service -> stdout
```

## CLI

```
python3 -m clutch_skills [--json] [--root DIR] list
python3 -m clutch_skills [--json] [--root DIR] show NAME [--file REL]
python3 -m clutch_skills [--json] [--root DIR] install NAME SOURCE [--file REL]
python3 -m clutch_skills [--json] [--root DIR] root [DIR]
python3 -m clutch_skills [--json] [--root DIR] health
```

Exit codes: `0` ok, `1` the library refused (unknown skill, bad or unreadable
file, a root that is not a directory), `2` usage error or transport trouble.
`--json` always emits **one** JSON object on stdout for both outcomes, so a
caller parses stdout plus the exit code and never prose. `show` prints the file
verbatim (exactly one trailing newline at most), so it can be piped into a file
and diffed.

`--no-server` answers `list` and `show` from the filesystem in this one process
and starts nothing; `health` and `root` are refused there, because the answer IS
daemon state. `--url`/`--port` talk to a service you already know instead of
discovering one.

## Installing a skill

`install` is the library's own write verb — nobody else writes into a root this
component serves, because a host-side installer writing into an installed
component's version directory is how two copies of one library start disagreeing:

```
python3 -m clutch_skills --root /tmp/library install web-design \
    https://raw.githubusercontent.com/me/skills/main/web-design --file SKILL.md
installed: web-design
source: https://raw.githubusercontent.com/me/skills/main/web-design
root: /tmp/library
- SKILL.md: 8123 bytes (installed)
Available skills (call load_skill to read one when relevant):
- web-design: pages, html, css, javascript, landing page, frontend, website
```

The source is an https directory URL held to the given files (`--file`,
repeatable; default `SKILL.md`), and loopback `http` is allowed too — nothing
crosses a wire there, and a preview server on this machine is how an operator
checks a skill before publishing it. Every file arrives before any is written, so
a source that dies halfway leaves the library exactly as it was; each file is
capped at 1 MB; `PROVENANCE.json` records the source, the moment and the sha256 of
each file. The write side reuses the reader's containment rule
(`skills.contained`), so a path that cannot be read out of a skill is not one that
can be written into it either. `install` answers the same catalog shape `list`
does plus `skill`/`source`/`files`, and it talks to the root directly: it takes
neither `--url` nor `--port`, because a service elsewhere serves a different
library.

## The wire contract

One daemon per root, `127.0.0.1`, no auth (nothing here is worth a token: the
service reads only inside the root it was pointed at, and its one mutation is
re-pointing itself):

```
GET  /health                -> {"ok", "root", "pid", "skills"}
GET  /skills                -> {"root", "skills": [{"name","description","dir"}]}
GET  /skill?name=&file=     -> {"root","name","description","dir","file","content"}
POST /root {"root": DIR}    -> {"ok", "root", "skills"}      re-point, no restart
```

Here the status codes carry the meaning: `200` ok, `400` a caller-fixable
request (missing field, bad file, escape, non-UTF-8, a root that is not a
directory), `404` an unknown skill or path. The `404` for a skill carries
`"available": [names]`, so a caller that guessed a name can answer with the real
ones instead of failing blind. Nothing is cached: editing a `SKILL.md` takes
effect on the next request. A daemon exits after `--idle` seconds without a
request (default 600) and on `SIGTERM`/`SIGINT`, and both paths remove its
discovery file.

## Rules the service keeps

- **name is never a path** — `name` selects a directory the scan already found;
  `file` is re-resolved against that directory, and absolute paths, `..`, and
  symlinks that leave the skill are refused;
- **a skill that is broken is loud** — a directory without `SKILL.md` is skipped
  silently, but a `SKILL.md` that cannot be decoded as UTF-8 raises instead of
  vanishing from the catalog;
- **bounded reads** — 1 MB per file (`MAX_FILE_BYTES`), and a file that is not
  UTF-8 text is a named refusal, not a mojibake answer;
- **stable output** — skills come back ordered by directory name;
- **no shared state with the other modules** — containment is written here on
  purpose; borrowing the workspace daemon's fence would couple two modules that
  must stay strangers.

## Rendezvous

`~/.clutch-skills/s-<sha256(root)[:32]>.json` (`CLUTCH_SKILLS_DISCOVERY_DIR`
overrides the directory; `%LOCALAPPDATA%` on Windows) holds root, port, pid and
start time. Two spellings of the same directory land on one file, a stale entry
whose pid is dead is removed on read, and the CLI reuses a live daemon or starts
one detached and waits for it to publish itself.

## Tests

```
cd clutch-skills && python3 -m pytest
```

`tests/conftest.py` gives every test its own discovery directory and reaps the
daemons it started; `tests/test_server.py` drives a real daemon process over
real HTTP, `tests/test_cli.py` drives `python -m clutch_skills` as a subprocess.
