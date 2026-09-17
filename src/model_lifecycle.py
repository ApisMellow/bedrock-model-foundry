"""Cloud-only Hugging Face to Bedrock Custom Model Import lifecycle."""

from __future__ import annotations

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
        relative = path.relative_to(model_dir).as_posix()
        s3.upload_file(str(path), bucket, f"{prefix}/{relative}")


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

    if config.model_dir.exists():
        shutil.rmtree(config.model_dir)
    downloader(config.model_id, config.model_revision, str(config.model_dir))
    validate_model_metadata(config.model_dir, config.model_id)
    _upload_directory(config.model_dir, config.bucket, config.prefix, s3)

    started = bedrock.create_model_import_job(
        jobName=f"{config.imported_model_name}-import-{int(time.time())}",
        importedModelName=config.imported_model_name,
        roleArn=config.import_role_arn,
        modelDataSource={
            "s3DataSource": {
                "s3Uri": f"s3://{config.bucket}/{config.prefix}/",
            }
        },
        clientRequestToken=config.model_revision,
    )
    job_arn = started["jobArn"]

    while True:
        status = bedrock.get_model_import_job(jobIdentifier=job_arn)
        state = status["status"]
        if state in TERMINAL_SUCCESS:
            model_arn = status["importedModelArn"]
            ssm.put_parameter(
                Name=config.parameter_name,
                Value=model_arn,
                Type="String",
                Overwrite=True,
            )
            return model_arn
        if state in TERMINAL_FAILURE:
            raise RuntimeError(status.get("failureMessage", "Bedrock model import failed"))
        sleep(30)


def cleanup_model(config: Config, *, s3: Any, bedrock: Any, ssm: Any) -> None:
    model_arn = _current_parameter(ssm, config.parameter_name)
    if model_arn:
        try:
            bedrock.delete_imported_model(modelIdentifier=model_arn)
        except Exception as error:
            if _error_code(error) not in {"ResourceNotFoundException", "ValidationException"}:
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
