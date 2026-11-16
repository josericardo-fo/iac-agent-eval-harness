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

## How to ask

Ask only when the answer would change what gets created, changed or deleted; otherwise use the
safe default and report it. When you ask:

- Ask one question, about the highest open decision first: which resources, then how they
  connect, then their settings.
- Offer 2 to 4 concrete options that cover the plausible cases, plus "not sure: use the safe
  default".
- For each option, say in plain words what it costs per month, what it exposes (who can reach
  or read it), and how it affects speed or availability.
- You may recommend one option with a one-line reason, but let the user choose.
- After the answer, restate the chosen option and its main risk in one sentence, then act.

For an engineer, keep the options terse. For a layperson, avoid jargon or explain it in a few
words.

## Flow

1. **Understand.** Restate the request in one sentence. When it refers to something that should
   already exist ("the bucket", "table X", "the dev queue"), list the account's resources first
   and find it. If nothing matches, or several do, stop and ask which one; creating a
   look-alike on a guess hides the user's mistake.
   When the request asks for something new and refers to nothing that should already exist,
   there is nothing to look up: choose safe, low-cost defaults for the details the user left
   open (sizes, names, settings), and report those choices in step 7 instead of asking about
   them. If it is unclear what kind of resource or setup the person needs, ask (see How to ask).
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
   with one question naming the resource and the action, following How to ask.
   A request to create a resource of a given type is that explicit request: pick the smallest,
   cheapest option that meets it, create it, and report the cost.
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
