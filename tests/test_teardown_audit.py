import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location(
    "audit_teardown", ROOT / "scripts/audit-teardown.py"
)
audit_teardown = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = audit_teardown
spec.loader.exec_module(audit_teardown)


class AwsError(RuntimeError):
    def __init__(self, code):
        self.response = {"Error": {"Code": code}}


class CleanS3:
    def head_bucket(self, **kwargs):
        raise AwsError("404")


class CleanCodeBuild:
    def batch_get_projects(self, **kwargs):
        return {"projects": []}


class CleanSSM:
    def get_parameter(self, **kwargs):
        raise AwsError("ParameterNotFound")


class CleanLambda:
    def get_function(self, **kwargs):
        raise AwsError("ResourceNotFoundException")


class CleanApiGateway:
    def get_rest_apis(self, **kwargs):
        return {"items": []}

    def get_api_keys(self, **kwargs):
        return {"items": []}

    def get_usage_plans(self, **kwargs):
        return {"items": []}


class CleanBedrock:
    def list_model_import_jobs(self, **kwargs):
        return {"modelImportJobSummaries": []}

    def list_guardrails(self, **kwargs):
        return {"guardrails": []}

    def list_imported_models(self, **kwargs):
        return {"modelSummaries": []}


class CleanIAM:
    def get_role(self, **kwargs):
        raise AwsError("NoSuchEntity")


class CleanLogs:
    def describe_log_groups(self, **kwargs):
        return {"logGroups": []}


def clients():
    return {
        "s3": CleanS3(),
        "codebuild": CleanCodeBuild(),
        "ssm": CleanSSM(),
        "lambda": CleanLambda(),
        "apigateway": CleanApiGateway(),
        "bedrock": CleanBedrock(),
        "iam": CleanIAM(),
        "logs": CleanLogs(),
    }


def config():
    return audit_teardown.AuditConfig(
        project_name="bedrock-model-foundry",
        region="us-east-1",
        account_id="123456789012",
        endpoints=("pii-mask", "denied-topic"),
    )


def test_audit_passes_when_all_scoped_resources_are_absent():
    assert audit_teardown.audit(config(), clients()) == []


def test_audit_reports_exact_remaining_resources():
    active = clients()
    active["codebuild"] = SimpleNamespace(batch_get_projects=lambda **kwargs: {
        "projects": [{"name": "bedrock-model-foundry-model-lifecycle"}]
    })
    active["bedrock"] = SimpleNamespace(
        list_guardrails=lambda **kwargs: {"guardrails": []},
        list_model_import_jobs=lambda **kwargs: {"modelImportJobSummaries": []},
        list_imported_models=lambda **kwargs: {"modelSummaries": [{
            "modelName": "bedrock-model-foundry-qwen-2-5-1-5b"
        }]},
    )

    remaining = audit_teardown.audit(config(), active)

    assert remaining == [
        "CodeBuild project bedrock-model-foundry-model-lifecycle",
        "imported model bedrock-model-foundry-qwen-2-5-1-5b",
    ]


def test_audit_uses_account_and_region_in_bucket_identity():
    assert config().bucket == "bedrock-model-foundry-123456789012-us-east-1"


def test_audit_reports_matching_in_progress_import_job():
    active = clients()
    bedrock = active["bedrock"]
    bedrock.list_model_import_jobs = lambda **kwargs: {
        "modelImportJobSummaries": [{
            "status": "InProgress",
            "jobArn": "arn:aws:bedrock:us-east-1:123:model-import-job/pending",
            "importedModelName": "bedrock-model-foundry-qwen-2-5-1-5b",
        }]
    }

    remaining = audit_teardown.audit(config(), active)

    assert remaining == [
        "in-progress model import arn:aws:bedrock:us-east-1:123:model-import-job/pending"
    ]
