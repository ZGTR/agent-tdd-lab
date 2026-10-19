"""Install one checksum-pinned Linux/amd64 OpenTofu version for CI/local verification."""

import argparse
import hashlib
import platform
import tempfile
import urllib.request
import zipfile
from pathlib import Path

VERSION = "1.13.1"
SHA256 = "8ccbc6f8ee21d2827715f3c6e08a9b3e0209b1e62057c05067ef117e047c1a80"

parser = argparse.ArgumentParser()
parser.add_argument("--directory", type=Path, required=True)
args = parser.parse_args()
if platform.system() != "Linux" or platform.machine() not in ("x86_64", "amd64"):
    raise SystemExit(
        "This installer is pinned for Linux/amd64; use the official installer elsewhere."
    )
url = f"https://github.com/opentofu/opentofu/releases/download/v{VERSION}/tofu_{VERSION}_linux_amd64.zip"
with tempfile.TemporaryDirectory() as directory:
    archive = Path(directory) / "tofu.zip"
    with urllib.request.urlopen(url, timeout=60) as response:
        archive.write_bytes(response.read())
    if hashlib.sha256(archive.read_bytes()).hexdigest() != SHA256:
        raise SystemExit("OpenTofu archive checksum mismatch")
    args.directory.mkdir(parents=True, exist_ok=True)
    binary = args.directory / "tofu"
    with zipfile.ZipFile(archive) as package:
        binary.write_bytes(package.read("tofu"))
    binary.chmod(0o755)
print(f"Installed checksum-verified OpenTofu {VERSION}")
