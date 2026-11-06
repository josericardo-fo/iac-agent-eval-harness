import json
import subprocess
from pathlib import Path

import pytest

from iac_harness.cli import build_executor, build_toolchain, final_cleanup
from iac_harness.executors import aws_sweep
from iac_harness.executors.aws import RealAwsExecutor
from iac_harness.executors.aws_sweep import ORDER, inventory, plan_deletions
from iac_harness.executors.floci import FlociExecutor
from iac_harness.modalities.execute import run_artifact
from iac_harness.modalities.spec import MODALITIES
from iac_harness.toolchain import Toolchain
from iac_harness.types import CheckStatus
from test_modalities import RecordingToolchain

ARN = "arn:aws:iam::931493484341:user/iac-harness"
_IAC = {"IAC_AWS_ACCESS_KEY_ID": "AKIAIACFAKE", "IAC_AWS_SECRET_ACCESS_KEY": "iac-secret-value"}


@pytest.fixture
def iac_env(monkeypatch):
    for k, v in _IAC.items():
        monkeypatch.setenv(k, v)
    # Credenciais do host que nunca podem entrar.
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAHOST")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "host-secret")


def test_aws_env_uses_iac_credentials_without_endpoint(iac_env):
    tc = Toolchain(mode="local", target="aws")
    env = tc._local_env()
    assert env["AWS_ACCESS_KEY_ID"] == "AKIAIACFAKE"
    assert env["AWS_SECRET_ACCESS_KEY"] == "iac-secret-value"
    assert env["AWS_REGION"] == env["AWS_DEFAULT_REGION"] == "us-east-1"
    assert "AWS_ENDPOINT_URL" not in env
    assert tc.network == "bridge"


@pytest.mark.parametrize("missing", list(_IAC))
def test_aws_target_without_iac_credentials_raises(iac_env, monkeypatch, missing):
    monkeypatch.delenv(missing)
    with pytest.raises(RuntimeError, match=missing):
        Toolchain(mode="docker", target="aws")


def test_aws_docker_argv_has_no_secret_value(iac_env, monkeypatch):
    calls = []

    def fake_run(cmd, **kw):
        calls.append((cmd, kw))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    Toolchain(mode="docker", target="aws").run(["aws", "--version"], Path("."))
    cmd, kw = calls[0]
    joined = " ".join(cmd)
    assert "iac-secret-value" not in joined and "AKIAIACFAKE" not in joined
    assert "host-secret" not in joined and "AWS_ENDPOINT_URL" not in joined
    assert cmd[cmd.index("--network") + 1] == "bridge"
    i = cmd.index("AWS_SECRET_ACCESS_KEY")
    assert cmd[i - 1] == "-e"  # só o nome; o valor vai no env do cliente docker
    assert kw["env"]["AWS_SECRET_ACCESS_KEY"] == "iac-secret-value"
    assert kw["env"]["AWS_ACCESS_KEY_ID"] == "AKIAIACFAKE"


def test_emulator_target_is_unchanged(iac_env, monkeypatch):
    calls = []

    def fake_run(cmd, **kw):
        calls.append((cmd, kw))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    tc = Toolchain(mode="docker")
    assert tc.target == "emulator" and tc.network == "iac-sandbox"
    assert tc.sandbox_env()["AWS_ACCESS_KEY_ID"] == "test"
    tc.run(["terraform", "version"], Path("."))
    cmd, kw = calls[0]
    assert "env" not in kw
    assert "AWS_ACCESS_KEY_ID=test" in cmd and "AWS_ENDPOINT_URL=http://floci:4566" in cmd
    assert "iac-secret-value" not in " ".join(cmd)


class _StsToolchain(Toolchain):
    """Toolchain aws que responde ao sts com um ARN fixo, sem docker."""

    def __init__(self, arn: str) -> None:
        super().__init__(mode="local", target="aws")
        self.arn = arn
        self.calls: list[list[str]] = []

    def run(self, args, workdir, timeout_s=None):
        self.calls.append(list(args))
        out = json.dumps({"Account": "931493484341", "Arn": self.arn})
        return subprocess.CompletedProcess(args, 0, out + "\n", "")


class _AwsToolchain(_StsToolchain):
    """Também responde ao aws_sweep.py: inventário fixo e sweep sem sobras."""

    INVENTORY = {"s3_buckets": ["fresh-snapshot"]}

    def run(self, args, workdir, timeout_s=None):
        if args[:1] != ["python3"]:
            return super().run(args, workdir, timeout_s)
        self.calls.append(list(args))
        if args[2] == "inventory":
            out = self.INVENTORY
        else:
            self.sent_baseline = json.loads((Path(workdir) / args[3]).read_text())
            out = {"deleted": {}, "failed": {}, "remaining": {}}
        return subprocess.CompletedProcess(args, 0, json.dumps(out) + "\n", "")


def _full_baseline(**items):
    """Baseline com todas as categorias do ORDER (categoria ausente levanta erro)."""
    return {c: items.get(c, []) for c in ORDER}


def _modes(tc):
    return [c[2] for c in tc.calls if c[0] == "python3"]


def test_baseline_is_loaded_from_existing_file(iac_env, tmp_path):
    baseline_file = tmp_path / "aws-baseline.json"
    baseline_file.write_text(json.dumps(_full_baseline(s3_buckets=["clean-account"])))
    tc = _AwsToolchain(ARN)
    RealAwsExecutor(tc, expected_arn=ARN, baseline_file=baseline_file).reset()
    assert _modes(tc) == ["sweep"]  # sem inventário novo
    assert tc.sent_baseline == _full_baseline(s3_buckets=["clean-account"])


def test_baseline_file_is_created_when_missing(iac_env, tmp_path):
    baseline_file = tmp_path / "sub" / "aws-baseline.json"
    tc = _AwsToolchain(ARN)
    RealAwsExecutor(tc, expected_arn=ARN, baseline_file=baseline_file).reset()
    assert _modes(tc) == ["inventory", "sweep"]
    assert json.loads(baseline_file.read_text()) == _AwsToolchain.INVENTORY
    assert tc.sent_baseline == _AwsToolchain.INVENTORY


def test_cleanup_sweeps_when_baseline_exists(iac_env, tmp_path):
    baseline_file = tmp_path / "aws-baseline.json"
    baseline_file.write_text(json.dumps(_full_baseline()))
    tc = _AwsToolchain(ARN)
    RealAwsExecutor(tc, expected_arn=ARN, baseline_file=baseline_file).cleanup()
    assert _modes(tc) == ["sweep"]


def test_cleanup_without_baseline_does_not_snapshot(iac_env, tmp_path):
    baseline_file = tmp_path / "aws-baseline.json"
    tc = _AwsToolchain(ARN)
    RealAwsExecutor(tc, expected_arn=ARN, baseline_file=baseline_file).cleanup()
    assert tc.calls == [] and not baseline_file.exists()


def test_cleanup_is_noop_on_floci(monkeypatch):
    def no_subprocess(*a, **kw):
        raise AssertionError("cleanup do Floci não deve rodar comandos")

    monkeypatch.setattr(subprocess, "run", no_subprocess)
    assert FlociExecutor(Toolchain(mode="local")).cleanup() is None


def test_final_cleanup_does_not_mask_original_error(capsys):
    class Failing:
        NAME = "X"

        def cleanup(self):
            raise RuntimeError("cleanup quebrou")

    with pytest.raises(ValueError, match="original"):
        try:
            raise ValueError("original")
        finally:
            final_cleanup(Failing())
    assert "cleanup quebrou" in capsys.readouterr().err
    with pytest.raises(RuntimeError, match="cleanup quebrou"):
        final_cleanup(Failing())


def test_wrong_arn_is_refused(iac_env):
    executor = RealAwsExecutor(_StsToolchain("arn:aws:iam::931493484341:root"), expected_arn=ARN)
    with pytest.raises(RuntimeError, match="recusada"):
        executor.reset()
    assert all(c[0] == "aws" for c in executor._tc.calls)  # nada foi apagado


def test_expected_arn_is_accepted(iac_env):
    executor = RealAwsExecutor(_StsToolchain(ARN), expected_arn=ARN)
    assert executor.identity() == {"account": "931493484341", "arn": ARN}


def test_override_is_none_and_never_unsupported(iac_env):
    executor = RealAwsExecutor(_StsToolchain(ARN), expected_arn=ARN)
    assert executor.override_for({"main.tf": 'provider "aws" {}'}) is None
    assert executor.classify_error("Error: not implemented (501)") is CheckStatus.FAIL


def test_plan_deletions_only_outside_baseline():
    baseline = {
        "ec2_vpcs": ["vpc-old"],
        "s3_buckets": ["mine"],
        "iam_users": ["mod-kept"],
        "kms_keys": ["key-old"],
    }
    current = {
        "ec2_vpcs": ["vpc-old", "vpc-new"],
        "s3_buckets": ["mine", "agent-bucket"],
        "iam_users": ["mod-kept", "del-new", "iac-harness", "someone"],
        "iam_groups": ["mod-group", "admins"],
        "kms_keys": ["key-old"],
        "sqs_queues": [],
        "unknown_category": ["x"],
    }
    plan = plan_deletions(current, baseline)
    assert plan == {
        "ec2_vpcs": ["vpc-new"],
        "s3_buckets": ["agent-bucket"],
        "iam_users": ["del-new"],
        "iam_groups": ["mod-group"],
    }
    assert list(plan) == [c for c in ORDER if c in plan]  # ordem de dependência
    assert plan_deletions(baseline, baseline) == {}


class _FakeClient:
    """Responde a paginators e chamadas diretas com as respostas configuradas por operação."""

    def __init__(self, responses):
        self.responses = responses

    def get_paginator(self, op):
        client = self

        class _Paginator:
            def paginate(self, **kwargs):
                key = (op, tuple(kwargs.get("AlarmTypes", ())))
                return [client.responses.get(key, client.responses.get(op, {}))]

        return _Paginator()

    def __getattr__(self, op):
        return lambda **kwargs: self.responses.get(op, {})


class _FakeSession:
    def __init__(self, responses):
        self.responses = responses

    def client(self, service, **kwargs):
        return _FakeClient(self.responses.get(service, {}))


def test_inventory_excludes_aws_defaults():
    responses = {
        "elasticache": {
            "describe_users": {"Users": [{"UserId": "default"}, {"UserId": "agent-user"}]},
            "describe_user_groups": {"UserGroups": [{"UserGroupId": "agent-group"}]},
        },
        "dax": {
            "describe_subnet_groups": {
                "SubnetGroups": [{"SubnetGroupName": "default"}, {"SubnetGroupName": "mine"}]
            },
            "describe_parameter_groups": {
                "ParameterGroups": [
                    {"ParameterGroupName": "default.dax1.0"},
                    {"ParameterGroupName": "pg"},
                ]
            },
        },
        "iam": {
            "list_roles": {
                "Roles": [{"RoleName": "iac-harness-awsb-fn"}, {"RoleName": "AWSServiceRole"}]
            },
        },
        "cloudfront": {
            "list_distributions": {"DistributionList": {"Items": [{"Id": "E1"}]}},
            "list_origin_access_controls": {"OriginAccessControlList": {"Items": [{"Id": "O1"}]}},
        },
        "cloudwatch": {
            ("describe_alarms", ("CompositeAlarm",)): {"CompositeAlarms": [{"AlarmName": "c"}]},
            ("describe_alarms", ("MetricAlarm",)): {"MetricAlarms": [{"AlarmName": "m"}]},
        },
        "lambda": {"list_functions": {"Functions": [{"FunctionName": "fn"}]}},
        "glacier": {"list_vaults": {"VaultList": [{"VaultName": "v"}]}},
        "sagemaker": {"list_human_task_uis": {"HumanTaskUiSummaries": [{"HumanTaskUiName": "u"}]}},
        "lightsail": {"get_relational_databases": {"relationalDatabases": [{"name": "db"}]}},
    }
    inv = inventory(_FakeSession(responses))
    assert set(ORDER) == set(inv)
    assert inv["elasticache_users"] == ["agent-user"]
    assert inv["dax_subnet_groups"] == ["mine"] and inv["dax_parameter_groups"] == ["pg"]
    assert inv["iam_roles"] == ["iac-harness-awsb-fn"]
    assert inv["cloudfront_distributions"] == ["E1"] and inv["cloudfront_oacs"] == ["O1"]
    assert inv["cloudwatch_composite_alarms"] == ["c"] and inv["cloudwatch_metric_alarms"] == ["m"]
    # Baseline sem as categorias novas: ainda assim nenhum default entra no plano.
    plan = plan_deletions(inv, {})
    assert "default" not in plan["elasticache_users"]
    assert not any(n.startswith("default") for n in plan["dax_subnet_groups"])


def test_inventory_lists_unassigned_mfa_devices_and_kinesis_video_streams():
    root_mfa = "arn:aws:iam::123:mfa/root-account-mfa-device"
    responses = {
        "iam": {
            "list_virtual_mfa_devices": {
                "VirtualMFADevices": [
                    {"SerialNumber": "arn:aws:iam::123:mfa/example"},
                    {"SerialNumber": root_mfa, "User": {"Arn": "arn:aws:iam::123:root"}},
                    {
                        "SerialNumber": "arn:aws:iam::123:mfa/u",
                        "User": {"Arn": "arn:aws:iam::123:user/u"},
                    },
                ]
            },
        },
        "kinesisvideo": {
            "list_streams": {"StreamInfoList": [{"StreamARN": "arn:kv:s1", "StreamName": "s1"}]}
        },
        "cloudfront": {
            "list_distributions": {"DistributionList": {}},
            "list_origin_access_controls": {"OriginAccessControlList": {}},
        },
    }
    inv = inventory(_FakeSession(responses))
    assert inv["iam_virtual_mfa_devices"] == ["arn:aws:iam::123:mfa/example"]
    assert inv["kinesis_video_streams"] == ["arn:kv:s1"]
    # Mesmo com baseline vazio, dispositivo atribuído (root ou usuário) nunca entra no plano.
    plan = plan_deletions(inv, {})
    assert plan["iam_virtual_mfa_devices"] == ["arn:aws:iam::123:mfa/example"]


def test_kinesis_video_stream_deleter_uses_arn_and_waits_until_gone(monkeypatch):
    class Kv:
        def __init__(self):
            self.streams = ["arn:kv:s1"]
            self.deleted = []

        def delete_stream(self, StreamARN):
            self.deleted.append(StreamARN)

        def get_paginator(self, op):
            kv = self

            class _Paginator:
                def paginate(self, **kwargs):
                    listed = [{"StreamARN": a} for a in kv.streams]
                    kv.streams = []  # some na segunda leitura
                    return [{"StreamInfoList": listed}]

            return _Paginator()

    kv = Kv()

    class Session(_FakeSession):
        def client(self, service, **kwargs):
            return kv if service == "kinesisvideo" else super().client(service, **kwargs)

    monkeypatch.setattr(aws_sweep.time, "sleep", lambda s: None)
    aws_sweep._deleters(Session({}))["kinesis_video_streams"]("arn:kv:s1")
    assert kv.deleted == ["arn:kv:s1"] and kv.streams == []


def test_inventory_lists_live_nat_gateways_and_elastic_ips():
    responses = {
        "ec2": {
            "describe_nat_gateways": {
                "NatGateways": [
                    {"NatGatewayId": "nat-live", "State": "available"},
                    {"NatGatewayId": "nat-going", "State": "deleting"},
                    {"NatGatewayId": "nat-gone", "State": "deleted"},
                ]
            },
            "describe_addresses": {"Addresses": [{"AllocationId": "eipalloc-1"}]},
        },
        "cloudfront": {
            "list_distributions": {"DistributionList": {}},
            "list_origin_access_controls": {"OriginAccessControlList": {}},
        },
    }
    inv = inventory(_FakeSession(responses))
    assert inv["ec2_nat_gateways"] == ["nat-live", "nat-going"]
    assert inv["ec2_elastic_ips"] == ["eipalloc-1"]


def test_plan_deletes_nat_then_eip_before_network():
    current = {
        "ec2_vpcs": ["vpc-1"],
        "ec2_internet_gateways": ["igw-1"],
        "ec2_subnets": ["subnet-1"],
        "ec2_network_interfaces": ["eni-1"],
        "ec2_elastic_ips": ["eipalloc-1"],
        "ec2_nat_gateways": ["nat-1"],
    }
    assert list(plan_deletions(current, {})) == [
        "ec2_nat_gateways",
        "ec2_elastic_ips",
        "ec2_network_interfaces",
        "ec2_subnets",
        "ec2_internet_gateways",
        "ec2_vpcs",
    ]


def test_eni_delete_not_found_is_not_a_failure():
    class NotFound(Exception):
        response = {"Error": {"Code": "InvalidNetworkInterfaceID.NotFound"}}

    class Denied(Exception):
        response = {"Error": {"Code": "UnauthorizedOperation"}}

    class Ec2(_FakeClient):
        def __init__(self, exc):
            super().__init__({})
            self.exc = exc

        def delete_network_interface(self, NetworkInterfaceId):
            raise self.exc

    def session(exc):
        class Session(_FakeSession):
            def client(self, service, **kwargs):
                return Ec2(exc) if service == "ec2" else super().client(service, **kwargs)

        return Session({})

    aws_sweep._deleters(session(NotFound()))["ec2_network_interfaces"]("eni-gone")
    with pytest.raises(Denied):
        aws_sweep._deleters(session(Denied()))["ec2_network_interfaces"]("eni-x")


def _fake_sweep_env(monkeypatch, groups, fail_times):
    """Inventário em memória de user groups; o deleter falha `fail_times[item]` vezes antes."""
    calls = []

    def delete(item):
        calls.append(item)
        if fail_times.get(item, 0) > 0:
            fail_times[item] -= 1
            raise RuntimeError(f"{item} has status creating")
        groups.remove(item)

    monkeypatch.setattr(aws_sweep, "inventory", lambda s: {"elasticache_user_groups": list(groups)})
    monkeypatch.setattr(aws_sweep, "_deleters", lambda s: {"elasticache_user_groups": delete})
    monkeypatch.setattr(aws_sweep.time, "sleep", lambda s: None)
    return calls


def test_sweep_retries_transient_delete_failure(monkeypatch):
    calls = _fake_sweep_env(monkeypatch, ["ug"], {"ug": 1})
    result = aws_sweep.sweep(None, {"elasticache_user_groups": []})
    assert calls == ["ug", "ug"]
    assert result == {"deleted": {"elasticache_user_groups": ["ug"]}, "failed": {}, "remaining": {}}


def test_sweep_permanent_failure_ends_in_failed_and_remaining(monkeypatch):
    calls = _fake_sweep_env(monkeypatch, ["ug"], {"ug": 10**6})
    result = aws_sweep.sweep(None, {"elasticache_user_groups": []})
    assert len(calls) == aws_sweep.SETTLE_TRIES  # 1ª passada + uma por espera
    assert result["deleted"] == {}
    assert "creating" in result["failed"]["elasticache_user_groups"]["ug"]
    assert result["remaining"] == {"elasticache_user_groups": ["ug"]}


def test_plan_deletions_filters_roles_by_prefix():
    plan = plan_deletions({"iam_roles": ["iac-harness-x", "OrganizationAccountAccessRole"]}, {})
    assert plan == {"iam_roles": ["iac-harness-x"]}


@pytest.mark.parametrize(
    ("first", "then"),
    [
        ("elasticache_user_groups", "elasticache_users"),
        ("cloudfront_distributions", "cloudfront_oacs"),
        ("cloudfront_distributions", "s3_buckets"),
        ("lambda_functions", "iam_roles"),
        ("dax_subnet_groups", "ec2_subnets"),
        ("dax_subnet_groups", "ec2_vpcs"),
        ("glacier_vaults", "sns_topics"),
        ("iam_virtual_mfa_devices", "iam_users"),
        ("cloudwatch_composite_alarms", "cloudwatch_metric_alarms"),
        ("cloudwatch_metric_alarms", "sns_topics"),
        ("ec2_instances", "ec2_nat_gateways"),
        ("ec2_nat_gateways", "ec2_elastic_ips"),
        ("ec2_elastic_ips", "ec2_network_interfaces"),
        ("ec2_elastic_ips", "ec2_subnets"),
        ("ec2_elastic_ips", "ec2_internet_gateways"),
        ("ec2_elastic_ips", "ec2_vpcs"),
    ],
)
def test_order_respects_dependencies(first, then):
    assert ORDER.index(first) < ORDER.index(then)


def test_baseline_missing_categories_raises_without_touching_file(iac_env, tmp_path):
    baseline_file = tmp_path / "aws-baseline.json"
    old = {c: [] for c in ORDER if c not in ("lambda_functions", "iam_roles")}
    baseline_file.write_text(json.dumps(old))

    tc = _AwsToolchain(ARN)
    with pytest.raises(RuntimeError, match="iam_roles"):
        RealAwsExecutor(tc, expected_arn=ARN, baseline_file=baseline_file).reset()
    assert _modes(tc) == []  # nem inventário nem sweep
    assert json.loads(baseline_file.read_text()) == old


def test_build_wires_aws_kind(iac_env):
    cfg = {"sandbox": {"kind": "aws", "expected_arn": ARN, "region": "us-east-1"}}
    tc = build_toolchain(cfg)
    assert tc.target == "aws" and tc.network == "bridge"
    executor = build_executor(cfg, tc, allow_real_aws=True)
    assert isinstance(executor, RealAwsExecutor) and executor.expected_arn == ARN
    assert executor.baseline_file == Path("runs/aws-baseline.json")
    cfg["sandbox"]["baseline_file"] = "x/b.json"
    assert build_executor(cfg, tc, allow_real_aws=True).baseline_file == Path("x/b.json")
    with pytest.raises(ValueError, match="expected_arn"):
        build_executor({"sandbox": {"kind": "aws"}}, tc, allow_real_aws=True)


def test_abc_commands_refuse_aws_kind(iac_env):
    cfg = {"sandbox": {"kind": "aws", "expected_arn": ARN}}
    tc = build_toolchain(cfg)
    with pytest.raises(SystemExit, match="modality"):
        build_executor(cfg, tc)  # prevalidate, prevalidate-miac, run e resume
    assert isinstance(build_executor(cfg, tc, allow_real_aws=True), RealAwsExecutor)


def test_run_artifact_on_aws_never_flags_unsupported(iac_env, tmp_path):
    tc = RecordingToolchain(1, stderr="Error: NotImplemented: CreateThing")
    executor = RealAwsExecutor(_StsToolchain(ARN), expected_arn=ARN)
    res, _ = run_artifact(tc, executor, MODALITIES["cli"], "x", tmp_path, "r", "t", "reference")
    assert not res.ok and res.unsupported is False


def test_build_default_kind_stays_emulator():
    tc = build_toolchain({})
    assert tc.target == "emulator"
    assert type(build_executor({}, tc)) is FlociExecutor


def test_identity_is_cached_until_forgotten(iac_env):
    tc = _StsToolchain(ARN)
    executor = RealAwsExecutor(tc, expected_arn=ARN)
    executor.identity()
    executor.identity()
    assert len(tc.calls) == 1  # cache: o comportamento das modalidades não muda
    executor.forget_identity()
    tc.arn = "arn:aws:iam::931493484341:root"  # credencial trocada no meio do run
    with pytest.raises(RuntimeError, match="recusada"):
        executor.identity()
    assert len(tc.calls) == 2
