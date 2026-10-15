/**
 * 配置(ADR-0050)の WebGL の描画。画面の表示と保存の書き出しの両方がこれを使う
 * (同じ処理で描くので、画面と保存の結果が一致する)。
 *
 * 素材は四隅を頂点にした三角形2枚で描き(凸の四角形なので2枚で覆える。双一次の写像でも
 * 辺はまっすぐなので、写った形は四隅を結んだ四角形そのもの)、フラグメントシェーダーで画素ごとに
 * 「キャンバスの座標 → 単位正方形」の逆の双一次の写像を解いてテクスチャの座標を求める
 * (`bilinear.ts`。三角形ごとのアフィンの近似ではなく、四角形全体で1つの双一次の写像になる。
 * 射影変換と違って奥行きは付かず、どの辺の上でも絵の間隔は均等。ADR-0050 1章)。
 *
 * テクスチャは乗算済みアルファで載せ(UNPACK_PREMULTIPLY_ALPHA)、合成は
 * ONE / ONE_MINUS_SRC_ALPHA にする(透過 PNG の素材の縁が黒ずまない)。縮小の多い素材でも
 * ちらつかないよう、ミップマップを作る(WebGL2 は 2 の累乗でない大きさでも作れる)。
 */
import { bilinearCoefficients, LINEAR_EPSILON } from './bilinear'
import type { Quad } from './quad'

const VERTEX_SHADER = `#version 300 es
in vec2 a_pos;
uniform vec3 u_view;
uniform vec2 u_viewport;
out vec2 v_pos;
void main() {
  v_pos = a_pos;
  vec2 d = a_pos * u_view.x + u_view.yz;
  vec2 clip = d / u_viewport * 2.0 - 1.0;
  gl_Position = vec4(clip.x, -clip.y, 0.0, 1.0);
}
`

// 逆の双一次の写像。`bilinear.ts` の `invBilinearWith` と同じ手順(係数は `bilinearCoefficients`
// で作る: u_origin = P0、u_e = P1-P0、u_f = P3-P0、u_g = P0-P1+P2-P3 を u_invScale 倍したもの)。
const FRAGMENT_SHADER = `#version 300 es
precision highp float;
in vec2 v_pos;
uniform vec2 u_origin;
uniform vec2 u_e;
uniform vec2 u_f;
uniform vec2 u_g;
uniform float u_invScale;
uniform vec2 u_flip;
uniform sampler2D u_tex;
out vec4 outColor;

float cross2(vec2 a, vec2 b) { return a.x * b.y - a.y * b.x; }

// solveU: h - v f = u (e + v g) の最小二乗の解。
float solveU(vec2 h, float v) {
  vec2 d = u_e + u_g * v;
  return dot(h - u_f * v, d) / max(dot(d, d), 1e-20);
}

// outside: (u, v) が [0,1]² からどれだけはみ出しているか。
float outsideAmount(vec2 uv) {
  return max(max(0.0, max(-uv.x, uv.x - 1.0)), max(-uv.y, uv.y - 1.0));
}

vec2 invBilinear(vec2 p) {
  vec2 h = (p - u_origin) * u_invScale;
  float k2 = cross2(u_g, u_f);
  float k1 = cross2(u_e, u_f) + cross2(h, u_g);
  float k0 = cross2(h, u_e);
  if (abs(k2) < ${LINEAR_EPSILON.toExponential()}) {
    // 1次: k1 v + k0 = 0(左右の辺が平行)。
    float v = -k0 / k1;
    return vec2(solveU(h, v), v);
  }
  // 2次: 桁落ちしない形の解の公式。2つの解のうち [0,1]² に近い方。
  float disc = max(0.0, k1 * k1 - 4.0 * k0 * k2);
  float q = -0.5 * (k1 + (k1 >= 0.0 ? 1.0 : -1.0) * sqrt(disc));
  float va = q / k2;
  float vb = abs(q) < 1e-20 ? va : k0 / q;
  vec2 a = vec2(solveU(h, va), va);
  vec2 b = vec2(solveU(h, vb), vb);
  return outsideAmount(a) <= outsideAmount(b) ? a : b;
}

void main() {
  vec2 uv = invBilinear(v_pos);
  if (u_flip.x > 0.5) uv.x = 1.0 - uv.x;
  if (u_flip.y > 0.5) uv.y = 1.0 - uv.y;
  outColor = texture(u_tex, uv);
}
`

export interface RenderLayer {
  texture: WebGLTexture
  corners: Quad
  flipX: boolean
  flipY: boolean
}

/** キャンバスの px → 描画先の px(デバイスの px)。d = p * scale + offset。 */
export interface RenderView {
  scale: number
  offsetX: number
  offsetY: number
}

/** 乗算済みの RGBA(0〜1)。 */
export type ClearColor = readonly [number, number, number, number]

export const WHITE: ClearColor = [1, 1, 1, 1]
export const TRANSPARENT: ClearColor = [0, 0, 0, 0]

function compile(gl: WebGL2RenderingContext, type: number, source: string): WebGLShader {
  const shader = gl.createShader(type)
  if (!shader) throw new Error('createShader failed')
  gl.shaderSource(shader, source)
  gl.compileShader(shader)
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    const log = gl.getShaderInfoLog(shader)
    gl.deleteShader(shader)
    throw new Error(`shader compile failed: ${log ?? ''}`)
  }
  return shader
}

export class CompositeRenderer {
  readonly gl: WebGL2RenderingContext
  readonly maxTextureSize: number
  readonly maxViewport: number
  private program: WebGLProgram
  private buffer: WebGLBuffer
  private vao: WebGLVertexArrayObject
  private uView: WebGLUniformLocation | null
  private uViewport: WebGLUniformLocation | null
  private uOrigin: WebGLUniformLocation | null
  private uE: WebGLUniformLocation | null
  private uF: WebGLUniformLocation | null
  private uG: WebGLUniformLocation | null
  private uInvScale: WebGLUniformLocation | null
  private uFlip: WebGLUniformLocation | null
  private uTex: WebGLUniformLocation | null

  private constructor(gl: WebGL2RenderingContext) {
    this.gl = gl
    this.maxTextureSize = gl.getParameter(gl.MAX_TEXTURE_SIZE) as number
    const dims = gl.getParameter(gl.MAX_VIEWPORT_DIMS) as Int32Array
    const rb = gl.getParameter(gl.MAX_RENDERBUFFER_SIZE) as number
    this.maxViewport = Math.min(dims[0], dims[1], rb)
    const vs = compile(gl, gl.VERTEX_SHADER, VERTEX_SHADER)
    const fs = compile(gl, gl.FRAGMENT_SHADER, FRAGMENT_SHADER)
    const program = gl.createProgram()
    if (!program) throw new Error('createProgram failed')
    gl.attachShader(program, vs)
    gl.attachShader(program, fs)
    gl.linkProgram(program)
    gl.deleteShader(vs)
    gl.deleteShader(fs)
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
      throw new Error(`program link failed: ${gl.getProgramInfoLog(program) ?? ''}`)
    }
    this.program = program
    this.uView = gl.getUniformLocation(program, 'u_view')
    this.uViewport = gl.getUniformLocation(program, 'u_viewport')
    this.uOrigin = gl.getUniformLocation(program, 'u_origin')
    this.uE = gl.getUniformLocation(program, 'u_e')
    this.uF = gl.getUniformLocation(program, 'u_f')
    this.uG = gl.getUniformLocation(program, 'u_g')
    this.uInvScale = gl.getUniformLocation(program, 'u_invScale')
    this.uFlip = gl.getUniformLocation(program, 'u_flip')
    this.uTex = gl.getUniformLocation(program, 'u_tex')

    const buffer = gl.createBuffer()
    const vao = gl.createVertexArray()
    if (!buffer || !vao) throw new Error('buffer allocation failed')
    this.buffer = buffer
    this.vao = vao
    gl.bindVertexArray(vao)
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer)
    gl.bufferData(gl.ARRAY_BUFFER, 12 * 4, gl.DYNAMIC_DRAW)
    const loc = gl.getAttribLocation(program, 'a_pos')
    gl.enableVertexAttribArray(loc)
    gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0)
    gl.bindVertexArray(null)
  }

  /** WebGL2 が使えなければ null。 */
  static create(canvas: HTMLCanvasElement, opts: { preserveDrawingBuffer?: boolean } = {}): CompositeRenderer | null {
    let gl: WebGL2RenderingContext | null = null
    try {
      gl = canvas.getContext('webgl2', {
        alpha: true,
        premultipliedAlpha: true,
        antialias: true,
        preserveDrawingBuffer: opts.preserveDrawingBuffer ?? false,
      })
    } catch {
      gl = null
    }
    if (!gl) return null
    try {
      return new CompositeRenderer(gl)
    } catch {
      return null
    }
  }

  /**
   * 画像からテクスチャを作る。maxEdge(と GPU の上限)を超える画像は 2D canvas で縮めてから載せる。
   */
  createTexture(image: HTMLImageElement | HTMLCanvasElement | ImageBitmap, maxEdge = Infinity): WebGLTexture {
    const gl = this.gl
    const limit = Math.min(this.maxTextureSize, maxEdge)
    let source: TexImageSource = image
    const w = 'naturalWidth' in image ? image.naturalWidth : image.width
    const h = 'naturalHeight' in image ? image.naturalHeight : image.height
    if (w > limit || h > limit) {
      const s = limit / Math.max(w, h)
      const c = document.createElement('canvas')
      c.width = Math.max(1, Math.floor(w * s))
      c.height = Math.max(1, Math.floor(h * s))
      const ctx = c.getContext('2d')
      if (ctx) {
        ctx.imageSmoothingQuality = 'high'
        ctx.drawImage(image, 0, 0, c.width, c.height)
        source = c
      }
    }
    const tex = gl.createTexture()
    if (!tex) throw new Error('createTexture failed')
    gl.bindTexture(gl.TEXTURE_2D, tex)
    gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, true)
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false)
    gl.pixelStorei(gl.UNPACK_COLORSPACE_CONVERSION_WEBGL, gl.NONE)
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, source)
    gl.generateMipmap(gl.TEXTURE_2D)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE)
    gl.bindTexture(gl.TEXTURE_2D, null)
    return tex
  }

  deleteTexture(tex: WebGLTexture): void {
    this.gl.deleteTexture(tex)
  }

  /** 描画先(canvas の実寸)全体を塗る。 */
  clear(color: ClearColor): void {
    const gl = this.gl
    gl.viewport(0, 0, gl.drawingBufferWidth, gl.drawingBufferHeight)
    gl.clearColor(color[0], color[1], color[2], color[3])
    gl.clear(gl.COLOR_BUFFER_BIT)
  }

  /** 素材を下から順に重ねて描く(clear はしない)。 */
  drawLayers(layers: readonly RenderLayer[], view: RenderView): void {
    const gl = this.gl
    const width = gl.drawingBufferWidth
    const height = gl.drawingBufferHeight
    gl.viewport(0, 0, width, height)
    gl.useProgram(this.program)
    gl.bindVertexArray(this.vao)
    gl.enable(gl.BLEND)
    gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA)
    gl.uniform3f(this.uView, view.scale, view.offsetX, view.offsetY)
    gl.uniform2f(this.uViewport, width, height)
    gl.uniform1i(this.uTex, 0)
    gl.activeTexture(gl.TEXTURE0)
    gl.bindBuffer(gl.ARRAY_BUFFER, this.buffer)
    const verts = new Float32Array(12)
    for (const layer of layers) {
      const c = bilinearCoefficients(layer.corners)
      if (!c) continue
      const [p0, p1, p2, p3] = layer.corners
      verts.set([p0.x, p0.y, p1.x, p1.y, p2.x, p2.y, p0.x, p0.y, p2.x, p2.y, p3.x, p3.y])
      gl.bufferSubData(gl.ARRAY_BUFFER, 0, verts)
      gl.uniform2f(this.uOrigin, c.origin.x, c.origin.y)
      gl.uniform2f(this.uE, c.e.x, c.e.y)
      gl.uniform2f(this.uF, c.f.x, c.f.y)
      gl.uniform2f(this.uG, c.g.x, c.g.y)
      gl.uniform1f(this.uInvScale, c.invScale)
      gl.uniform2f(this.uFlip, layer.flipX ? 1 : 0, layer.flipY ? 1 : 0)
      gl.bindTexture(gl.TEXTURE_2D, layer.texture)
      gl.drawArrays(gl.TRIANGLES, 0, 6)
    }
    gl.bindTexture(gl.TEXTURE_2D, null)
    gl.bindVertexArray(null)
  }

  dispose(): void {
    const gl = this.gl
    gl.deleteBuffer(this.buffer)
    gl.deleteVertexArray(this.vao)
    gl.deleteProgram(this.program)
  }

  /** コンテキストを手放す(書き出し用の使い捨ての canvas で、GPU のメモリをすぐ返す)。 */
  loseContext(): void {
    this.gl.getExtension('WEBGL_lose_context')?.loseContext()
  }
}

let webglAvailable: boolean | null = null

/** WebGL2 が使えるか(1回だけ確かめて覚える)。 */
export function isWebGL2Available(): boolean {
  if (webglAvailable !== null) return webglAvailable
  try {
    const canvas = document.createElement('canvas')
    const gl = canvas.getContext('webgl2')
    webglAvailable = gl !== null
    gl?.getExtension('WEBGL_lose_context')?.loseContext()
  } catch {
    webglAvailable = false
  }
  return webglAvailable
}
