## What
<!-- Required: What is being changed and why -->

## Why
<!-- Required: Business or technical reason for this change -->

## Testing done
- [ ] Unit tests pass
- [ ] Integration test passes on staging
- [ ] Manual smoke test on staging: trengo-search-staging NodePort 32505 on a cluster node

## Rollback plan
```bash
# e.g. kubectl rollout undo deployment/<name> -n <namespace>
```

## Version bump
- [ ] `patch` — bug fix, no new functionality
- [ ] `minor` — new feature, backward compatible
- [ ] `major` — breaking change

## Checklist
- [ ] MR title describes the change clearly
- [ ] Version label added (major / minor / patch)
- [ ] No direct push to main — this MR is the change record
