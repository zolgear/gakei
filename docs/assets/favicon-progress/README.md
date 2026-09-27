# Gakei favicon progress

生成の進捗を、ブラウザタブの favicon で表すための素材と実装です。
`demo.html` をブラウザで開くと、このページの favicon が実際に切り替わります。

## パターン

| # | パターン | 意味 | フォルダ |
|---|---|---|---|
| 1 | 蓋 | 「画」の上辺（蓋）が消えて箱が開く。戻れば完了 | `frames/1-lid/` |
| 2a | 回転：タイル | 光るタイルが時計回りに移動（4 コマ） | `frames/2-spin-tile/` |
| 2b | 回転：残像つき | 2a に 1 つ前の位置の残像を付ける（4 コマ） | `frames/2-spin-trail/` |
| 2c | 回転：外周 | 光が「画」の外周（蓋 → 右 → 底 → 左）を回る（12 コマ） | `frames/2-spin-orbit/` |
| 3a | 点灯 | 0 / 25 / 50 / 75 / 100% をタイルの点灯数で表す | `frames/3-progress/` |
| 3b | 点灯（なめらか） | 25% 刻みの間を、タイルの下からの塗りで表す | `frames/3-progress-smooth/` |
| 4a | 溜まる（推奨） | 蓋のない空の箱に、下の段から水位が上がるように溜まる（0–50% 下段、50–100% 上段）。最後に蓋が閉まる | `frames/4-fill-liquid/` |
| 4b | 溜まる：1 マスずつ | 左下 → 右下 → 左上 → 右上 の順に 25% 刻みで埋まる。最後に蓋が閉まる | `frames/4-fill-tile/` |
| 4c | 溜まる：回転 | 進捗が取れない間は、蓋を外したまま回転 | `frames/4-fill-spin/` |
| — | 状態 | 通常 / 待機（蓋のない空の箱）/ 蓋が閉まった満杯の箱 / 完了＋通知ドット / エラー | `frames/5-states/` |

- パターン 4 の流れ：空の箱（`queued`）→ 下から溜まる（`fill`）→ 満杯で蓋が閉まる（`sealed`、0.7 秒）→ 通常ロゴ（左上 Violet・右下 Pink）
- タイルの色は位置ごとに Violet（左上）→ Pink（右下）のランプです。
- 回転は時計回り（左上 → 右上 → 右下 → 左下）です。
- 全フレームで、枠の色は `prefers-color-scheme` に合わせて自動で切り替わります。

## 使い方

```js
import { createGakeiFavicon } from "./src/gakei-favicon.js";

const fav = createGakeiFavicon({ title: true }); // title: タブ名に [42%] を付ける

fav.queued();                        // キュー投入：蓋のない空の箱
fav.fill(42);                        // 進捗 0〜100：下の段から溜まる
fav.spin({ style: "tile", lid: true }); // 進捗が取れない間
fav.done();                          // 蓋が閉まる → 通常ロゴ（非表示タブなら通知ドット）
fav.error();
fav.restore();                       // 元の <link rel="icon"> とタイトルに戻す
```

### WebSocket の進捗イベントに接続する例

```js
ws.onmessage = (e) => {
  const m = JSON.parse(e.data);
  switch (m.type) {
    case "queued":   fav.queued(); break;
    case "progress": fav.fill((m.value / m.max) * 100); break;
    case "running":  fav.tick({ style: "tile", lid: true }); break; // イベントごとに 1 コマ回す
    case "done":     fav.done(); break;
    case "error":    fav.error(); break;
  }
};
```

### API

| API | オプション | 内容 |
|---|---|---|
| `createGakeiFavicon()` | `title` | `document.title` に `[42%]` を付ける（既定 `false`） |
| | `spinInterval` | `spin()` のコマ送り間隔 ms（既定 250） |
| | `format` | `"svg"`（既定）または `"png"`（canvas で 64px PNG に変換） |
| | `onChange(svg)` | 反映した SVG を受け取る。アプリ内のロゴを同期させたい場合に使う |
| `queued()` | | 蓋のない空の箱 |
| `open()` | | 蓋だけ外す（中は通常ロゴ） |
| `fill(pct, o)` | `mode` | `"liquid"` 段ごとに水位が上がる（既定）/ `"tile"` 1 マスずつ |
| `progress(pct, o)` | `smooth` | 25% 刻みの間をタイル内の塗りで表す |
| | `lid` | `true` で蓋を外したまま点灯 |
| `spin(o)` / `tick(o)` | `style` | `"tile"` / `"trail"` / `"orbit"` |
| | `lid` | `true` で蓋を外したまま回す（`tile` / `trail`） |
| `done(o)` | `seal` | 蓋が閉まった満杯の箱を表示してから通常ロゴへ（既定 `true`） |
| | `sealMs` | 蓋が閉まった状態の表示時間（既定 700） |
| | `notify` | 非表示タブで完了したら通知ドットを出す（既定 `true`） |
| `error()` / `idle()` / `restore()` | | エラー / 通常ロゴ / 元に戻す |

アイコン単体が必要な場合は `renderIcon(presets.fill(50))` で SVG 文字列を取得できます。

## 注意点

- **SVG 内のアニメーションは使っていません。** Chrome 系では favicon の SVG アニメーション（CSS / SMIL）が再生されないため、JS で 1 コマずつ差し替えています。
- **非表示タブではタイマーが間引かれます。** Chrome ではバックグラウンドタブの `setInterval` がおおむね 1 秒に 1 回まで遅くなり、長時間非表示だとさらに間引かれます。favicon が見られるのは主に別タブにいるときなので、
  - 回転はコマが飛んでも読める 4 コマ（`tile` / `trail`）を基本にしています。
  - 進捗値が取れるなら `progress()`、取れないならサーバーのイベントごとに `tick()` を呼ぶ使い方をおすすめします。
- **Safari などで SVG の favicon が反映されない場合**は `format: "png"` を試してください。
- **16px では外周回転（2c）の光が 1〜2px 程度になります。** 小さいサイズでの視認性は `tile` / `trail` のほうが上です。

## 構成

```
demo.html               動作デモ（依存なし、ローカルで開ける）
preview.gif             全パターンのアニメーション
src/gakei-favicon.js    実装（ES Module、依存なし）
src/gakei-favicon.d.ts  型定義
frames/                 各パターンの静止フレーム SVG（58 枚）
```
