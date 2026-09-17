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
