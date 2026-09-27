# ADR-0007: ホスティング(Azure 日本リージョン)

**Status:** Proposed(本文は未作成。Azure でホスティングすることだけは決定済み)
**Date:** 2026-09-21

## Context

本線(ADR-0002〜0006)は Azure(Container Apps または App Service、Azure OpenAI、Blob Storage、PostgreSQL、Entra ID の Easy Auth)を前提にしている。当初の検討(HANDOFF.md)でホスティング先は Azure の日本リージョンと決めたが、構成の詳細(サービスの選択、Bicep の構成、リージョン、コスト)は書き起こしていない。

## Decision

- ホスティング先は Azure。リージョンは日本(Japan East / Japan West)。
- 詳細は本線に着手するときに、この ADR を書き直して決める。着手前の未解決事項は gpt-image-2.5 の日本リージョンでの提供可否、閲覧範囲、保存期間、想定利用量(CLAUDE.md)。

## Consequences

- ローカルMVP(ADR-0008)と Docker(ADR-0016)は、この ADR とは独立に動く。
- 認証はローカル線では ADR-0019(OIDC)、Azure 線では ADR-0006(Easy Auth)を想定する。両立の方法は本線に着手するときに決める。
