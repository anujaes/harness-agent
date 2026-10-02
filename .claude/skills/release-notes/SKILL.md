---
name: release-notes
description: Maintain RELEASE_NOTES.md for the Windows edition of harness-agent. Use when porting upstream changes (PrajsRamteke/harness-agent) into windows-support, when finishing a Windows fix or feature, before a commit/push the user has approved, or when the user asks for release notes, a changelog, "what changed" or "what's new".
---

# Release notes for the Windows edition

`RELEASE_NOTES.md` (repo root) is the user-facing record of what each change to
`anujaes/harness-agent@windows-support` brings to Windows users: which upstream
features arrived, how they were made to work on Windows, and what is still
missing. It is written for people who *use* Jarvis on Windows, not for
reviewers of the diff.

## File layout

```markdown
# Release notes — Jarvis for Windows

## Unreleased
<!-- entries land here until the user confirms a commit/push -->

## 2026-10-02 — upstream port `6b5a8c0..16961d6` (Jarvis 0.2.5)
### From upstream
- One line per upstream feature, in plain words. (`abc1234`)
### Windows support
- What was done so it works on Windows (or a Windows-only fix). Name the test that covers it.
### Known gaps
- Anything that works on macOS but not yet on Windows, and the workaround.
### Removed
- Things upstream removed that users may notice (e.g. a provider).
```

- Newest section first, directly under `## Unreleased`.
- A section heading is `## <YYYY-MM-DD> — <what>`; for a port, `<what>` is
  `upstream port \`<old>..<new>\` (Jarvis <VERSION>)`, where `<old>` is the
  last upstream commit ported before, `<new>` the last one ported now, and
  `<VERSION>` is `jarvis/constants/models.py:VERSION`. This range is how the
  next port finds where to start — upstream is never merged, so git history
  can't tell.
- Use only the subsections that have entries, in the order above. A
  Windows-only change outside a sync goes under `### Windows support` of the
  `Unreleased` section.
- One bullet = one user-visible change, one or two lines, plain language. Name
  commands, settings and env vars in backticks. No internal function names
  unless that is the only way to describe it.

## Workflow

1. **Collect.** For an upstream port:
   `git fetch upstream; git log --oneline --no-merges <old>..upstream/main`,
   where `<old>` is the end of the range in the newest port section. For fork work: `git log --oneline --no-merges <last-released-commit>..HEAD`
   plus the uncommitted diff (`git status`, `git diff --stat`).
2. **Classify.** Read each upstream commit's diff (not only its subject) and
   decide which subsection(s) it belongs to. For every feature check whether
   it needed Windows work — shell/process handling, paths, encodings, private
   files, key hints, browser/Chrome discovery, macOS-only tools — and record
   that work under *Windows support* together with the test that proves it.
3. **Gaps are mandatory.** If anything is macOS-only or degraded on Windows,
   list it under *Known gaps*. Never claim parity that was not tested.
4. **Write** the entries under `## Unreleased` while the work is uncommitted.
   When the user confirms the commit, move them into a dated section (the
   heading format above) in the same commit as the code.
5. **Check** before handing back: every bullet is true for the current tree,
   commit hashes exist (`git cat-file -t <hash>`), and the newest section is
   first.

## Rules

- Never commit or push the notes (or anything else) until the user explicitly
  confirms.
- Do not edit published (dated) sections except to fix a factual error; add a
  new entry instead.
- Keep the notes in English, UTF-8, LF line endings.
