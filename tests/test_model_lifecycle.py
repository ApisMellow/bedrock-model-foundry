import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.model_lifecycle import Config, cleanup_model, import_model, validate_model_metadata


MODEL_ID = "Qwen/Qwen2.5-1.5B-Instruct"
REVISION = "775b11afaf83e0dc75bd5abaf90133e47b3ec082"


def write_model(path: Path, *, model_type: str = "qwen2") -> None:
    path.mkdir()
    (path / "config.json").write_text(json.dumps({
        "model_type": model_type,
        "architectures": ["Qwen2ForCausalLM"],
    }))
    (path / "tokenizer_config.json").write_text(json.dumps({"chat_template": "{{ messages }}"}))
    (path / "tokenizer.json").write_text("{}")
    (path / "model.safetensors").write_bytes(b"weights")


def config(tmp_path: Path) -> Config:
    return Config(
        action="import",
        model_id=MODEL_ID,
        model_revision=REVISION,
        model_dir=tmp_path / "model",
        bucket="demo-bucket",
        prefix="models/qwen",
        parameter_name="/demo/model-arn",
        import_role_arn="arn:aws:iam::123456789012:role/import",
        imported_model_name="demo-qwen",
        region="us-east-1",
    )


def test_validation_accepts_complete_qwen2_snapshot(tmp_path):
    model_dir = tmp_path / "model"
    write_model(model_dir)
    validate_model_metadata(model_dir, MODEL_ID)


def test_validation_rejects_unapproved_repository(tmp_path):
    model_dir = tmp_path / "model"
    write_model(model_dir)
    with pytest.raises(ValueError, match="approved repository"):
        validate_model_metadata(model_dir, "another/model")


def test_validation_rejects_wrong_architecture(tmp_path):
    model_dir = tmp_path / "model"
    write_model(model_dir, model_type="llama")
    with pytest.raises(ValueError, match="Qwen2"):
        validate_model_metadata(model_dir, MODEL_ID)


def test_validation_requires_safetensors(tmp_path):
    model_dir = tmp_path / "model"
    write_model(model_dir)
    (model_dir / "model.safetensors").unlink()
    with pytest.raises(ValueError, match="safetensors"):
        validate_model_metadata(model_dir, MODEL_ID)


class FakeS3:
    def __init__(self):
        self.uploaded = []
        self.deleted = []

    def upload_file(self, filename, bucket, key):
        self.uploaded.append((Path(filename).name, bucket, key))

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return SimpleNamespace(paginate=lambda **kwargs: [
            {"Contents": [{"Key": "models/qwen/config.json"}]}
        ])

    def delete_objects(self, **kwargs):
        self.deleted.extend(kwargs["Delete"]["Objects"])


class FakeBedrock:
    def __init__(self, status="Completed"):
        self.status = status
        self.deleted = []

    def create_model_import_job(self, **kwargs):
        self.create_args = kwargs
        return {"jobArn": "arn:aws:bedrock:us-east-1:123:job/demo"}

    def get_model_import_job(self, jobIdentifier):
        if self.status == "Failed":
            return {"status": "Failed", "failureMessage": "invalid model"}
        return {
            "status": "Completed",
            "importedModelArn": "arn:aws:bedrock:us-east-1:123:imported-model/demo",
        }

    def delete_imported_model(self, modelIdentifier):
        self.deleted.append(modelIdentifier)


class FakeSSM:
    def __init__(self):
        self.value = "PENDING"

    def put_parameter(self, **kwargs):
        self.value = kwargs["Value"]

    def get_parameter(self, **kwargs):
        return {"Parameter": {"Value": self.value}}


def test_import_publishes_model_arn_and_uploads_snapshot(tmp_path):
    cfg = config(tmp_path)
    s3, bedrock, ssm = FakeS3(), FakeBedrock(), FakeSSM()

    def download(repo_id, revision, local_dir):
        assert (repo_id, revision) == (MODEL_ID, REVISION)
        write_model(Path(local_dir))
        return local_dir

    arn = import_model(
        cfg,
        s3=s3,
        bedrock=bedrock,
        ssm=ssm,
        downloader=download,
        sleep=lambda _: None,
    )

    assert arn.endswith("imported-model/demo")
    assert ssm.value == arn
    assert {item[0] for item in s3.uploaded} == {
        "config.json", "tokenizer_config.json", "tokenizer.json", "model.safetensors"
    }
    assert bedrock.create_args["modelDataSource"]["s3DataSource"]["s3Uri"] == "s3://demo-bucket/models/qwen/"


def test_import_failure_does_not_publish_arn(tmp_path):
    cfg = config(tmp_path)
    s3, bedrock, ssm = FakeS3(), FakeBedrock(status="Failed"), FakeSSM()

    def download(repo_id, revision, local_dir):
        write_model(Path(local_dir))
        return local_dir

    with pytest.raises(RuntimeError, match="invalid model"):
        import_model(cfg, s3=s3, bedrock=bedrock, ssm=ssm, downloader=download, sleep=lambda _: None)
    assert ssm.value == "PENDING"


def test_cleanup_is_idempotent_and_clears_remote_state(tmp_path):
    cfg = config(tmp_path)
    s3, bedrock, ssm = FakeS3(), FakeBedrock(), FakeSSM()
    ssm.value = "arn:aws:bedrock:us-east-1:123:imported-model/demo"

    cleanup_model(cfg, s3=s3, bedrock=bedrock, ssm=ssm)
    cleanup_model(cfg, s3=s3, bedrock=bedrock, ssm=ssm)

    assert bedrock.deleted == ["arn:aws:bedrock:us-east-1:123:imported-model/demo"]
    assert ssm.value == "PENDING"
    assert {"Key": "models/qwen/config.json"} in s3.deleted


def test_validation_requires_chat_template(tmp_path):
    model_dir = tmp_path / "model"
    write_model(model_dir)
    (model_dir / "tokenizer_config.json").write_text("{}")
    with pytest.raises(ValueError, match="chat_template"):
        validate_model_metadata(model_dir, MODEL_ID)
