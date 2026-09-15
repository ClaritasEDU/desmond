# Claude Code Instructions - Desmond

## About This Project
Infrastructure/workflow tool. v1 shipped. Python-based utility for managing development context and workflows across multiple projects.

## About Me (Chris Treadaway)
Product builder, not a coder. I bring requirements and vision — you handle implementation.

**Working with me:**
- Bias toward action — just do it, don't argue
- Make terminal commands dummy-proof (always start with `cd ~/desmond`)
- Minimize questions — make judgment calls and tell me what you chose
- I get interrupted frequently — always end sessions with clear handoff

## Tech Stack
- **Language:** Python
- **Category:** Infrastructure

## File Paths
- **Always use:** `~/desmond/`
- **Never use:** `/Users/christreadaway/...`
- **Always start commands with:** `cd ~/desmond`

## PII Rules
❌ NEVER include: real names, email addresses, API keys, tokens, file paths with /Users/christreadaway → use ~/
✅ ALWAYS use placeholders

## Git Branch Strategy
- Claude Code creates new branch per session
- Merge to main when stable
- Delete merged branches immediately

## Session End Routine

At the end of EVERY session — or when I say "end session" — do ALL of the following:

### A. Update SESSION_NOTES.md
Append a detailed entry at the TOP of SESSION_NOTES.md (most recent first) with: What We Built, Technical Details, Current Status (✅/❌/🚧), Branch Info, Decisions Made, Next Steps, Questions/Blockers.

### B. Update PROJECT_STATUS.md
Overwrite PROJECT_STATUS.md with the CURRENT state of the project — progress %, what's working, what's broken, what's in progress, next steps, last session date/summary. This is a snapshot, not a log.

### C. Commit Both Files
```
git add SESSION_NOTES.md PROJECT_STATUS.md
git commit -m "Session end: [brief description of what was done]"
git push
```

### D. Tell the User
- What branch you're on
- Whether it's ready to merge to main (and if not, why)
- Top 3 next steps for the next session

---
Last Updated: September 15, 2026


## Branch Rules
Always work on the main branch. Do not create new branches unless explicitly asked. Commit and push all changes directly to main.

