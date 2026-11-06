
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
