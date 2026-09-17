#!/usr/bin/env python3
"""Verify that disposable Model Foundry resources are absent after destroy."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List


@dataclass(frozen=True)
class AuditConfig:
    project_name: str
    region: str
    account_id: str
    endpoints: tuple[str, ...]
    model_suffix: str = "qwen-2-5-1-5b"

    @property
    def bucket(self) -> str:
        return f"{self.project_name}-{self.account_id}-{self.region}"

    @property
    def imported_model_name(self) -> str:
        return f"{self.project_name}-{self.model_suffix}"


def _error_code(error: Exception) -> str:
    return getattr(error, "response", {}).get("Error", {}).get("Code", "")


def _pages(
    client: Any, operation: str, result_key: str, pagination_key: str = "nextToken", **kwargs
) -> Iterable[dict]:
    token = None
    while True:
        call = dict(kwargs)
        if token:
            call[pagination_key] = token
        response = getattr(client, operation)(**call)
        yield from response.get(result_key, [])
        token = response.get(pagination_key)
        if not token:
            return


def audit(config: AuditConfig, clients: Dict[str, Any]) -> List[str]:
    remaining = []

    try:
        clients["s3"].head_bucket(Bucket=config.bucket)
        remaining.append(f"S3 bucket {config.bucket}")
    except Exception as error:
        if _error_code(error) not in {"404", "NoSuchBucket", "NotFound"}:
            remaining.append(f"S3 bucket {config.bucket} (unverifiable: {_error_code(error)})")

    build_name = f"{config.project_name}-model-lifecycle"
    builds = clients["codebuild"].batch_get_projects(names=[build_name]).get("projects", [])
    if any(project.get("name") == build_name for project in builds):
        remaining.append(f"CodeBuild project {build_name}")

    parameter_name = f"/{config.project_name}/imported-model-arn"
    try:
        clients["ssm"].get_parameter(Name=parameter_name)
        remaining.append(f"SSM parameter {parameter_name}")
    except Exception as error:
        if _error_code(error) != "ParameterNotFound":
            remaining.append(f"SSM parameter {parameter_name} (unverifiable: {_error_code(error)})")

    for endpoint in config.endpoints:
        function_name = f"{config.project_name}-{endpoint}"
        try:
            clients["lambda"].get_function(FunctionName=function_name)
            remaining.append(f"Lambda function {function_name}")
        except Exception as error:
            if _error_code(error) != "ResourceNotFoundException":
                remaining.append(f"Lambda function {function_name} (unverifiable: {_error_code(error)})")

    api_names = {item.get("name") for item in _pages(
        clients["apigateway"], "get_rest_apis", "items", pagination_key="position", limit=500
    )}
    api_key_names = {item.get("name") for item in _pages(
        clients["apigateway"], "get_api_keys", "items", pagination_key="position",
        includeValues=False, nameQuery=config.project_name, limit=500
    )}
    usage_plan_names = {item.get("name") for item in _pages(
        clients["apigateway"], "get_usage_plans", "items", pagination_key="position", limit=500
    )}
    guardrail_names = {item.get("name") for item in _pages(
        clients["bedrock"], "list_guardrails", "guardrails", maxResults=100
    )}
    for endpoint in config.endpoints:
        resource_name = f"{config.project_name}-{endpoint}"
        if resource_name in api_names:
            remaining.append(f"REST API {resource_name}")
        if resource_name in api_key_names:
            remaining.append(f"API key {resource_name}")
        if resource_name in usage_plan_names:
            remaining.append(f"usage plan {resource_name}")
        if resource_name in guardrail_names:
            remaining.append(f"guardrail {resource_name}")

    import_jobs = list(_pages(
        clients["bedrock"], "list_model_import_jobs", "modelImportJobSummaries",
        maxResults=1000, statusEquals="InProgress"
    ))
    for job in import_jobs:
        if job.get("importedModelName") == config.imported_model_name:
            remaining.append(f"in-progress model import {job.get('jobArn', 'unknown')}")

    model_names = {item.get("modelName") for item in _pages(
        clients["bedrock"], "list_imported_models", "modelSummaries", maxResults=100
    )}
    if config.imported_model_name in model_names:
        remaining.append(f"imported model {config.imported_model_name}")

    role_names = [
        f"{config.project_name}-bedrock-import",
        f"{config.project_name}-lifecycle",
        *(f"{config.project_name}-{endpoint}-proxy" for endpoint in config.endpoints),
    ]
    for role_name in role_names:
        try:
            clients["iam"].get_role(RoleName=role_name)
            remaining.append(f"IAM role {role_name}")
        except Exception as error:
            if _error_code(error) != "NoSuchEntity":
                remaining.append(f"IAM role {role_name} (unverifiable: {_error_code(error)})")

    expected_logs = [
        f"/aws/codebuild/{config.project_name}-model-lifecycle",
        *(f"/aws/lambda/{config.project_name}-{endpoint}" for endpoint in config.endpoints),
    ]
    for expected_log in expected_logs:
        log_names = {
            item.get("logGroupName")
            for item in clients["logs"].describe_log_groups(
                logGroupNamePrefix=expected_log
            ).get("logGroups", [])
        }
        if expected_log in log_names:
            remaining.append(f"CloudWatch log group {expected_log}")

    return remaining


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-name", default="bedrock-model-foundry")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument(
        "--model-suffix", default="qwen-2-5-1-5b",
        help="Imported-model suffix from the active Terraform model definition.",
    )
    parser.add_argument(
        "--endpoint", action="append", dest="endpoints",
        help="Endpoint key; repeat for custom configurations.",
    )
    return parser.parse_args()


def main() -> int:
    import boto3

    args = parse_args()
    session = boto3.session.Session(region_name=args.region)
    account_id = session.client("sts").get_caller_identity()["Account"]
    config = AuditConfig(
        project_name=args.project_name,
        region=args.region,
        account_id=account_id,
        endpoints=tuple(args.endpoints or ("pii-mask", "denied-topic")),
        model_suffix=args.model_suffix,
    )
    clients = {name: session.client(service) for name, service in {
        "s3": "s3",
        "codebuild": "codebuild",
        "ssm": "ssm",
        "lambda": "lambda",
        "apigateway": "apigateway",
        "bedrock": "bedrock",
        "iam": "iam",
        "logs": "logs",
    }.items()}
    remaining = audit(config, clients)
    if remaining:
        print("FAIL: teardown audit found resources or incomplete checks:", file=sys.stderr)
        for item in remaining:
            print(f"  - {item}", file=sys.stderr)
        return 1
    print("PASS: no Model Foundry resources found for the requested identity.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
