# GraphQL client (`hcl_graphql`)

Small HTTP client for the [Hardcover.app](https://hardcover.app/) GraphQL API.

Bundled inside the **Hardcover Sync** Calibre plugin zip as `hcl_graphql/` (not
`graphql/`) to avoid clashing with the official Hardcover metadata plugin, which
also ships a `graphql` module.

Includes cross-process rate limiting (shared lock file) and 429 retry handling.

Tests live in `test/hcl_graphql/`.
