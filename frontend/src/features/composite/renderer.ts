/**
 * 配置(ADR-0050)の WebGL の描画。画面の表示と保存の書き出しの両方がこれを使う
 * (同じ処理で描くので、画面と保存の結果が一致する)。
 *
 * 素材は四隅を頂点にした三角形2枚で描き(凸の四角形なので2枚で覆える)、フラグメント
 * シェーダーで画素ごとに「キャンバスの座標 → 単位正方形」の逆ホモグラフィーを当てて
 * テクスチャの座標を求める。三角形ごとのアフィンの近似ではなく、正確な射影の写像になる。
 *
 * テクスチャは乗算済みアルファで載せ(UNPACK_PREMULTIPLY_ALPHA)、合成は
 * ONE / ONE_MINUS_SRC_ALPHA にする(透過 PNG の素材の縁が黒ずまない)。縮小の多い素材でも
 * ちらつかないよう、ミップマップを作る(WebGL2 は 2 の累乗でない大きさでも作れる)。
 */
import { invertMat3, squareToQuad, toColumnMajor } from './homography'
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

const FRAGMENT_SHADER = `#version 300 es
precision highp float;
in vec2 v_pos;
uniform mat3 u_inv;
uniform vec2 u_flip;
uniform sampler2D u_tex;
out vec4 outColor;
void main() {
  vec3 uvw = u_inv * vec3(v_pos, 1.0);
  vec2 uv = uvw.xy / uvw.z;
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
  private uInv: WebGLUniformLocation | null
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
    this.uInv = gl.getUniformLocation(program, 'u_inv')
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
      const inv = invertMat3(squareToQuad(layer.corners))
      if (!inv) continue
      const [p0, p1, p2, p3] = layer.corners
      verts.set([p0.x, p0.y, p1.x, p1.y, p2.x, p2.y, p0.x, p0.y, p2.x, p2.y, p3.x, p3.y])
      gl.bufferSubData(gl.ARRAY_BUFFER, 0, verts)
      gl.uniformMatrix3fv(this.uInv, false, toColumnMajor(inv))
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
