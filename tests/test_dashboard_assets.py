import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"

SCRIPT_VERSION_RE = re.compile(r'src="app\.js\?v=([^\"]+)"')
IMPORT_RE = re.compile(r'from\s+["\']([^"\']+\.js)(?:\?v=([^"\']+))?["\']')


def test_dashboard_module_graph_uses_one_cache_version():
    index = (WEB / "index.html").read_text()
    match = SCRIPT_VERSION_RE.search(index)
    assert match, "index.html must cache-bust the dashboard entry module"
    build_version = match.group(1)

    module_files = [WEB / "app.js", *sorted((WEB / "js").rglob("*.js"))]
    for module_file in module_files:
        source = module_file.read_text()
        for import_path, import_version in IMPORT_RE.findall(source):
            assert import_version == build_version, (
                f"{module_file.relative_to(ROOT)} imports {import_path} without the "
                f"current dashboard cache version {build_version}"
            )
            target = (module_file.parent / import_path).resolve()
            assert target.is_relative_to(WEB.resolve())
            assert target.is_file(), f"missing dashboard module: {target}"
