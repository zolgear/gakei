"""画像を展開する前に確かめる画素数の上限(展開の爆弾を避ける)。

Pillow の既定(`Image.MAX_IMAGE_PIXELS`、約 8950 万画素で警告、その2倍の約 1.79 億画素で例外)
のままだと、ヘッダーで寸法を偽った小さなファイルでも RGBA で 700MB 近くまで展開してしまう。
取り込み・画像で探す・アバターの入口では、`Image.open` の直後(`load()` の前)に寸法を確かめ、
この上限を超えるものを断る。

上限は 64M(2^26 = 67,108,864)画素。選んだ理由:
- OpenAI の画像 API の出力は最大でも約 830 万画素(`sizes.MAX_TOTAL_PIXELS`。4K 相当)、
  ComfyUI や SD WebUI で 4K〜8K に拡大した出力も 8192x8192 = 64M 画素に収まる。
- 利用者が持ち込む写真も、一般的な一眼(2400 万〜6100 万画素)や 1 億画素級のスマホの
  既定の出力(画素の統合で 1200 万〜5000 万画素)を含めて収まる。
- 展開した RGBA でも 256MB 程度にとどまり、小さな VM でも1枚なら扱える。
"""

from __future__ import annotations

from PIL import Image

MAX_IMAGE_PIXELS = 64 * 1024 * 1024


def exceeds_pixel_limit(image: Image.Image, max_pixels: int = MAX_IMAGE_PIXELS) -> bool:
    """開いた画像の寸法(ヘッダーの値)が上限を超えるか。展開(`load()`)の前に呼ぶ。"""
    width, height = image.size
    return width * height > max_pixels
