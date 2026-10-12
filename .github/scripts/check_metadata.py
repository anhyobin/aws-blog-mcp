"""Check that release metadata agrees across the repository and the built distributions.

Checks ``pyproject.toml``, ``server.json`` (MCP Registry), the ``mcp-name`` marker in
``README.md`` and the ``CHANGELOG.md`` heading. Optionally also checks a release tag and the
wheel/sdist metadata in a ``dist`` directory.

Usage::

    uv run --no-project .github/scripts/check_metadata.py
    uv run --no-project .github/scripts/check_metadata.py --dist dist
    uv run --no-project .github/scripts/check_metadata.py --tag v0.1.0 --release --dist dist

Only the standard library is used, so it runs without installing the project.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tarfile
import tomllib
import zipfile
from email.message import Message
from email.parser import Parser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SDIST_REQUIRED = ("PKG-INFO", "LICENSE", "NOTICE", "README.md", "server.json", "CHANGELOG.md")


def main(argv: list[str] | None = None) -> int:
    """Run all checks and return the process exit code."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--tag", help="release tag that must equal 'v' + the project version")
    parser.add_argument(
        "--release",
        action="store_true",
        help="also require a dated (not 'Unreleased') CHANGELOG entry for the version",
    )
    parser.add_argument("--dist", type=Path, help="directory with the built wheel and sdist")
    args = parser.parse_args(argv)

    errors: list[str] = []
    project = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]
    name: str = project["name"]
    version: str = project["version"]
    server = json.loads((ROOT / "server.json").read_text("utf-8"))
    marker = f"mcp-name: {server['name']}"

    errors += _check_server_json(server, name, version)
    if not _has_marker((ROOT / "README.md").read_text("utf-8"), marker):
        errors.append(f"README.md does not contain the registry marker {marker!r}.")
    errors += _check_changelog(version, release=args.release)
    if args.tag is not None and args.tag != f"v{version}":
        errors.append(f"Tag {args.tag!r} does not match the project version (expected v{version}).")
    if args.dist is not None:
        errors += _check_dist(args.dist, name, version, project["license"], marker)

    for error in errors:
        print(f"::error::{error}")
    if errors:
        return 1
    print(f"OK: {name} {version} ({server['name']}) metadata is consistent.")
    return 0


def _check_server_json(server: dict, name: str, version: str) -> list[str]:
    errors: list[str] = []
    if server.get("version") != version:
        errors.append(f"server.json version {server.get('version')!r} != pyproject {version!r}.")
    packages = [p for p in server.get("packages", []) if p.get("registryType") == "pypi"]
    if not packages:
        errors.append("server.json has no PyPI package entry.")
    for package in packages:
        if package.get("identifier") != name:
            errors.append(
                f"server.json package identifier {package.get('identifier')!r} != {name!r}."
            )
        if package.get("version") != version:
            errors.append(
                f"server.json package version {package.get('version')!r} != pyproject {version!r}."
            )
    if len(server.get("description", "")) > 100:
        errors.append("server.json description is longer than 100 characters.")
    return errors


def _has_marker(text: str, marker: str) -> bool:
    # The registry requires a boundary after the name: whitespace, an HTML tag or "-->".
    return re.search(re.escape(marker) + r"(?=\s|<|-->|$)", text) is not None


def _check_changelog(version: str, *, release: bool) -> list[str]:
    changelog = (ROOT / "CHANGELOG.md").read_text("utf-8")
    heading = re.search(rf"^## \[{re.escape(version)}\](.*)$", changelog, re.MULTILINE)
    if heading is None:
        return [f"CHANGELOG.md has no '## [{version}]' section."]
    if release and not re.search(r"\d{4}-\d{2}-\d{2}", heading.group(1)):
        return [
            f"CHANGELOG.md section for {version} is not dated "
            f"(found {heading.group(0)!r}; expected '## [{version}] - YYYY-MM-DD')."
        ]
    return []


def _check_dist(dist: Path, name: str, version: str, license_expr: str, marker: str) -> list[str]:
    errors: list[str] = []
    stem = f"{name.replace('-', '_')}-{version}"
    wheels = sorted(dist.glob(f"{stem}-*.whl"))
    sdists = sorted(dist.glob(f"{stem}.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        return [f"Expected one wheel and one sdist for {stem} in {dist}, found {wheels + sdists}."]

    with zipfile.ZipFile(wheels[0]) as wheel:
        metadata_name = f"{stem}.dist-info/METADATA"
        if metadata_name not in wheel.namelist():
            return [f"{wheels[0].name} has no {metadata_name}."]
        wheel_meta = Parser().parsestr(wheel.read(metadata_name).decode("utf-8"))
    errors += _check_core_metadata(wheels[0].name, wheel_meta, name, version, license_expr, marker)

    with tarfile.open(sdists[0], "r:gz") as sdist:
        members = {m.name for m in sdist.getmembers()}
        missing = [f for f in SDIST_REQUIRED if f"{stem}/{f}" not in members]
        if missing:
            errors.append(f"{sdists[0].name} is missing {', '.join(missing)}.")
        pkg_info = sdist.extractfile(f"{stem}/PKG-INFO") if not missing else None
        if pkg_info is not None:
            sdist_meta = Parser().parsestr(pkg_info.read().decode("utf-8"))
            errors += _check_core_metadata(
                sdists[0].name, sdist_meta, name, version, license_expr, marker
            )
    return errors


def _check_core_metadata(
    label: str, meta: Message, name: str, version: str, license_expr: str, marker: str
) -> list[str]:
    errors: list[str] = []
    if meta.get("Name") != name or meta.get("Version") != version:
        errors.append(f"{label}: Name/Version is {meta.get('Name')} {meta.get('Version')}.")
    if meta.get("License-Expression") != license_expr:
        errors.append(f"{label}: License-Expression is {meta.get('License-Expression')!r}.")
    license_files = set(meta.get_all("License-File") or [])
    if not {"LICENSE", "NOTICE"} <= license_files:
        errors.append(f"{label}: License-File entries are {sorted(license_files)}.")
    if meta.get("Description-Content-Type") != "text/markdown":
        content_type = meta.get("Description-Content-Type")
        errors.append(f"{label}: Description-Content-Type is {content_type!r}.")
    body = meta.get_payload()
    if not isinstance(body, str) or not _has_marker(body, marker):
        errors.append(f"{label}: the long description does not contain {marker!r}.")
    return errors


if __name__ == "__main__":
    sys.exit(main())
