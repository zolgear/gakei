"""`app.main` を import しただけでは副作用が起きないことのテスト。

以前はモジュールの末尾で `app = create_app()` を実行していたため、テストの収集で import する
だけで既定の `DATA_DIR`(リポジトリ直下の `data/`)の `secrets.json` に `auth_secret` を
書き込み、Windows では pytest-xdist のワーカー同士の書き込みがぶつかって収集が失敗した。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]


def test_importing_app_main_does_not_write_data_dir(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    env = {**os.environ, "DATA_DIR": str(data_dir), "AUTH_SECRET": ""}
    env.pop("AUTH_MODE", None)

    subprocess.run(
        [sys.executable, "-c", "import app.main"],
        cwd=BACKEND_DIR,
        env=env,
        check=True,
    )

    assert not data_dir.exists() or not any(data_dir.iterdir())
