# リリース手順

GAKEI のバージョン付けとリリースの自動化は ADR-0021 で決めた。ここには実際の手順だけを書く。設計の理由は ADR-0021 を参照。

## バージョンの決め方

- バージョンは SemVer(`X.Y.Z`)。正は `backend/pyproject.toml` の `version` の1か所だけ(`frontend/package.json` の `version` は使わない)。
- ローカルMVPの間は `0.y.z`。
  - `y` を上げる: 利用者に見える機能の追加、互換性のない変更(設定名や DB の意味が変わるときなど)。
  - `z` を上げる: 修正、小さな改善。
  - `1.0.0` にするのは、本線(Azure)に着手したときではなく「ローカルMVPとして完成」と判断したとき。
- タグは `vX.Y.Z`(先頭に `v`)。main のコミットにだけ打つ。プレリリースは `vX.Y.Z-rc.N`(これから出す `X.Y.Z` の候補。`pyproject.toml` は `X.Y.Z` のまま、タグの `-` の前だけが比べられる)。

## リリース手順

1. `backend/pyproject.toml` の `version` を上げる PR を作り、マージする。
2. main を最新にしてタグを打ち、push する。

   ```bash
   git switch main && git pull
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```

3. GitHub の Actions で `Release` ワークフローが動く(タグの push がトリガー)。完了まで 10 分程度(`linux/amd64` と `linux/arm64` のマルチアーキテクチャビルドを含む)。Actions のログを見て、途中で失敗していないか確認する。
4. 完了したら次の2つを確認する。
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
