from pathlib import Path

from iac_harness.datasets.multi_iac_eval import (
    DEFAULT_REVISION,
    MultiIacEvalAdapter,
    is_compatible,
    providers_of,
)

FIXTURE = Path(__file__).parent / "fixtures" / "multi_iac_eval_sample.csv"


def test_load_filters_dedups_and_keeps_stable_id():
    adapter = MultiIacEvalAdapter(FIXTURE)
    triples = adapter.load()
    assert adapter.version == DEFAULT_REVISION
    # Duplicata, module e cloudformation ficam de fora; awscc entra.
    assert [t.triple_id for t in triples] == ["s3.tf_0#0", "cc.tf_0#2"]
    triple = triples[0]
    assert triple.utterance == "Add a random suffix."
    assert 'resource "random_id"' in triple.expected
    assert triple.source_repo == "repo"


def test_providers_and_compatibility():
    assert providers_of('provider "aws" {}\ndata "archive_file" "z" {}\n') == {"aws", "archive"}
    assert is_compatible('resource "aws_vpc" "v" {}', 'resource "random_id" "r" {}')
    assert is_compatible('resource "awscc_s3_bucket" "b" {}', 'resource "aws_vpc" "v" {}')
    assert not is_compatible('resource "aws_vpc" "v" {}', 'resource "tls_private_key" "k" {}')
    assert not is_compatible('module "m" {}\nresource "aws_vpc" "v" {}', "")
    assert not is_compatible("", "")
