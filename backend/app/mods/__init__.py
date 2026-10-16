"""Mod とフック(ADR-0051)。

公開する契約のモジュール(`app/mods/api.py`)は、Python の Mod から `gakei_mod` の名前で
import できるようにする(ここを読み込んだときに `sys.modules` に登録する)。GAKEI のパッケージの
外にもう1つトップレベルのパッケージを置かずに済み、Docker のイメージ(`backend/app` だけを
入れる)とも揃う。
"""

from __future__ import annotations

import sys

from app.mods import api as _api

sys.modules.setdefault("gakei_mod", _api)
