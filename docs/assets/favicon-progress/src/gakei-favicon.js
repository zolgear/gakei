/**
 * Gakei favicon progress
 * ブラウザタブの favicon で生成の進捗を表す。依存なし（ES Module）。
 *
 *   import { createGakeiFavicon } from "./gakei-favicon.js";
 *   const fav = createGakeiFavicon({ title: true });
 *   fav.queued();              // 蓋を外した空の箱
 *   fav.fill(40);              // 下の段から溜まっていく
 *   fav.spin({ lid: true });   // 進捗が取れない間は回転
 *   fav.done();                // 蓋が閉まる → 通常ロゴ（非表示タブなら通知ドット）
 */

export const COLORS = {
  violet: "#9B8CFF",
  violetPink: "#BC82E2",
  pinkViolet: "#DE79C5",
  pink: "#FF6FA8",
  error: "#F0525A",
  lineOnDark: "#E8EAED",
  lineOnLight: "#2A2D33",
};

/** タイルの並び: 0=左上, 1=右上, 2=左下, 3=右下 */
const TILE_XY = [[18, 18], [33, 18], [18, 33], [33, 33]];
/** 起点(Violet) → 派生(Pink) のランプ。読み順で点灯する */
const RAMP = [COLORS.violet, COLORS.violetPink, COLORS.pinkViolet, COLORS.pink];
/** 回転の順序（時計回り）: 左上 → 右上 → 右下 → 左下 */
const CLOCKWISE = [0, 1, 3, 2];

const DIM_IDLE = 0.4; // 通常ロゴの非アクティブタイル
const DIM_OFF = 0.22; // 進捗表示中の消灯タイル

const BAR = "M4 4H60V9H4Z";
const FRAME = "M4 17.5H9V55H55V17.5H60V60H4Z";

// 外周（蓋 → 右辺 → 底辺 → 左辺）を時計回りに一周する中心線。gap は非表示区間。
const ORBIT = [
  { from: [4, 6.5], to: [60, 6.5] },
  { gap: 11 },
  { from: [57.5, 17.5], to: [57.5, 57.5] },
  { from: [57.5, 57.5], to: [6.5, 57.5] },
  { from: [6.5, 57.5], to: [6.5, 17.5] },
  { gap: 11 },
];
const segLen = (s) => s.gap ?? Math.hypot(s.to[0] - s.from[0], s.to[1] - s.from[1]);
const ORBIT_LEN = ORBIT.reduce((a, s) => a + segLen(s), 0);

const n = (v) => +v.toFixed(2);

/** 外周上の [start, start+len] 区間を path の d 文字列で返す */
function orbitPath(start, len) {
  const out = [];
  const walk = (a, b) => {
    let pos = 0;
    for (const s of ORBIT) {
      const L = segLen(s);
      const s0 = Math.max(a, pos), s1 = Math.min(b, pos + L);
      if (!s.gap && s1 > s0) {
        const p = (t) => [s.from[0] + (s.to[0] - s.from[0]) * t, s.from[1] + (s.to[1] - s.from[1]) * t];
        const [x0, y0] = p((s0 - pos) / L), [x1, y1] = p((s1 - pos) / L);
        out.push(`M${n(x0)} ${n(y0)}L${n(x1)} ${n(y1)}`);
      }
      pos += L;
    }
  };
  const a = ((start % ORBIT_LEN) + ORBIT_LEN) % ORBIT_LEN;
  walk(a, Math.min(a + len, ORBIT_LEN));
  if (a + len > ORBIT_LEN) walk(0, a + len - ORBIT_LEN);
  return out.join("");
}

/**
 * 状態から SVG 文字列を生成する。
 * @param {object} st
 * @param {Array<{color?:string|null, opacity?:number, fill?:number, empty?:boolean}>} st.tiles
 *   4枚（左上, 右上, 左下, 右下）。color=null は枠線色。empty=true は何も描かない（空のマス）。
 *   fill<1 は下から fill の割合だけ塗る（empty=true なら未塗りの部分も描かない）
 * @param {boolean} [st.lid=false] true で蓋（上辺）を外す
 * @param {number} [st.frameOpacity=1]
 * @param {{start:number,len:number,color:string}} [st.orbit]  外周を走る光
 * @param {string|null} [st.badge] 右上の通知ドット色
 * @param {"auto"|"dark"|"light"} [st.theme="auto"] auto は prefers-color-scheme で切替
 */
export function renderIcon({ tiles, lid = false, frameOpacity = 1, orbit = null, badge = null, theme = "auto" }) {
  const line = theme === "light" ? COLORS.lineOnLight : COLORS.lineOnDark;
  const g = theme === "auto" ? 'class="g"' : `fill="${line}"`;
  let s = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64">';
  if (theme === "auto") {
    s += `<style>.g{fill:${COLORS.lineOnDark}}@media (prefers-color-scheme:light){.g{fill:${COLORS.lineOnLight}}}</style>`;
  }
  // 通知ドットがあるときは周囲を抜いて重なりを避ける
  if (badge) {
    s += '<mask id="m"><rect width="64" height="64" fill="#fff"/><circle cx="54" cy="10" r="11" fill="#000"/></mask><g mask="url(#m)">';
  }
  const fo = frameOpacity < 1 ? ` opacity="${frameOpacity}"` : "";
  if (!lid) s += `<path d="${BAR}" ${g}${fo}/>`;
  s += `<path d="${FRAME}" ${g}${fo}/>`;

  tiles.forEach((t, i) => {
    const [x, y] = TILE_XY[i];
    const fill = t.fill ?? 1;
    const base = (attr) => `<rect x="${x}" y="${y}" width="13" height="13" rx="2.5" ${attr}/>`;
    const colorAttr = t.color ? `fill="${t.color}"` : g;
    const op = t.opacity ?? 1;
    if (t.empty && fill <= 0) return;
    if (fill >= 1) {
      s += base(`${colorAttr}${op < 1 ? ` opacity="${op}"` : ""}`);
    } else {
      if (!t.empty) s += base(`${g} opacity="${DIM_OFF}"`);
      if (fill > 0) {
        const h = n(13 * fill);
        s += `<clipPath id="c${i}"><rect x="${x}" y="${n(y + 13 - h)}" width="13" height="${h}"/></clipPath>`;
        s += base(`${colorAttr} clip-path="url(#c${i})"${op < 1 ? ` opacity="${op}"` : ""}`);
      }
    }
  });

  if (orbit) {
    s += `<path d="${orbitPath(orbit.start, orbit.len)}" fill="none" stroke="${orbit.color}" stroke-width="5" stroke-linejoin="miter"/>`;
  }
  if (badge) {
    s += `</g><circle cx="54" cy="10" r="8" fill="${badge}"/>`;
  }
  return s + "</svg>";
}

// ---------------------------------------------------------------------------
// プリセット（状態オブジェクトを返す）

const off = () => ({ color: null, opacity: DIM_OFF });
const empty = () => ({ empty: true, fill: 0 });
const clamp01 = (v) => Math.max(0, Math.min(1, v));

export const presets = {
  /** 通常ロゴ */
  idle: () => ({
    tiles: [{ color: COLORS.violet }, { color: null, opacity: DIM_IDLE }, { color: null, opacity: DIM_IDLE }, { color: COLORS.pink }],
  }),

  /** 待機: 蓋を外した空の箱 */
  queued: () => ({ lid: true, tiles: [empty(), empty(), empty(), empty()] }),

  /** 蓋だけ外す（中は通常ロゴのまま） */
  open: () => ({ ...presets.idle(), lid: true }),

  /**
   * 点灯で進捗 0〜100。タイルを読み順（左上→右上→左下→右下）で点灯。
   * @param {number} pct
   * @param {{lid?:boolean, smooth?:boolean}} [o]
   *   lid: 完了まで蓋を外す / smooth: 25% 刻みの間をタイル内の下からの塗りで表す
   */
  progress: (pct, { lid = false, smooth = false } = {}) => {
    const lit = clamp01(pct / 100) * 4;
    const tiles = RAMP.map((color, i) => {
      if (lit >= i + 1) return { color };
      if (smooth && lit > i) return { color, fill: lit - i };
      return off();
    });
    return { tiles, lid };
  },

  /**
   * 溜まる進捗 0〜100（推奨）。蓋のない空の箱に、下の段から溜まっていく。
   * 閉じるのは done()（または sealed()）。
   * @param {number} pct
   * @param {{mode?:"liquid"|"tile"}} [o]
   *   liquid: 段ごとに水位が上がる（0-50% 下段、50-100% 上段）
   *   tile:   左下→右下→左上→右上 の順に 1 マスずつ 25% 刻みで埋まる
   */
  fill: (pct, { mode = "liquid" } = {}) => {
    const p = clamp01(pct / 100);
    const tiles = RAMP.map((color) => ({ color, empty: true, fill: 0 }));
    if (mode === "tile") {
      [2, 3, 0, 1].forEach((i, k) => { if (p * 4 >= k + 1) tiles[i].fill = 1; });
    } else {
      const bottom = clamp01(p * 2), top = clamp01(p * 2 - 1);
      tiles[2].fill = tiles[3].fill = bottom;
      tiles[0].fill = tiles[1].fill = top;
    }
    return { tiles, lid: true };
  },

  /** 蓋が閉まった瞬間: 満杯の箱に蓋 */
  sealed: () => ({ tiles: RAMP.map((color) => ({ color })) }),

  /**
   * 回転（不定プログレス）
   * @param {number} frame 任意の整数（呼ぶたびに +1）
   * @param {{style?:"tile"|"trail"|"orbit", lid?:boolean}} [o]
   *   tile: 光るタイルが時計回り（4コマ）/ trail: 残像つき（4コマ）/ orbit: 光が外周を回る（12コマ）
   *   lid: 蓋を外したまま回す（tile / trail のみ）
   */
  spin: (frame, { style = "tile", lid = false } = {}) => {
    if (style === "orbit") {
      const step = ORBIT_LEN / 12;
      return {
        frameOpacity: 0.35,
        tiles: [{ color: COLORS.violet, opacity: 0.35 }, off(), off(), { color: COLORS.pink, opacity: 0.35 }],
        orbit: { start: (frame % 12) * step, len: 26, color: COLORS.violet },
      };
    }
    const k = ((frame % 4) + 4) % 4;
    const cur = CLOCKWISE[k], prev = CLOCKWISE[(k + 3) % 4];
    const tiles = [0, 1, 2, 3].map((i) => {
      if (i === cur) return { color: RAMP[i] };
      if (style === "trail" && i === prev) return { color: RAMP[i], opacity: 0.45 };
      return off();
    });
    return { tiles, lid };
  },

  /** 完了。notify=true で右上に通知ドット */
  done: ({ notify = false } = {}) => ({ ...presets.idle(), badge: notify ? COLORS.pink : null }),

  /** エラー */
  error: () => ({ tiles: [off(), off(), off(), { color: COLORS.error }] }),
};

// ---------------------------------------------------------------------------
// コントローラ

const svgUrl = (svg) => "data:image/svg+xml," + encodeURIComponent(svg);

function svgToPng(svg, size = 64) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => {
      const c = document.createElement("canvas");
      c.width = c.height = size;
      c.getContext("2d").drawImage(img, 0, 0, size, size);
      resolve(c.toDataURL("image/png"));
    };
    img.onerror = reject;
    img.src = svgUrl(svg);
  });
}

/**
 * @param {object} [opt]
 * @param {boolean} [opt.title=false]     document.title に [42%] を付ける
 * @param {number}  [opt.spinInterval=250] spin() のコマ送り間隔 ms
 * @param {"svg"|"png"} [opt.format="svg"] png は canvas で 64px に変換（SVG favicon 非対応環境向け）
 * @param {(svg:string)=>void} [opt.onChange] 反映した SVG を受け取る（アプリ内の表示と同期したい場合）
 */
export function createGakeiFavicon({ title = false, spinInterval = 250, format = "svg", onChange } = {}) {
  let link = null, saved = [], timer = null, frame = 0, spinOpt = {};
  let baseTitle = document.title, onVisible = null, seq = 0;

  const ensureLink = () => {
    if (link) return;
    saved = [...document.querySelectorAll('link[rel~="icon"]')];
    saved.forEach((l) => l.remove());
    link = document.createElement("link");
    link.rel = "icon";
    link.type = format === "png" ? "image/png" : "image/svg+xml";
    document.head.appendChild(link);
  };

  const apply = async (state, pct = null) => {
    ensureLink();
    const my = ++seq;
    let href;
    onChange?.(renderIcon(state));
    if (format === "png") {
      const dark = matchMedia("(prefers-color-scheme: dark)").matches;
      href = await svgToPng(renderIcon({ ...state, theme: dark ? "dark" : "light" }));
    } else {
      href = svgUrl(renderIcon(state));
    }
    if (my !== seq) return; // 古い非同期結果は捨てる
    link.href = href;
    if (title) document.title = pct == null ? baseTitle : `[${Math.round(pct)}%] ${baseTitle}`;
  };

  const stop = () => {
    if (timer) { clearInterval(timer); clearTimeout(timer); }
    timer = null;
    if (onVisible) document.removeEventListener("visibilitychange", onVisible);
    onVisible = null;
  };

  const api = {
    idle() { stop(); apply(presets.idle()); },
    queued() { stop(); apply(presets.queued()); },
    /** 蓋だけ外す */
    open() { stop(); apply(presets.open()); },
    /** 点灯で進捗 @param {number} pct 0〜100  @param {{lid?:boolean, smooth?:boolean}} [o] */
    progress(pct, o) { stop(); apply(presets.progress(pct, o), pct); },
    /** 溜まる進捗（推奨） @param {number} pct 0〜100  @param {{mode?:"liquid"|"tile"}} [o] */
    fill(pct, o) { stop(); apply(presets.fill(pct, o), pct); },
    /** タイマーで回す。@param {{style?:"tile"|"trail"|"orbit", lid?:boolean}} [o] */
    spin(o = {}) {
      stop(); spinOpt = o;
      apply(presets.spin(frame, o));
      timer = setInterval(() => apply(presets.spin(++frame, spinOpt)), spinInterval);
    },
    /** サーバーからの進捗イベントごとに 1 コマ進める（タイマーを使わない回転） */
    tick(o = spinOpt) { if (timer) { clearInterval(timer); timer = null; } apply(presets.spin(++frame, o)); },
    /**
     * 完了。seal=true なら蓋が閉まった満杯の箱を sealMs 表示してから通常ロゴへ。
     * タブが非表示なら通知ドットを出し、表示されたら通常ロゴに戻す。
     */
    done({ notify = true, seal = true, sealMs = 700 } = {}) {
      stop();
      if (seal) {
        apply(presets.sealed(), null);
        timer = setTimeout(() => { timer = null; api.done({ notify, seal: false }); }, sealMs);
        return;
      }
      if (notify && document.hidden) {
        apply(presets.done({ notify: true }));
        onVisible = () => { if (!document.hidden) { stop(); apply(presets.idle()); } };
        document.addEventListener("visibilitychange", onVisible);
      } else {
        apply(presets.idle());
      }
    },
    error() { stop(); apply(presets.error()); },
    /** 元の <link rel="icon"> とタイトルに戻す */
    restore() {
      stop();
      if (link) { link.remove(); link = null; saved.forEach((l) => document.head.appendChild(l)); saved = []; }
      document.title = baseTitle;
    },
  };
  return api;
}
