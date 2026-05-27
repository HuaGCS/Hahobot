Update memory files based on the analysis below.
- [FILE] entries: add the described content to the appropriate file
- [FILE-REMOVE] entries: delete the corresponding content from memory files

## File paths (relative to workspace root)
- SOUL.md
- USER.md
- PROFILE.md
- INSIGHTS.md
- memory/MEMORY.md

Do NOT guess paths.

## Editing rules
- Edit directly — file contents provided below, no read_file needed
- If `PROFILE.md` or `INSIGHTS.md` is missing and you need it, create it with `write_file`
- Keep file roles strict: PROFILE = stable user facts/preferences, INSIGHTS = collaboration heuristics, USER = relationship framing, SOUL = persona identity, MEMORY = project/work context
- For touched PROFILE / INSIGHTS bullets, prefer structured metadata comments over raw `(verify)`, for example `- Prefers short review loops. <!-- hahobot-meta: confidence=high last_verified=2026-04-08 -->`
- `confidence` must be one of `low`, `medium`, or `high`
- Only set `last_verified=YYYY-MM-DD` when the current batch explicitly reconfirms the fact or pattern
- Legacy `(verify)` markers may remain on untouched bullets, but normalize them when editing that bullet
- Use exact text as old_text, include surrounding blank lines for unique match
- Batch changes to the same file into one edit_file call
- For deletions: section header + all bullets as old_text, new_text empty
- Resolve contradictions by editing or deleting older bullets; never leave stale and corrected versions side by side
- If a surviving bullet is tentative, keep one canonical bullet with `confidence=low` instead of creating duplicate maybe-variants
- Merge near-duplicate bullets into one canonical line when possible
- `memory/MEMORY.md` accumulates appended fact blocks from consolidation; merge near-duplicate bullets and collapse repeated headers into one coherent structure
- Each `MEMORY.md` fragment is one blank-line-separated block, optionally prefixed by a metadata header on its own line: `<!-- ts:YYYY-MM-DDTHH:MM tag:WORD src:WORD -->` where `tag` is one of preference, project, reference, feedback, user, experience, legacy. Preserve headers when editing; when adding a new fragment, emit a fresh header with the current date/time and `src:dream`
- Use `tag:experience` only for a reusable task pattern distilled from multiple turns (e.g. a successful tool sequence for a class of problem), not for one-off facts. Keep at most a handful — merge near-duplicates
- When a pattern is solid enough to deserve its own callable skill (3+ similar successful task traces, clear inputs/outputs), propose it instead of just recording an `experience` fragment: use `write_file` to create `skills/proposed/<slug>/SKILL.md` with YAML frontmatter (`name`, `description`, optional `metadata: {"hahobot":{"triggers":[...],"tool_tags":[...]}}`) and a numbered-steps body. Do NOT write to `skills/<slug>/` directly — the human approves the proposal via the admin UI. Slug uses lowercase letters, digits, dashes; max 64 chars
- Surgical edits only — never rewrite entire files
- If nothing to update, stop without calling tools

## Quality
- Every line must carry standalone value
- Concise bullets under clear headers
- When reducing (not deleting): keep essential facts, drop verbose details
- If uncertain whether to delete, keep one canonical bullet and lower confidence instead of inventing duplicate hedges
