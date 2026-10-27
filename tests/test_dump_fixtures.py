"""接口契约导出脚本：能产出样例和 manifest，且样例里没有真实令牌。"""
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.")


def test_dump_fixtures(tmp_path):
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "dump_fixtures.py"), "--out", str(tmp_path)],
                       capture_output=True, text=True, cwd=ROOT, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["api_version"] == 1
    files = {f["file"]: f for f in manifest["files"]}
    assert len(files) >= 100 and all((tmp_path / n).exists() for n in files)
    assert (tmp_path / "openapi.json").exists()
    for status in (401, 403, 404, 409, 422, 429, 400):
        assert any(f["status"] == status for f in files.values()), status
    assert any("sparse" in n for n in files) and any("in_progress" in n for n in files)
    for p in tmp_path.iterdir():
        assert not JWT_RE.search(p.read_text(encoding="utf-8")), p.name
    login = json.loads((tmp_path / "auth_login__ios.json").read_text(encoding="utf-8"))
    assert login["token"] == "FIXTURE-TOKEN" and login["expires_at"].endswith("Z")
    assert "token" not in json.loads((tmp_path / "auth_login__web_no_token.json").read_text(encoding="utf-8"))
