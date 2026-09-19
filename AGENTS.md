# Agent Instructions

## Git repository rules

Agents MUST NOT push, force push, merge, create pull requests, create releases, modify remote state, delete remote refs, or create remote tags. The user reviews and manually pushes approved work. Agents must not create commits unless the user expressly authorizes them.

## Mandatory review gate

After every major task, report:

1. goal completed;
2. files created;
3. files modified;
4. files deleted;
5. architecture changes;
6. security impact;
7. dependency changes;
8. tests run;
9. test results;
10. lint/static-check results;
11. `git diff --stat`;
12. `git status`;
13. known limitations and next risks.

Then stop for user review.

## Security rules

Never disable antivirus; whitelist suspicious files; restore or execute quarantined `fetch-params.sh`; commit secrets, wallet databases, private keys, or real identity credentials; or weaken proof constraints merely to make tests pass.

## Claim discipline

Never claim experimental ZSA functionality is production mainnet functionality, Zcash consensus enforces ZWA compliance, arbitrary external ZSA transfers are blocked, full recursive ownership lineage exists, private fields are hidden unless verified, or mandatory protocol fees exist at consensus level.

## Pre-existing work

Always distinguish **PRE-EXISTING ZK-ORIGIN** from **NEW ZWA PROTOCOL WORK**. Preserve third-party and team-source attribution and license notices.
