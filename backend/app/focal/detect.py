"""イラストの顔の位置を求め、サムネイルの焦点にする(ADR-0043 2章)。

opencv の LBP カスケード lbpcascade_animeface(nagadomi、MIT)で、アニメ調の顔を探す。
カスケードの XML は `data/` に同梱し、実行時にダウンロードしない(取得元と sha256 は下の定数)。

手順: 長辺 320px に縮小 → グレースケール → `equalizeHist` → `detectMultiScale` → いちばん
大きい顔の中心を、画像の幅・高さに対する 0〜1 の位置にする。`y` は顔の高さの
`FOCAL_Y_SHIFT` 倍だけ上に寄せる(枠の上端で髪が切れにくくするため)。見つからなければ None。

精度は求めない(実写・横顔・小さい顔は見逃しやすい)。検出の手順や定数を変えたら
`ALGORITHM_VERSION` を上げる。保存済みの古い版は、埋め戻しのツールが作り直す。
"""

from __future__ import annotations

import hashlib
import logging
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from app.embedding.base import composite_on_white

logger = logging.getLogger(__name__)

# 検出の版。手順や定数を変えたら 1 つ上げる(`asset_focal_point.version`)。
ALGORITHM_VERSION = 1

# `asset_focal_point.method` の値。
METHOD_ANIMEFACE_LBP = "animeface-lbp"
METHOD_NONE = "none"

# 同梱のカスケード。https://github.com/nagadomi/lbpcascade_animeface の
# コミット 4433ab1ae1166ea75acfe99eb0f18709dac329a0 の lbpcascade_animeface.xml。
CASCADE_PATH = Path(__file__).parent / "data" / "lbpcascade_animeface.xml"
CASCADE_SHA256 = "9376d30ac38db6bda2a68b88b3b76bbd7e6aa33af47f7f5c76bc88ca75f1ce30"
CASCADE_LICENSE_PATH = Path(__file__).parent / "data" / "LICENSE-lbpcascade_animeface.txt"
CASCADE_SOURCE_URL = "https://github.com/nagadomi/lbpcascade_animeface"
CASCADE_COMMIT = "4433ab1ae1166ea75acfe99eb0f18709dac329a0"

# 検出に使う縮小画像の長辺。元がこれより小さければ拡大しない。
DETECT_LONG_EDGE = 320
SCALE_FACTOR = 1.1
MIN_NEIGHBORS = 5
MIN_SIZE = (24, 24)
# 焦点の y を、顔の中心から顔の高さの何倍だけ上に寄せるか。
FOCAL_Y_SHIFT = 0.25


@dataclass(frozen=True)
class Face:
    """縮小画像の中の顔の矩形(画素)。"""

    left: int
    top: int
    width: int
    height: int


@dataclass(frozen=True)
class FocalPoint:
    """画像の幅・高さに対する 0〜1 の位置。"""

    x: float
    y: float


# 検出の鍵(`detectMultiScale` を同時に呼ばない)と、カスケードを読む鍵。
_lock = threading.Lock()
_load_lock = threading.Lock()
_cascade = None
_cascade_failed = False


def _load_cascade():  # noqa: ANN202
    """カスケードをプロセス内で1回だけ読む。読めなければ以後も None(検出は常に焦点なし)。

    opencv はファイルのパスに ASCII 以外の文字があると Windows で開けないことがあるので、
    パスは渡さず、Python で読んだ中身をメモリから読み込ませる。
    """
    global _cascade, _cascade_failed
    with _load_lock:
        if _cascade is not None or _cascade_failed:
            return _cascade
        try:
            import cv2

            data = CASCADE_PATH.read_bytes()
            if hashlib.sha256(data).hexdigest() != CASCADE_SHA256:
                raise ValueError("lbpcascade_animeface.xml の sha256 が一致しません")
            storage = cv2.FileStorage(
                data.decode("utf-8"), cv2.FILE_STORAGE_READ | cv2.FILE_STORAGE_MEMORY
            )
            cascade = cv2.CascadeClassifier()
            if not cascade.read(storage.getFirstTopLevelNode()) or cascade.empty():
                raise ValueError("lbpcascade_animeface.xml を読めません")
        except Exception:
            logger.exception("顔の検出のカスケードを読めません。焦点は求めません")
            _cascade_failed = True
            return None
        _cascade = cascade
        return cascade


def _to_detect_gray(image: Image.Image) -> np.ndarray:
    """透明な部分を白で合成し、長辺 `DETECT_LONG_EDGE` に縮めたグレースケールの配列にする。"""
    working = image
    long_edge = max(working.size)
    if long_edge > DETECT_LONG_EDGE:
        scale = DETECT_LONG_EDGE / long_edge
        size = (
            max(1, round(working.width * scale)),
            max(1, round(working.height * scale)),
        )
        # 巨大な画像でも速いよう、先に整数倍で粗く縮めてから仕上げる(reducing_gap)。
        if working.mode not in ("RGB", "RGBA", "L", "LA"):
            working = working.convert("RGBA")
        working = working.resize(size, Image.Resampling.BILINEAR, reducing_gap=2.0)
    return np.asarray(composite_on_white(working).convert("L"), dtype=np.uint8)


def detect_faces(image: Image.Image) -> tuple[tuple[int, int], list[Face]]:
    """縮小画像の大きさ(幅, 高さ)と、その中で見つかった顔(大きい順)を返す。"""
    import cv2

    gray = _to_detect_gray(image)
    height, width = gray.shape[:2]
    cascade = _load_cascade()
    if cascade is None or width < MIN_SIZE[0] or height < MIN_SIZE[1]:
        return (width, height), []
    equalized = cv2.equalizeHist(gray)
    # CascadeClassifier の同じインスタンスを複数のスレッドから同時に使うのは安全と
    # 言い切れないので、検出の間だけ鍵を持つ(縮小画像なので数ミリ秒〜数十ミリ秒)。
    with _lock:
        found = cascade.detectMultiScale(
            equalized,
            scaleFactor=SCALE_FACTOR,
            minNeighbors=MIN_NEIGHBORS,
            minSize=MIN_SIZE,
        )
    faces = [Face(int(x), int(y), int(w), int(h)) for (x, y, w, h) in found]
    faces.sort(key=lambda face: face.width * face.height, reverse=True)
    return (width, height), faces


def focal_from_face(
    size: tuple[int, int], face: Face, y_shift: float = FOCAL_Y_SHIFT
) -> FocalPoint:
    """顔の矩形から焦点を作る。x は顔の中心、y は中心から顔の高さの `y_shift` 倍だけ上。
    どちらも 0〜1 に収める。"""
    width, height = size
    x = (face.left + face.width / 2) / width
    y = (face.top + face.height / 2 - face.height * y_shift) / height
    return FocalPoint(x=_clamp01(x), y=_clamp01(y))


def _clamp01(value: float) -> float:
    return round(min(1.0, max(0.0, value)), 4)


def detect_focal_point(image: Image.Image) -> FocalPoint | None:
    """画像の焦点(いちばん大きい顔の位置)。見つからない・検出に失敗したら None。"""
    try:
        size, faces = detect_faces(image)
    except Exception:
        logger.warning("顔の検出に失敗しました", exc_info=True)
        return None
    if not faces:
        return None
    return focal_from_face(size, faces[0])
