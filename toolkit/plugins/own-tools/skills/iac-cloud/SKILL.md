---
name: iac-cloud
description: Create, change, delete or query cloud infrastructure on AWS (Terraform, AWS CLI, SDK). Use for any request that reads or changes resources in an AWS account.
---

# Cloud infrastructure work

Run the flow below for every request that touches a cloud account. You are done when the
account is in the state the user asked for, you have read that state back from the API, and the
user knows what changed.

## Calibrate to the person

Read the help level from the request itself.

- **Precise** (names resources, attributes, Terraform, ARNs): an engineer. Be terse. Act, then
  report the change and anything surprising.
- **Vague or everyday words** ("my website", "the table", "make it cheaper"): a layperson. Use
  plain words, say in one sentence what each costly or irreversible step does before doing it,
  and finish with what now exists, what it costs, and how to undo it.

Step 4 applies at both levels.

## Flow

1. **Understand.** Restate the request in one sentence. When it refers to something that should
   already exist ("the bucket", "table X", "the dev queue"), list the account's resources first
   and find it. If nothing matches, or several do, stop and ask which one; creating a
   look-alike on a guess hides the user's mistake.
2. **Pick the form by cost.** Accuracy is the same either way, so choose the fewest steps:
   - query: read-only calls (`describe`, `list`, `get`) or Terraform `data` sources. A query
     leaves the account exactly as it was;
   - create or modify: Terraform. If the project already has Terraform for the resource, edit
     that code;
   - delete: remove it with what created it (the Terraform block or `destroy` if
     Terraform-managed, otherwise the CLI).
3. **Rehearse.** Read `terraform plan` before every `apply`, and check that it touches only what
   was asked. If a local emulator or a sandbox account is available and supports the service,
   try there first, remembering that emulators diverge on some details; confirm against the
   real cloud read-only.
4. **Confirm.** Before an action that is irreversible (deleting data, replacing a resource) or
   that costs money (instances, databases, NAT gateways, Elastic IPs, larger sizes), ask, unless
   the user explicitly requested that exact action on that exact resource. To ask, end your turn
   with one plain question naming the resource, the action, and its consequence.
5. **Act.** Use the credentials you were given, as they are: they should be least-privilege and
   separate from the owner's. Tag everything you create, reusing the tags already in the
   project. Use the exact names the user gave. Only when you choose a globally unique name
   yourself (S3 buckets), add a unique suffix such as the account id, never an example name.
   Treat `AccessDenied` as a guardrail: stop, explain it, and offer the cheapest option that
   fits within it. Keep secret values out of your output and context;
   reference secrets by name or ARN.
6. **Verify.** Read the final state from the API; an exit code or a plan is not evidence. Retry
   the read for up to about 60 seconds before declaring failure, because cloud APIs are
   eventually consistent. "Ran without errors, but nothing changed or nothing was found" is
   suspicious: before saying something does not exist, read every page and every region it could
   be in, and otherwise report the result as partial and say where you looked.
7. **Report.** Compare with the start: what was created, changed or deleted (with ids), what now
   costs money, and ask whether anything you created should be removed.

## Tools

Bundled scripts. They use the standard AWS environment (credentials, `AWS_REGION`,
`AWS_ENDPOINT_URL`), print JSON, and exit non-zero on failure; `--help` lists every flag.

- Before step 1: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/whoami.py"` shows the account,
  identity, region and endpoint you are about to read or change.
- Step 1: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/inventory.py" --save /tmp/baseline.json` saves
  a baseline of the account and prints counts. Narrow with `--tag key=value`; widen with
  `--regions r1 r2`. Without `--save` it prints every resource with its id and tags.
- Step 6: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/verify.py" --service lambda --call
  get-function-configuration --params '{"FunctionName": "..."}' --expect
  MemorySize=512` repeats the read until every `--expect PATH=VALUE`
  holds (exit 0) or 60 seconds pass (exit 1).
- Step 7: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/diff_baseline.py" /tmp/baseline.json` lists
  the resources added and removed since the baseline.
