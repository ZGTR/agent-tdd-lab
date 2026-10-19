"""Private atomic artifacts. Reports contain grades/metadata, never raw task/answer text."""

import html
import json
import os
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


def prepare_reports(path: Path) -> None:
    """Reject unusable destinations before inference; final writes still need error handling."""
    if path.suffix != ".json":
        raise ValueError("report path must end in .json")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    for extension in (".json", ".html", ".xml"):
        target = path.with_suffix(extension)
        if target.exists() and not target.is_file():
            raise ValueError("report destination is not a file")
    descriptor, name = tempfile.mkstemp(prefix=".agent-tdd-check-", dir=path.parent)
    os.close(descriptor)
    Path(name).unlink()


def _write(path: Path, content: str) -> None:
    descriptor, name = tempfile.mkstemp(prefix=".agent-tdd-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as target:
            target.write(content)
            target.flush()
            os.fsync(target.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def write_reports(path: Path, report: dict[str, Any]) -> None:
    if path.suffix != ".json":
        raise ValueError("report path must end in .json")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    rows = report["runs"]
    table = "".join(
        "<tr>"
        + "".join(
            f"<td>{html.escape(str(value))}</td>"
            for value in (
                r["case_id"],
                r["repeat"],
                r["status"],
                r["quality_passed"],
                ", ".join(r["failures"] + r["policy_violations"]),
                r["elapsed_seconds"],
            )
        )
        + "</tr>"
        for r in rows
    )
    page = (
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta http-equiv="Content-Security-Policy" content="default-src &#39;none&#39;">'
        "<title>Agent TDD evaluation</title><body><h1>Agent TDD evaluation</h1>"
        f"<p>Suite: {html.escape(report['suite'])} | Passed: {report['passed']}</p>"
        f"<pre>{html.escape(json.dumps(report['summary'], indent=2))}</pre>"
        f"<p>Gate failures: {html.escape(', '.join(report['gate_failures']))}</p>"
        "<table><caption>Every evaluated attempt</caption><thead><tr>"
        "<th>Case</th><th>Repeat</th><th>Status</th><th>Quality passed</th>"
        f"<th>Failures</th><th>Seconds</th></tr></thead><tbody>{table}</tbody></table>"
        "</body></html>"
    )
    root = ET.Element("testsuite", name=report["suite"], tests=str(len(rows)))
    failures = 0
    for row in rows:
        test = ET.SubElement(
            root,
            "testcase",
            name=f"{row['case_id']}#{row['repeat']}",
            time=str(row["elapsed_seconds"]),
        )
        if not row["quality_passed"] or row["policy_violations"]:
            failures += 1
            ET.SubElement(test, "failure", message="evaluation failed").text = ", ".join(
                row["failures"] + row["policy_violations"]
            )
    # A suite-wide budget/regression failure must also fail JUnit consumers.
    if not report["passed"]:
        gate = ET.SubElement(root, "testcase", name="release-gates")
        ET.SubElement(gate, "failure", message=", ".join(report["gate_failures"]))
        failures += 1
        root.set("tests", str(len(rows) + 1))
    root.set("failures", str(failures))
    _write(path.with_suffix(".html"), page)
    _write(path.with_suffix(".xml"), ET.tostring(root, encoding="unicode"))
    # Write JSON last as the completion marker for the artifact bundle.
    _write(path, json.dumps(report, indent=2, allow_nan=False) + "\n")
