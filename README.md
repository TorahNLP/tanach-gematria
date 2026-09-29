# Tanach Gematria — developer docs

Documentation branch for [TorahNLP/tanach-gematria](https://github.com/TorahNLP/tanach-gematria).

**This branch is never deployed.** Only a commit on `main` in the served
directory restarts the app (post-commit hook); committing or pushing here
restarts nothing. The split began when HuggingFace, since retired, rebuilt
production on every push, including doc-only ones.

| File | What it is |
|------|------------|
| `HANDOFF.md` | Session handoff — read this first |
| `BUILD.md` | Build and deploy notes |
| `CLAUDE_CODE_TASKS.md` | Task scratchpad |

## Working on the docs

This branch is checked out as a git worktree beside the code:

```bash
# one-time, if the worktree is missing:
git worktree add ../tanakh-docs docs

cd ../tanakh-docs      # edit HANDOFF.md etc. here
git add -A && git commit -m "..."
git push origin docs   # restarts nothing
```

Code changes go through `main` in the main checkout, and those *do* deploy (the app restarts on commit).
