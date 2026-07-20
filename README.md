# Tanach Gematria — developer docs

Documentation branch for [TorahNLP/tanach-gematria](https://huggingface.co/spaces/TorahNLP/tanach-gematria).

**This branch is never deployed.** The Space builds `main`; pushing here does not
rebuild or restart the running app. That is the whole point of the split — doc
edits used to cost a production outage, because HuggingFace rebuilds on any push
to the tracked branch regardless of what changed.

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
git push space docs    # no rebuild, no downtime
```

Code changes still go through `main` in the main checkout, and those *do* deploy.
