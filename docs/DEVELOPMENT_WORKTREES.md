# Development worktrees and ownership

This policy prevents overlapping Codex sessions from implementing the same feature, hides machine-specific paths in a private register, and keeps live Compose services independent of disposable worktrees.

## Required preflight before editing

The integration owner records the following before changing files:

1. Repository and task or issue/PR, including the desired behavior and acceptance gates.
2. Absolute repository root, current branch, `HEAD`, upstream, and `git status --short -uall`.
3. Fresh GitHub `main` SHA, open PRs and review threads, local and remote branch tips, worktree inventory, stashes, and any other active Codex owner for overlapping files.
4. Exact expected file paths, a single primary owner for each overlapping file set, any read-only reviewers, and the source/deployment baseline.
5. Compose containers and Scheduled Tasks that depend on a worktree path. Never infer ownership from a branch name alone.

For a new task, first use `git fetch origin` (without pruning), verify the intended baseline is the fetched `origin/main`, and create a uniquely named `codex/...` branch from that SHA. Do not start from a stale local `main`. Record the owner, branch, expected files, baseline SHA, and deployment dependencies in the private registry before editing.

## Ownership and preservation

- One integration owner edits an overlapping file set at a time. Other agents may review read-only until ownership is explicitly transferred.
- Search current `origin/main`, open PRs and review threads, the registry, and every active worktree for the feature before implementation. Reuse or supersede verified code instead of duplicating it.
- Record classified dirty files and independently preserve staged, unstaged, and untracked work before branch changes, archive moves, or rebases. Never use `git clean` as an archive procedure.
- Do not delete a worktree or branch until its content, active-session ownership, backups, GitHub PRs, Docker/task references, and rollback dependencies have been reviewed. Retain non-ancestor history until a human records a disposition.
- Do not run long-lived production Compose services from a Codex worktree. Keep the canonical checkout as the supported Compose root; reconcile existing metadata only in an approved maintenance window.
- Every PR states reused/superseded work, source paths, test gates, and source/runtime rollback. Every deployment record includes the source SHA, candidate build hash, previous build hash, operator, and post-cutover acceptance.

## Private machine registry

Keep the active register outside the checkout in an operator-controlled private directory. Use [the path-free example](worktree-registry.example.json) and validate it against [the schema](worktree-registry.schema.json). Populate absolute local paths, branch, purpose, status, owner, declared Docker dependency, and review ticket only in the private copy. Do not record secrets, private transcripts, bearer tokens, task arguments, or credentials. The registry describes intended ownership; Git and Docker remain the sources of observed state.

## Read-only audit

Run the audit from the canonical checkout with the private registry path:

```powershell
pwsh -NoProfile -File scripts/audit_worktrees.ps1 `
  -RepoRoot (git rev-parse --show-toplevel) `
  -RegistryPath '<private path to active-worktrees.json>' `
  -ReportPath '<new timestamped report path outside this checkout>'
```

The audit reads Git worktrees/refs, queries `origin/main` directly without updating local refs, reads Compose labels/mounts/aliases, and reads Scheduled Task program and working directory. It does not print environment variables or task arguments and has no cleanup, prune, recreate, or process-kill mode. If a non-ancestor ref has changed file blobs, the report includes raw path/mode/blob identities and a patch hash, then says `MANUAL_REVIEW_REQUIRED`; it does not decide semantic equivalence. Critical registry or secret-like untracked-file drift exits nonzero. `-SkipMachineInventory` exists only for the disposable test fixture.

Run the fixture acceptance checks with:

```powershell
pwsh -NoProfile -File scripts/test_audit_worktrees.ps1
```

The fixture verifies a clean registry, a deliberately stale entry, secret-value redaction, and different source blobs that require human review. It operates in a unique temporary Git repository and never changes the real LITE Git, Docker, or Scheduled Task state.
