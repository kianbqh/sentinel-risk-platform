#!/usr/bin/env python3
"""Download fixed official Linux tools to a task directory, without system install."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import shutil
import tarfile
from datetime import datetime, timezone
import urllib.request

TOOLS = {
    "helm": ("3.19.0", "https://get.helm.sh/helm-v3.19.0-linux-amd64.tar.gz", "linux-amd64/helm"),
    "kubeconform": ("0.7.0", "https://github.com/yannh/kubeconform/releases/download/v0.7.0/kubeconform-linux-amd64.tar.gz", "kubeconform"),
    "kind": ("0.30.0", "https://github.com/kubernetes-sigs/kind/releases/download/v0.30.0/kind-linux-amd64", None),
    "kubectl": ("1.34.0", "https://dl.k8s.io/release/v1.34.0/bin/linux/amd64/kubectl", None),
    "promtool": ("3.7.3", "https://github.com/prometheus/prometheus/releases/download/v3.7.3/prometheus-3.7.3.linux-amd64.tar.gz", "prometheus-3.7.3.linux-amd64/promtool"),
    "shellcheck": ("0.11.0", "https://github.com/koalaman/shellcheck/releases/download/v0.11.0/shellcheck-v0.11.0.linux.x86_64.tar.xz", "shellcheck-v0.11.0/shellcheck"),
    "bats": ("1.12.0", "https://github.com/bats-core/bats-core/archive/refs/tags/v1.12.0.tar.gz", "bats-core-1.12.0"),
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--tools", nargs="+", choices=TOOLS, default=list(TOOLS))
    args = parser.parse_args()
    destination = args.directory.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    downloads = destination / "downloads"
    downloads.mkdir(exist_ok=True)
    manifest_path = destination / "downloads.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    for name in args.tools:
        version, url, member = TOOLS[name]
        archive = downloads / (name + "-" + url.rsplit("/", 1)[-1])
        if not archive.exists():
            with urllib.request.urlopen(url, timeout=60) as response, archive.with_suffix(".partial").open("wb") as stream:
                shutil.copyfileobj(response, stream)
            archive.with_suffix(".partial").replace(archive)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        if name in manifest and manifest[name]["download_sha256"] != digest:
            raise RuntimeError(f"Cached tool archive changed: {name}")
        if name == "bats":
            with tarfile.open(archive) as bundle:
                for item in bundle.getmembers():
                    # Only regular files under the pinned prefix, never archive links.
                    if item.isfile() and item.name.startswith(member + "/"):
                        relative = Path(item.name).relative_to(member)
                        path = destination / "bats-core" / relative
                        if ".." in relative.parts:
                            raise ValueError("Unsafe archive member")
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(bundle.extractfile(item).read())
                        if item.mode & 0o111:
                            path.chmod(0o755)
            binary = destination / "bats"
            binary.write_text('#!/usr/bin/env bash\nexec "' + str(destination / "bats-core/bin/bats") + '" "$@"\n')
        elif member:
            with tarfile.open(archive) as bundle:
                binary = destination / name
                binary.write_bytes(bundle.extractfile(member).read())
        else:
            binary = destination / name
            shutil.copyfile(archive, binary)
        binary.chmod(0o755)
        manifest[name] = {"version_requested": version, "url": url, "download_sha256": digest,
                          "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
                          "download_bytes": archive.stat().st_size, "timestamp_utc": datetime.now(timezone.utc).isoformat()}
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
        print(f"{name}: {version}, sha256 {digest}", flush=True)


if __name__ == "__main__":
    main()
