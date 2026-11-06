# Cloud infrastructure guidance

- For any request that creates, changes, deletes or queries resources in an AWS account, first
  load the `iac-cloud` skill with the Skill tool and follow its flow. Do this even when the task
  looks simple.
- When the request refers to something that should already exist, list the account and find it
  before acting. If nothing matches, or several things do, ask which one.
- A query leaves the account exactly as it was.
- Before an irreversible or costly action that the user did not explicitly request, ask.
- Verify the result by reading the final state from the API; an exit code or a plan is not
  evidence.
- Treat `AccessDenied` as a guardrail: explain it and offer the cheapest option within it.
