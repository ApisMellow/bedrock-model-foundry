"""Cloud-only Hugging Face to Bedrock Custom Model Import lifecycle."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional


APPROVED_MODEL_ID = "Qwen/Qwen2.5-1.5B-Instruct"
APPROVED_MODEL_TYPE = "qwen2"
APPROVED_ARCHITECTURE = "Qwen2ForCausalLM"
TERMINAL_SUCCESS = {"Completed"}
TERMINAL_FAILURE = {"Failed"}


@dataclass(frozen=True)
class Config:
    action: str
    model_id: str
    model_revision: str
    model_dir: Path
    bucket: str
    prefix: str
    parameter_name: str
    import_role_arn: str
    imported_model_name: str
    region: str
    deployment_id: str = "local-deployment"

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            action=os.environ.get("ACTION", "import"),
            model_id=os.environ["HF_MODEL_ID"],
            model_revision=os.environ["MODEL_REVISION"],
            model_dir=Path(os.environ.get("MODEL_DIR", "/tmp/model")),
            bucket=os.environ["S3_BUCKET"],
            prefix=os.environ.get("S3_PREFIX", "models/default").strip("/"),
            parameter_name=os.environ["SSM_PARAMETER_NAME"],
            import_role_arn=os.environ["IMPORT_ROLE_ARN"],
            imported_model_name=os.environ["IMPORTED_MODEL_NAME"],
            region=os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-east-1")),
            deployment_id=os.environ.get("MODEL_DEPLOYMENT_ID", "manual-deployment"),
        )


def validate_model_metadata(model_dir: Path, expected_repo: str) -> None:
    if expected_repo != APPROVED_MODEL_ID:
        raise ValueError("model is not the approved repository")

    required = ("config.json", "tokenizer_config.json", "tokenizer.json")
    missing = [name for name in required if not (model_dir / name).is_file()]
    if missing:
        raise ValueError("missing required model files: " + ", ".join(missing))
    if not any(model_dir.glob("*.safetensors")):
        raise ValueError("model snapshot must contain safetensors weights")

    tokenizer_metadata = json.loads((model_dir / "tokenizer_config.json").read_text())
    if not tokenizer_metadata.get("chat_template"):
        raise ValueError("tokenizer_config.json must contain chat_template")

    metadata = json.loads((model_dir / "config.json").read_text())
    architectures = metadata.get("architectures", [])
    if (
        metadata.get("model_type") != APPROVED_MODEL_TYPE
        or APPROVED_ARCHITECTURE not in architectures
    ):
        raise ValueError("model must use the approved Qwen2 architecture")


def _default_downloader(repo_id: str, revision: str, local_dir: str) -> str:
    from huggingface_hub import snapshot_download

    return snapshot_download(
        repo_id=repo_id,
        revision=revision,
        local_dir=local_dir,
    )


def _error_code(error: Exception) -> str:
    response = getattr(error, "response", {})
    return response.get("Error", {}).get("Code", "")


def _current_parameter(ssm: Any, name: str) -> Optional[str]:
    try:
        value = ssm.get_parameter(Name=name)["Parameter"]["Value"]
    except Exception as error:
        if _error_code(error) == "ParameterNotFound":
            return None
        raise
    return value if value.startswith("arn:aws:bedrock:") else None


def _upload_directory(model_dir: Path, bucket: str, prefix: str, s3: Any) -> None:
    for path in sorted(item for item in model_dir.rglob("*") if item.is_file()):
        relative_path = path.relative_to(model_dir)
        if ".cache" in relative_path.parts:
            continue
        relative = relative_path.as_posix()
        s3.upload_file(str(path), bucket, f"{prefix}/{relative}")


def _stable_suffix(config: Config, generation: str = "initial") -> str:
    identity = "|".join((
        config.region,
        config.bucket,
        config.prefix,
        config.parameter_name,
        config.imported_model_name,
        config.model_revision,
        config.deployment_id,
        generation,
    ))
    return hashlib.sha256(identity.encode()).hexdigest()[:32]


def _job_prefix(config: Config) -> str:
    suffix = _stable_suffix(config)[:10]
    stem = config.imported_model_name[: 54 - len(suffix) - 8].rstrip("-")
    return f"{stem}-import-{suffix}"


def _job_name(config: Config, generation: str) -> str:
    prefix = _job_prefix(config)
    if generation == "initial":
        return prefix
    return f"{prefix}-{_stable_suffix(config, generation)[:8]}"


def _model_import_jobs(bedrock: Any, model_name: str) -> list[dict]:
    jobs = []
    next_token = None
    while True:
        request = {"maxResults": 1000}
        if next_token:
            request["nextToken"] = next_token
        response = bedrock.list_model_import_jobs(**request)
        jobs.extend(
            job for job in response.get("modelImportJobSummaries", [])
            if job.get("importedModelName") == model_name
        )
        next_token = response.get("nextToken")
        if not next_token:
            return jobs


def _deployment_jobs(config: Config, bedrock: Any) -> list[dict]:
    prefix = _job_prefix(config)
    return [
        job for job in _model_import_jobs(bedrock, config.imported_model_name)
        if job.get("jobName") == prefix
        or str(job.get("jobName", "")).startswith(f"{prefix}-")
    ]


def _model_exists(bedrock: Any, model_arn: str) -> bool:
    try:
        bedrock.get_imported_model(modelIdentifier=model_arn)
        return True
    except Exception as error:
        if _error_code(error) == "ResourceNotFoundException":
            return False
        raise


def _poll_import_job(
    bedrock: Any, job_arn: str, sleep: Callable[[float], None]
) -> str:
    while True:
        status = bedrock.get_model_import_job(jobIdentifier=job_arn)
        state = status["status"]
        if state in TERMINAL_SUCCESS:
            return status["importedModelArn"]
        if state in TERMINAL_FAILURE:
            raise RuntimeError(status.get("failureMessage", "Bedrock model import failed"))
        sleep(30)


def _publish_model(ssm: Any, parameter_name: str, model_arn: str) -> None:
    ssm.put_parameter(
        Name=parameter_name,
        Value=model_arn,
        Type="String",
        Overwrite=True,
    )


def import_model(
    config: Config,
    *,
    s3: Any,
    bedrock: Any,
    ssm: Any,
    downloader: Callable[[str, str, str], str] = _default_downloader,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    existing = _current_parameter(ssm, config.parameter_name)
    if existing:
        return existing

    deployment_jobs = _deployment_jobs(config, bedrock)
    in_progress = [job for job in deployment_jobs if job.get("status") == "InProgress"]
    for job in in_progress:
        model_arn = _poll_import_job(bedrock, job["jobArn"], sleep)
        try:
            _publish_model(ssm, config.parameter_name, model_arn)
        except Exception:
            bedrock.delete_imported_model(modelIdentifier=model_arn)
            raise
        return model_arn

    for job in reversed(deployment_jobs):
        model_arn = job.get("importedModelArn") if job.get("status") == "Completed" else None
        if model_arn and _model_exists(bedrock, model_arn):
            _publish_model(ssm, config.parameter_name, model_arn)
            return model_arn

    terminal_identity = "|".join(sorted(
        f"{job.get('jobArn', '')}:{job.get('status', '')}" for job in deployment_jobs
    ))
    generation = (
        hashlib.sha256(terminal_identity.encode()).hexdigest()[:12]
        if terminal_identity else "initial"
    )

    if config.model_dir.exists():
        shutil.rmtree(config.model_dir)
    downloader(config.model_id, config.model_revision, str(config.model_dir))
    validate_model_metadata(config.model_dir, config.model_id)
    _upload_directory(config.model_dir, config.bucket, config.prefix, s3)

    started = bedrock.create_model_import_job(
        jobName=_job_name(config, generation),
        importedModelName=config.imported_model_name,
        roleArn=config.import_role_arn,
        modelDataSource={
            "s3DataSource": {
                "s3Uri": f"s3://{config.bucket}/{config.prefix}/",
            }
        },
        clientRequestToken=_stable_suffix(config, generation),
    )
    model_arn = _poll_import_job(bedrock, started["jobArn"], sleep)
    try:
        _publish_model(ssm, config.parameter_name, model_arn)
    except Exception:
        bedrock.delete_imported_model(modelIdentifier=model_arn)
        raise
    return model_arn


def _model_arns_by_name(bedrock: Any, model_name: str) -> list[str]:
    model_arns = []
    next_token = None
    while True:
        request = {"maxResults": 100}
        if next_token:
            request["nextToken"] = next_token
        response = bedrock.list_imported_models(**request)
        model_arns.extend(
            summary["modelArn"]
            for summary in response.get("modelSummaries", [])
            if summary.get("modelName") == model_name
        )
        next_token = response.get("nextToken")
        if not next_token:
            return model_arns


def _wait_for_matching_imports(
    config: Config,
    bedrock: Any,
    sleep: Callable[[float], None],
    max_attempts: int = 330,
) -> list[dict]:
    for attempt in range(max_attempts):
        jobs = _model_import_jobs(bedrock, config.imported_model_name)
        if not any(job.get("status") == "InProgress" for job in jobs):
            return jobs
        if attempt + 1 < max_attempts:
            sleep(30)
    raise RuntimeError(
        f"timed out waiting for imports of {config.imported_model_name} to finish"
    )


def cleanup_model(
    config: Config,
    *,
    s3: Any,
    bedrock: Any,
    ssm: Any,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    jobs = _wait_for_matching_imports(config, bedrock, sleep)
    model_arn = _current_parameter(ssm, config.parameter_name)
    if model_arn:
        model_arns = [model_arn]
    else:
        model_arns = [
            job["importedModelArn"]
            for job in jobs
            if job.get("status") == "Completed" and job.get("importedModelArn")
        ]
        model_arns.extend(_model_arns_by_name(bedrock, config.imported_model_name))

    for candidate in dict.fromkeys(model_arns):
        try:
            bedrock.delete_imported_model(modelIdentifier=candidate)
        except Exception as error:
            if _error_code(error) != "ResourceNotFoundException":
                raise

    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=config.bucket, Prefix=f"{config.prefix}/"):
        objects = [{"Key": item["Key"]} for item in page.get("Contents", [])]
        if objects:
            s3.delete_objects(Bucket=config.bucket, Delete={"Objects": objects, "Quiet": True})

    ssm.put_parameter(
        Name=config.parameter_name,
        Value="PENDING",
        Type="String",
        Overwrite=True,
    )


def main() -> int:
    import boto3

    config = Config.from_env()
    session = boto3.session.Session(region_name=config.region)
    clients = {
        "s3": session.client("s3"),
        "bedrock": session.client("bedrock"),
        "ssm": session.client("ssm"),
    }
    if config.action == "import":
        model_arn = import_model(config, **clients)
        print(json.dumps({"status": "imported", "model_arn": model_arn}))
        return 0
    if config.action == "cleanup":
        cleanup_model(config, **clients)
        print(json.dumps({"status": "cleaned"}))
        return 0
    raise ValueError(f"unsupported ACTION: {config.action}")


if __name__ == "__main__":
    raise SystemExit(main())
