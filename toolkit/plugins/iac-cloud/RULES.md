# Cloud infrastructure guidance

- For any request that creates, changes, deletes or queries resources in an AWS account, first
  load the `iac-cloud` skill with the Skill tool and follow its flow. Do this even when the task
  looks simple.
- When the request refers to something that should already exist, list the account and find it
  before acting. If nothing matches, or several things do, ask which one.
- When the request asks for something new and refers to nothing that should already exist,
  there is nothing to look up: create it with safe, low-cost defaults and report the choices.
  Do not ask about details the user left open.
- A query leaves the account exactly as it was.
- Before an irreversible or costly action that the user did not explicitly request, ask.
- Asking for a resource of a given type is consent to create it: pick the smallest, cheapest
  option that meets the request, act, and report what it costs.
- Verify the result by reading the final state from the API; an exit code or a plan is not
  evidence.
- Treat `AccessDenied` as a guardrail: explain it and offer the cheapest option within it.
