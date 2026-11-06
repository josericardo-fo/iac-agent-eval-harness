from pathlib import Path

import pytest

from iac_harness.agentic.stream import parse_stream

FIXTURE = Path(__file__).parent / "fixtures" / "claude_stream_sample.jsonl"


def test_parse_stream_reads_init_result_and_tool_calls():
    parsed = parse_stream(FIXTURE.read_text().splitlines())
    assert parsed.init["model"] == "Haiku 5.5"
    assert [p["name"] for p in parsed.init["plugins"]] == ["own-tools", "cc-plugin-telemetry"]
    assert parsed.final_text == "Done: retention is now 4 days."
    assert parsed.num_turns == 5
    assert parsed.cost_usd == 0.0123
    assert parsed.usage == {
        "input_tokens": 1200,
        "output_tokens": 300,
        "cache_creation_input_tokens": 5000,
        "cache_read_input_tokens": 20000,
    }
    assert parsed.is_error is False and parsed.subtype == "success"
    assert parsed.permission_denials == []
    assert [c["name"] for c in parsed.tool_calls] == ["Bash", "Write", "Bash", "Bash"]
    assert parsed.tool_counts == {"Bash": 3, "Write": 1}


def test_parse_stream_tolerates_garbage_and_missing_result():
    parsed = parse_stream(["not json", "", '{"type":"assistant","message":{"content":[]}}'])
    assert parsed.init is None
    assert parsed.final_text is None
    assert parsed.usage == {}
    assert parsed.tool_calls == []


def _bash(cmd):
    return {"name": "Bash", "input": {"command": cmd}}


def _write(path, content):
    return {"name": "Write", "input": {"file_path": path, "content": content}}


def test_verified_state_true_when_read_follows_last_mutation():
    from iac_harness.agentic.stream import verified_state

    parsed = parse_stream(FIXTURE.read_text().splitlines())
    # o fixture escreve fix.py (set_queue_attributes), roda e relê com get-queue-attributes
    assert verified_state(parsed.tool_calls) is True


@pytest.mark.parametrize(
    "calls,expected",
    [
        # sem mutação: nada a verificar
        ([_bash("aws ec2 describe-security-groups")], None),
        ([], None),
        # mutação por último
        (
            [
                _bash("aws sqs get-queue-url --queue-name q"),
                _bash("aws sqs set-queue-attributes --queue-url u --attributes x=1"),
            ],
            False,
        ),
        # leitura depois, no mesmo comando
        (
            [
                _bash(
                    "aws s3api put-bucket-versioning --bucket b --versioning-configuration "
                    "Status=Enabled && aws s3api get-bucket-versioning --bucket b"
                )
            ],
            True,
        ),
        # terraform: plan/show/state/output não são evidência (plan não lê o efeito; state é local)
        (
            [_bash("terraform init && terraform apply -auto-approve"), _bash("terraform show")],
            False,
        ),
        ([_bash("terraform apply -auto-approve"), _bash("terraform state list")], False),
        (
            [_bash("terraform apply -auto-approve"), _bash("terraform state show aws_s3_bucket.b")],
            False,
        ),
        ([_bash("terraform apply -auto-approve"), _bash("terraform output -json")], False),
        ([_bash("terraform plan"), _bash("terraform apply -auto-approve")], False),
        ([_bash("terraform destroy -auto-approve"), _bash("terraform plan")], False),
        ([_bash("terraform apply -auto-approve"), _bash("aws s3api list-buckets")], True),
        # scripts do toolkit (verify/inventory/diff_baseline/whoami) contam como leitura
        (
            [
                _bash("terraform apply -auto-approve"),
                _bash(
                    'python3 "${CLAUDE_PLUGIN_ROOT}/scripts/verify.py" --service s3 '
                    "--call list_buckets"
                ),
            ],
            True,
        ),
        (
            [
                _bash("terraform apply -auto-approve"),
                _bash("python3 /opt/conditions/own-tools/scripts/inventory.py --save /tmp/a.json"),
            ],
            True,
        ),
        (
            [
                _bash("aws sqs delete-queue --queue-url u"),
                _bash('python3 "$CLAUDE_PLUGIN_ROOT/scripts/diff_baseline.py" /tmp/b.json'),
            ],
            True,
        ),
        (
            [
                _bash("terraform apply -auto-approve"),
                _bash('python3 "${CLAUDE_PLUGIN_ROOT}/scripts/whoami.py"'),
            ],
            True,
        ),
        # inspeção de arquivos é neutra: nunca muta, nunca lê o estado
        ([_bash("grep -i aws main.tf")], None),
        ([_bash("grep -rn aws main.tf variables.tf outputs.tf")], None),
        ([_bash("cat main.tf | grep terraform apply"), _bash("head -n 20 main.tf")], None),
        ([_bash("find . -name '*.tf' -exec grep aws {} \\;"), _bash("wc -l main.tf")], None),
        ([_bash("sed -n '1,40p' main.tf; tail -5 main.tf; ls -la /work")], None),
        ([_bash("jq . answer.json"), _bash('echo "aws s3 rb s3://b --force"')], None),
        ([_bash("grep -n 'delete_queue(' fix.py")], None),
        # mutação depois de inspeção de arquivo continua mutação
        ([_bash("grep aws main.tf && aws s3 mb s3://b")], False),
        ([_bash("AWS_REGION=us-east-1 aws s3 cp f s3://b/k")], False),
        ([_bash("aws s3 sync . s3://b"), _bash("aws s3 ls s3://b")], True),
        ([_bash("aws s3 mv s3://b/a s3://b/c")], False),
        # waiters e sts leem (assume-role não muda o estado)
        (
            [
                _bash("aws s3api delete-bucket --bucket b"),
                _bash("aws s3api wait bucket-not-exists"),
            ],
            True,
        ),
        ([_bash("aws sts assume-role --role-arn r --role-session-name s")], None),
        # script com leitura e mutação é mutação (não conta como releitura de si mesmo)
        (
            [
                _write(
                    "/work/fix.py",
                    "import boto3\nc = boto3.client('sqs')\n"
                    "c.purge_queue(QueueUrl=u)\nprint(c.get_queue_attributes(QueueUrl=u))\n",
                ),
                _bash("cd /work && python3 fix.py"),
            ],
            False,
        ),
        # python -c com só leitura conta como releitura
        (
            [
                _bash("aws sqs delete-queue --queue-url u"),
                _bash("python3 -c \"import boto3; print(boto3.client('sqs').list_queues())\""),
            ],
            True,
        ),
        # script escrito via heredoc e executado depois
        (
            [
                _bash("aws sqs delete-queue --queue-url u"),
                _bash(
                    "cat > /work/check.py <<'EOF'\nimport boto3\n"
                    "print(boto3.client('sqs').list_queues())\nEOF\npython3 /work/check.py"
                ),
            ],
            True,
        ),
        # script do agente com o mesmo nome de um script do toolkit: vale o conteúdo
        (
            [
                _write(
                    "/work/verify.py", "import boto3\nboto3.client('s3').delete_bucket(Bucket=b)\n"
                ),
                _bash("python3 verify.py"),
            ],
            False,
        ),
        # aws s3 rm/rb mutam; aws s3 ls lê
        ([_bash("aws s3 rb s3://b --force"), _bash("aws s3 ls")], True),
        # script boto3 escrito e executado: classificado pelo conteúdo
        (
            [
                _write("/work/q.py", "import boto3\nboto3.client('ec2').describe_vpcs()\n"),
                _bash("aws dynamodb update-table --table-name t --billing-mode PAY_PER_REQUEST"),
                _bash("python3 q.py"),
            ],
            True,
        ),
        (
            [
                _bash("aws iam list-users"),
                _write("/work/main.py", "c.delete_queue(QueueUrl=u)\n"),
                _bash("python3 /work/main.py"),
            ],
            False,
        ),
        # heredoc inline do python
        (
            [
                _bash("aws sqs delete-queue --queue-url u"),
                _bash(
                    "python3 - <<'PY'\nimport boto3\nprint(boto3.client('sqs').list_queues())\nPY"
                ),
            ],
            True,
        ),
        # comandos que não tocam AWS não contam
        ([_bash("aws sqs delete-queue --queue-url u"), _bash("cat main.tf; ls -la")], False),
    ],
)
def test_verified_state_cases(calls, expected):
    from iac_harness.agentic.stream import verified_state

    assert verified_state(calls) is expected


def test_state_ops_counts_toolkit_verify_calls_and_mutation():
    from iac_harness.agentic.stream import state_ops

    ops = state_ops(
        [
            _bash('python3 "${CLAUDE_PLUGIN_ROOT}/scripts/inventory.py" --save /tmp/b.json'),
            _bash("terraform apply -auto-approve"),
            _bash('python3 "${CLAUDE_PLUGIN_ROOT}/scripts/verify.py" --service s3'),
            _bash('python3 "${CLAUDE_PLUGIN_ROOT}/scripts/whoami.py"'),
        ]
    )
    assert ops.mutated is True and ops.verified is True
    # só verify e inventory contam como chamadas de verificação do toolkit
    assert ops.verify_calls == 2


def test_state_ops_without_mutation():
    from iac_harness.agentic.stream import state_ops

    ops = state_ops([_bash("grep -i aws main.tf"), _bash("aws s3 ls")])
    assert ops.mutated is False and ops.verified is None and ops.verify_calls == 0


def test_redact_replaces_every_secret_value():
    from iac_harness.agentic.stream import redact

    line = FIXTURE.read_text().splitlines()[0]
    assert "sk-test-SECRET-123456" in line
    out = redact(line, ["sk-test-SECRET-123456", "", "AKIAEXAMPLE1"])
    assert "sk-test-SECRET-123456" not in out and "***" in out
    assert redact("a AKIAEXAMPLE1 b AKIAEXAMPLE1", ["AKIAEXAMPLE1"]) == "a *** b ***"


def test_redact_ignores_short_values():
    from iac_harness.agentic.stream import redact

    # valor curto demais (ex.: "test") apagaria texto legítimo do transcript
    assert redact("aws test creds", ["test", None]) == "aws test creds"
