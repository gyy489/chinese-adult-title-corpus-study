# Local review applications

These browser interfaces were used inside the controlled research environment
for blind coding, post-completion comparison, and exact-span privacy review.
They bind to localhost and store item-level work in local SQLite files.

No queue, title, sealed reference, reviewer database, or item-level decision is
distributed here. The code is public so the review controls and aggregation
workflow can be inspected; running it requires a caller-supplied private queue
that satisfies the documented schema.
