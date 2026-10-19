# Repository workflow

- Read `README.md` and `docs/dev/README.md` before making changes.
- Before creating or pushing a branch, fetch the remote refs and check existing branch names.
- Development and PR branches must match `^TTA-[0-9]+$`, for example `TTA-12`. Use an unused number for new work.
- Commit and push changes on the task branch, never directly on `main`.
- Merge changes into `main` through a pull request. Do not bypass branch protection.
