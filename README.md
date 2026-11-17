# clutch-skills

The skill library as a standalone service. A skill is a directory holding a
`SKILL.md`; a session needs only `name` + `description` to decide what to load,
and the body only when it actually loads one. This module owns those files and
answers those three questions — catalog, one file, a re-pointed root — over
loopback HTTP, stdlib only, importing no host code.

```
clutch-skills/skills/       the skills this module ships (seeded, not the library)
clutch-skills/clutch_skills/
  skills.py                 the library: scan, frontmatter, containment, read
  install.py                the library's write verb: fetch a skill into the root
  server.py                 the service: HTTP skin over the library
  client.py                 thin client: find or lazily start a daemon
  discovery.py              how a caller finds the daemon for a root
  settings.py               the knobs a host drew for this component
  cli.py                    flags -> service -> stdout
```

## CLI

```
python3 -m clutch_skills [--json] [--root DIR] [--settings PATH] list
python3 -m clutch_skills [--json] [--root DIR] [--settings PATH] show NAME [--file REL]
python3 -m clutch_skills [--json] [--root DIR] [--settings PATH] install NAME SOURCE [--file REL]
python3 -m clutch_skills [--json] [--root DIR] [--settings PATH] root [DIR]
python3 -m clutch_skills [--json] [--root DIR] [--settings PATH] health
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

## Where the library lives

A library is a place on THIS machine, never inside whatever directory a
component was installed into: a payload is replaced by the next version, so a
library kept in one is orphaned by the next upgrade. The root is resolved once,
before anything is looked up, in three steps:

1. `--root DIR` — what this call asked for;
2. `library.root` from the settings document — what the person set in the host's
   form for this component;
3. `~/.clutch/skills` — this machine's own library, next to the app's
   `components/` and `settings.json`.

The third step is seeded once: when that directory does not exist yet, the
skills shipped under `clutch-skills/skills/` are copied into it and are the
person's from then on — an upgrade ships a new payload but never writes over a
library that already exists. A skill already present under a name the seed also
carries is left alone.

`library.enabled` is the other declaration, and `false` is the only thing that
turns it off: `--facts list` then answers an empty catalog, from which
everything else follows with no second rule — the gate that fact stands behind
has nothing in it, so the host withdraws `load_skill` and drops the prompt line
that spends the catalog. Nothing on disk is read, moved or deleted.

Settings arrive over the interface a component already has for host facts:
`component.json` declares `vars: {settings: "host.settings"}`, the host fills
that placeholder with the path of the document it wrote (every declared knob,
defaults included), and every statement that names `{settings}` — the fact line
and the tool line — reads it. So this module owns no defaults of its own: the
declaration's are the only ones, and a knob it does not recognize drops to its
default rather than refusing to run.

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
