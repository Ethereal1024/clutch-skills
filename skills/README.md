# The bundled skills root

This directory is the default `--root` of the skills service: the module ships
the library below (the four skills in `SHIPPED_SKILLS` of the host's
`scripts/build-server-bundle.sh`), and the host may instead point a daemon
anywhere else with `--root DIR` (or by re-pointing a live one with
`POST /root`).

What makes a directory a skill:

```
skills/
  my-skill/
    SKILL.md          <- required; leading `---` block with `name:` and `description:`
    notes.md          <- any other file the skill wants to load on demand
  not-a-skill/        <- no SKILL.md -> skipped silently
```

`name` falls back to the directory name and `description` to `""` when the
frontmatter omits them, so a bare SKILL.md is a legal (if unhelpful) skill.
This file is not a skill: the scan only looks inside directories.

The shipped library:

```
skills/
  readme-crafter/   write, rewrite and audit a repo's README
  readme-doctor/    audit a README and report a prioritized fix list
  refactor/         restructure code without changing its behavior
  web-design/       build or restyle a web page
```
