# リリース手順

GAKEI のバージョン付けとリリースの自動化は ADR-0021 で決めた。ここには実際の手順だけを書く。設計の理由は ADR-0021 を参照。

## バージョンの決め方

- バージョンは SemVer(`X.Y.Z`)。正は `backend/pyproject.toml` の `version` の1か所だけ(`frontend/package.json` の `version` は使わない)。
- ローカルMVPの間は `0.y.z`。
  - `y` を上げる: 利用者に見える機能の追加、互換性のない変更(設定名や DB の意味が変わるときなど)。
  - `z` を上げる: 修正、小さな改善。
  - `1.0.0` にするのは、本線(Azure)に着手したときではなく「ローカルMVPとして完成」と判断したとき。
- タグは `vX.Y.Z`(先頭に `v`)。main のコミットにだけ打つ(main はリリース済みの状態だけを指す。開発は dev で行う。下の「ブランチ」)。プレリリースは `vX.Y.Z-rc.N`(これから出す `X.Y.Z` の候補。`pyproject.toml` は `X.Y.Z` のまま、タグの `-` の前だけが比べられる)。

## ブランチ

- **`main`(既定ブランチ):** リリース済みの状態だけを指す。利用者が `git clone` や起動スクリプトで手にするのはこちら。`dev` からの PR でしか更新しない。タグは main にだけ打つ。
- **`dev`:** 開発の集約先。機能や修正は Issue に積み、`dev` から切った作業ブランチで作って PR を `dev` に出す(`gh pr create --base dev ...`。GitHub の PR 作成画面の既定は `main` なので、向き先を `dev` に変える)。
- `main` と `dev` はルールセットで守られている: PR 経由のみ(承認数は 0)、CI(`test (ubuntu-latest)` と `docker build & smoke test`)の成功が必須、force push と削除は禁止。`v*` のタグは削除・上書きが禁止(管理者はバイパス可)。
- CI は PR と、`main` / `dev` への push で走る。Windows のテストは push のときだけ。

## リリース手順

1. `dev` で `backend/pyproject.toml` の `version` を上げる PR を作り、マージする。
2. `dev` → `main` の PR を作り、マージする(タイトルは `Release vX.Y.Z` など。本文は空でよい。GitHub Release のノートは PR の一覧から自動で作られる)。

   ```bash
   gh pr create --base main --head dev --title "Release vX.Y.Z" --body ""
   ```

3. main を最新にしてタグを打ち、push する。

   ```bash
   git switch main && git pull
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```

4. GitHub の Actions で `Release` ワークフローが動く(タグの push がトリガー)。完了まで 10 分程度(`linux/amd64` と `linux/arm64` のマルチアーキテクチャビルドを含む)。Actions のログを見て、途中で失敗していないか確認する。
5. 完了したら次の2つを確認する。
   - GitHub の Releases に `vX.Y.Z` が並び、`THIRD_PARTY_NOTICES.txt` が添付されていること。
   - `ghcr.io/zolgear/gakei` に `X.Y.Z`、`X.Y`、`latest` のタグが並んでいること(プレリリースは `X.Y.Z-rc.N` だけ)。

## プレリリースの扱い

タグに `vX.Y.Z-rc.N` のようにハイフンを含めると、ワークフローはこれをプレリリースとして扱う。

- GitHub Release は Pre-release になる。
- イメージのタグは `X.Y.Z-rc.N` だけが付き、`X.Y` と `latest` は動かさない(先に進んでいるプレリリースで安定版の `latest` を上書きしないため)。

## 失敗したときの対処

- **タグとバージョンが一致していない(ワークフローの最初のステップで失敗):** 何も公開されていない。タグを削除して、`pyproject.toml` を直すか、正しいタグを打ち直す。

  ```bash
  git tag -d vX.Y.Z
  git push origin :refs/tags/vX.Y.Z
  ```

- **起動確認(`linux/amd64` のビルドと `/api/capabilities` の確認)で失敗:** GHCR への push より前なので、この時点でも何も公開されていない。原因を直す PR をマージしてから、同じ手順でタグを打ち直す。
- **GHCR への push は終わったが、その後に問題が見つかった:** 一度公開したタグは上書きしない(`latest` や `X.Y` を含め、動かしたタグを消したり差し替えたりしない)。修正して次のパッチ版(`vX.Y.(Z+1)`)を出す。

## GHCR のパッケージの公開範囲

public リポジトリの Actions から最初に push されたパッケージは、リポジトリに紐づいて自動で public になる(2026-09-27 の `v0.1.0` で確認。手作業は要らなかった)。パッケージのページは `https://github.com/users/zolgear/packages/container/package/gakei`。

private リポジトリから push した場合だけ private になる。その場合に public にしたければ、パッケージのページ → Package settings → Danger Zone の Change visibility で切り替える。

## 公開前の予行演習

公開リポジトリは新しく作る(ADR-0011)ため、private リポジトリのうちにワークフローを一度動かして確かめる。

1. private のまま `v0.1.0-rc.1` のようなプレリリースのタグを打って push する(`pyproject.toml` が `0.1.0` のとき)。
2. Actions のログと、できあがった GitHub Release / GHCR のパッケージを確認する。
3. 確認が終わったら、次の3つを削除する。
   - GitHub Release(`vX.Y.Z-rc.N`)
   - タグ自体(上記の「タグとバージョンが一致していない」の削除コマンドと同じ)
   - GHCR のパッケージ(`gh auth refresh -s read:packages,delete:packages` のあと `gh api -X DELETE /user/packages/container/gakei`。Web なら Package settings → Delete this package)

   理由: `ghcr.io/zolgear/gakei` という名前は公開リポジトリでも同じになる。旧(private)リポジトリに紐づいたパッケージやリリースを残すと、公開後にどちらが正式なものか混乱する。

## `compose.yaml` との関係

リポジトリの `compose.yaml` は、clone してソースからビルドする人向けのまま(`image:` は変えない)。公開イメージ(`ghcr.io/zolgear/gakei`)で起動したい場合は、README の `docker run` の例か、README に載せている compose の最小例を使う。
