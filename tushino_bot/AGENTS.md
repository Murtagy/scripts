# Tushino bot maintenance

## Runtime and tests

- Support Python 3.10+. Dependencies live in `requirements.txt`.
- Run from this directory: `python -m unittest discover -s tests -v`.
- Keep parsing/rendering deterministic and separate from network/database I/O.
- Use synthetic fixtures. Never commit production API responses, chat exports, message-ID manifests, or database snapshots.

## Public repository and credentials

- Credentials come from environment variables or ignored `token.sh`; never hardcode them.
- Headquarters polling is optional and enabled by `HEADQUARTERS_ACCESS_TOKEN`.
- The headquarters token travels in a query parameter: do not log request URLs or raw HTTP exceptions containing it.
- Keep runtime databases, logs, replay state, keys, environment files, and credential backups out of Git.
- Before publishing, review the staged file list and scan staged changes for secrets. Do not force-add ignored runtime files.

## Headquarters contracts

- Poll only the four nearest game keys `(event_datetime, event_position)` in this or next Moscow week. Today is included because the API provides dates without start times.
- Respect the persistent 90-second request reservation, including manual API inspection and restarts.
- Topic names come from `PlanSummary.topic_name`: date, weekday/order label, mission name. Resolve names from dated public announcements and retain the cache during outages.
- Publish one editable DER-only plan post. Do not forward comments or append historical copies of plan fragments, arrangements, or slot lists.
- Ignore unused API fields such as slots/comments; their independent schema changes must not block plan updates.
- Use `screenshots.ally` / `screenshots.enemy` directly for uniforms, with full-size `image` URLs. Do not invert them again using `reversed`.
- Uniforms are separate ally/enemy albums, at most ten images each. Other plan images remain separate messages.
- Download image bytes only from approved HTTPS origins; reject redirects and enforce size limits.

## Telegram delivery state

- Persist IDs and desired/confirmed content. Invalidate confirmation before edits so a lost response followed by a revert is retried correctly.
- Preserve document-promotion intent for long plans; update the same post instead of sending another copy.
- Nested `InputFile` objects inside `InputMedia*` require `attach=True`. Preserve the real-SDK multipart regression test, not just mocked Bot calls.
- Persist all album receipts and superseded-photo cleanup atomically. Delete old photos only after successful replacement; respect `RetryAfter` across topic deliveries.
- Persist deletion-attempt uncertainty before I/O. Never reuse an ambiguously deleted ID as confirmed.
- Treat `plan_deliveries` as legacy history, not an outgoing text queue. Preserve its already-uploaded-image deduplication during migrations.

## Production operations

- Preserve current topic/message IDs unless recreation is explicitly requested.
- Bot API cannot enumerate chat history. Legacy messages without recorded IDs cannot safely be cleaned by guessing ID ranges.
- Before an authorized topic reset, back up SQLite consistently, cache/verify current images, and retain canonical plan/media content while clearing receipts tied to deleted threads.
- Back up a live WAL database through SQLite's backup API, not by copying only its main file.
- Deployment is service-managed (`deploy/tushino-bot.service`). Restart that service rather than launching a second process; do not restart the independently managed tunnel unnecessarily.
- Validate on the server's Python version and check health, delivery queues, and logs after deployment.
