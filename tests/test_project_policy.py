import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_customer_diagram_is_valid_accessible_svg():
    document = ET.parse(ROOT / "docs/architecture/bedrock-model-foundry.svg")
    root = document.getroot()
    namespace = {"svg": "http://www.w3.org/2000/svg"}
    title = root.find("svg:title", namespace)
    description = root.find("svg:desc", namespace)
    assert title is not None and title.text
    assert description is not None and description.text


def test_shell_entrypoints_parse():
    for path in sorted((ROOT / "scripts").glob("*.sh")):
        subprocess.run(["bash", "-n", str(path)], check=True)


def test_python_entrypoints_compile():
    subprocess.run(
        ["python3", "-m", "compileall", "-q", str(ROOT / "src"), str(ROOT / "scripts")],
        check=True,
    )


def test_reviewed_model_and_two_default_endpoints_are_pinned():
    variables = (ROOT / "terraform/variables.tf").read_text()
    assert 'Qwen/Qwen2.5-1.5B-Instruct' in variables
    assert '775b11afaf83e0dc75bd5abaf90133e47b3ec082' in variables
    assert variables.count('pii-mask = {') == 1
    assert variables.count('denied-topic = {') == 1


def test_model_download_is_delegated_to_remote_codebuild():
    lifecycle = (ROOT / "terraform/lifecycle.tf").read_text()
    buildspec = (ROOT / "terraform/buildspec.yml").read_text()
    assert 'aws_codebuild_project' in lifecycle
    assert 'python /tmp/model_lifecycle.py' in buildspec
    assert 'model_dir=Path(os.environ.get("MODEL_DIR", "/tmp/model"))' in (
        ROOT / "src/model_lifecycle.py"
    ).read_text()


def test_cleanup_anchor_precedes_fallible_import():
    lifecycle = (ROOT / "terraform/lifecycle.tf").read_text()
    assert 'terraform_data" "model_cleanup' in lifecycle
    assert 'depends_on = [terraform_data.model_cleanup]' in lifecycle


def test_implementation_has_no_unfinished_markers():
    targets = [ROOT / "src", ROOT / "scripts", ROOT / "terraform"]
    source = "\n".join(
        path.read_text(errors="ignore")
        for target in targets
        for path in target.rglob("*")
        if path.is_file() and ".terraform" not in path.parts and path.suffix != ".zip"
    )
    assert "TO" + "DO" not in source
    assert "FIX" + "ME" not in source


def test_repository_policy_scan_passes():
    subprocess.run(["python3", str(ROOT / "scripts/repository-policy.py")], check=True)
