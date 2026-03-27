## What
<!-- Required: What is being changed and why -->

## Why
<!-- Required: Business or technical reason for this change -->

## Testing done
- [ ] Unit tests pass
- [ ] Integration test passes on staging
- [ ] Manual smoke test on staging: http://192.168.122.218:32504

## Rollback plan
```bash
ssh andy@192.168.122.218 "sudo k3s kubectl rollout undo deployment/trengo-search -n default"
```

## Version bump
- [ ] `patch` — bug fix, no new functionality
- [ ] `minor` — new feature, backward compatible
- [ ] `major` — breaking change

## Checklist
- [ ] MR title describes the change clearly
- [ ] Version label added (major / minor / patch)
- [ ] No direct push to main — this MR is the change record
