# AGENTS.md

Instructions for any AI agent working in this repo. The full repository
guidelines live in `CLAUDE.md`; this file leads with the one thing that is
easiest to get wrong: **the experience bar.**

## The Experience Bar (applies to every user-facing change)

A change is not "done" when it compiles or the test passes — it is done when the
end-to-end experience holds. Check every user-facing change against these. Full
text + rationale + the real failure each was learned from: `HEXIS_EXPERIENCE_BAR.md`.

1. **Derive from truth — never hardcode.** If a value has a live source (models,
   defaults, endpoints, versions), read it; don't hardcode a constant that goes stale.
2. **The user keeps control.** No destructive/irreversible action on a timer, by
   default, or without an explicit choice. No auto-exit, no auto-overwrite, no silent delete.
3. **Honor the medium.** Terminal ⇒ Ctrl+C exits, native copy/paste + scrollback,
   keyboard-first, no focus-hunting. Don't fight the platform; if a framework's
   defaults need constant overriding, it's the wrong tool.
4. **No dead-ends.** Every flow completes in place or hands the user the exact next
   step. Never "quit, run this other command, come back." Errors say what/why/next.
5. **Least surprise.** Never silently reuse ambient state the user didn't choose
   (env creds, other tools' logins, stale config). Surface it; don't consume it.
6. **Defaults are the expert's choice**, not the first constant that compiles.
7. **Whole journey, not the diff.** Drive the real path a user runs, start to finish,
   before calling it done.
8. **Fail loud, recover gracefully.** Advisory checks never block; failures show
   cause + fix — never a bare traceback, never a silent `except: pass`.

The deeper reason these slip through one at a time (an abstraction/altitude failure)
is written up in `why_i_suck_and_how_to_fix_it.md`.

## Everything else

See `CLAUDE.md` for project overview, structure, build/test commands, the venv +
"bouncing the database" workflow, coding style, testing conventions, and safety
notes (never revert/delete files without asking; heartbeat gating; consent flow).
