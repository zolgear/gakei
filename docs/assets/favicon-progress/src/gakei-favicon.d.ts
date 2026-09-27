export interface TileState { color?: string | null; opacity?: number; fill?: number; empty?: boolean }
export interface IconState {
  tiles: TileState[];
  lid?: boolean;
  frameOpacity?: number;
  orbit?: { start: number; len: number; color: string } | null;
  badge?: string | null;
  theme?: "auto" | "dark" | "light";
}
export type FillMode = "liquid" | "tile";
export type SpinStyle = "tile" | "trail" | "orbit";

export declare const COLORS: Record<
  "violet" | "violetPink" | "pinkViolet" | "pink" | "error" | "lineOnDark" | "lineOnLight", string>;

export declare function renderIcon(state: IconState): string;

export declare const presets: {
  idle(): IconState;
  queued(): IconState;
  open(): IconState;
  progress(pct: number, o?: { lid?: boolean; smooth?: boolean }): IconState;
  fill(pct: number, o?: { mode?: FillMode }): IconState;
  sealed(): IconState;
  spin(frame: number, o?: { style?: SpinStyle; lid?: boolean }): IconState;
  done(o?: { notify?: boolean }): IconState;
  error(): IconState;
};

export interface GakeiFavicon {
  idle(): void;
  queued(): void;
  open(): void;
  progress(pct: number, o?: { lid?: boolean; smooth?: boolean }): void;
  fill(pct: number, o?: { mode?: FillMode }): void;
  spin(o?: { style?: SpinStyle; lid?: boolean }): void;
  tick(o?: { style?: SpinStyle; lid?: boolean }): void;
  done(o?: { notify?: boolean; seal?: boolean; sealMs?: number }): void;
  error(): void;
  restore(): void;
}

export declare function createGakeiFavicon(opt?: {
  title?: boolean;
  spinInterval?: number;
  format?: "svg" | "png";
  onChange?: (svg: string) => void;
}): GakeiFavicon;
