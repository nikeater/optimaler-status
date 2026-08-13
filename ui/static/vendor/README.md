# Vendored front-end assets

Vendored, not fetched from a CDN. A public-administration deployment cannot
assume outbound internet access from the browser, and a third-party CDN in the
page is a third party in the audit trail.

| File | Upstream | Version | License |
|---|---|---|---|
| `htmx.min.js` | https://unpkg.com/htmx.org@2.0.4/dist/htmx.min.js | 2.0.4 | 0BSD (`htmx-LICENSE.txt`) |

Updating: download the new `dist/htmx.min.js` and its `LICENSE`, replace both
files, bump the version above, and re-run `pytest`.

The metrics panel degrades gracefully: with htmx absent or JavaScript disabled,
the "Neu laden" control is an ordinary link to `GET /metrics`.
