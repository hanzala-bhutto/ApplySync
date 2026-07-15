# ClickHouse analyzer fix

## Motivation
The Langfuse dashboard's Scores page should not 500.

## Problem
`clickhouse` was pinned to `latest`, which drifted to 26.6 during this session. ClickHouse 25.x+ defaults to a new query analyzer; Langfuse v3's generated SQL (the `scores.all` query) was written against the old one, and fails on 26.6 with "Not found column and(...)" - surfacing as a 500 on the Scores page.

## Solution
Pin the image to `26.6.1.1193` (stop future drift) and force the legacy analyzer via a mounted `users.d` config (`enable_analyzer=0`), matching what Langfuse's own SQL expects. Mounting a directory onto ClickHouse's `users.d` path replaces whatever the image's entrypoint would otherwise auto-generate there from `CLICKHOUSE_USER`/`CLICKHOUSE_PASSWORD` - including the file that defines the `clickhouse` user itself - so that user has to be redeclared in our own mounted config, and `CLICKHOUSE_SKIP_USER_SETUP=1` has to be set, or the entrypoint keeps overwriting our file with its own auto-generated (plaintext-password) version on every restart.

## Changes
- `langfuse/docker-compose.yml`: pinned ClickHouse image, `CLICKHOUSE_SKIP_USER_SETUP: "1"`, mounted `./clickhouse/users.d`
- `langfuse/clickhouse/users.d/langfuse-old-analyzer.xml`: forces `enable_analyzer=0`
- `langfuse/clickhouse/users.d/default-user.xml`: redeclares the `clickhouse` user, password sourced via `from_env="CLICKHOUSE_PASSWORD"` (never written in plaintext, since this file is committed to git)

## Benefits
- Scores page works again; verified live (auth via the env-var-sourced password, analyzer setting active, clean web logs) across a container restart.
- No secret ever lands in a tracked file - the password stays exactly where it already lived, in `langfuse/.env`.
