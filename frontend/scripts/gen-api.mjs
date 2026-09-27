// backend の OpenAPI を書き出し、openapi-typescript で型を生成する。
// サーバーは起動しない(`export_openapi` はアプリを import するだけで済む)。
import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const frontendRoot = resolve(__dirname, "..");
const backendRoot = resolve(frontendRoot, "..", "backend");
const outFile = resolve(frontendRoot, "src/api/schema.d.ts");

const tmpDir = mkdtempSync(join(tmpdir(), "gakei-openapi-"));
const openapiJson = join(tmpDir, "openapi.json");

try {
  console.log("[gen:api] backend の OpenAPI を書き出し中...");
  execFileSync("uv", ["run", "python", "-m", "app.tools.export_openapi", openapiJson], {
    cwd: backendRoot,
    stdio: "inherit",
  });

  console.log("[gen:api] openapi-typescript で型を生成中...");
  // npx は Windows では npx.cmd なので execFileSync で直接起動できない。
  // インストール済みの CLI を node で実行する。
  const cli = resolve(frontendRoot, "node_modules/openapi-typescript/bin/cli.js");
  execFileSync(process.execPath, [cli, openapiJson, "-o", outFile], {
    cwd: frontendRoot,
    stdio: "inherit",
  });

  console.log(`[gen:api] 完了: ${outFile}`);
} finally {
  rmSync(tmpDir, { recursive: true, force: true });
}
