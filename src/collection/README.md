# Collection stage

This directory contains the final reusable collection engine used by the
project: configurable selectors, domain and URL filtering, pagination,
request/Playwright fetch modes, deterministic deduplication, resumable file and
MySQL storage, status inspection, import, and export.

The public release deliberately omits the production source configuration,
source names, source URLs, credentials, crawl snapshots, and record-level
exports. [`config/sites.public.example.json`](../../config/sites.public.example.json)
and [`examples/synthetic/collection_page.html`](../../examples/synthetic/collection_page.html)
provide a harmless local example of the configuration and parser interface.
The two retained acquisition rounds and their pseudonymous source coverage are
reported in [`config/collection_rounds_public.json`](../../config/collection_rounds_public.json).

This is runnable code, not a crawl-history log. It does not make network
requests during tests.
