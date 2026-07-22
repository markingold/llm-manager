import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_dashboard_escape_regression_suite():
    result = subprocess.run(
        ["node", "--test", "tests/js/ui-core.test.mjs"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_stored_dashboard_fields_are_escaped_at_render_sites():
    governance = (ROOT / "web/js/domains/governance.js").read_text()
    evaluation = (ROOT / "web/js/domains/evaluation.js").read_text()
    assert "${escapeHtml(e.reason || \"-\")}" in governance
    assert "escapeHtml(r.error ?" in evaluation
    assert "escapeHtml((String(r.output_text" in evaluation
