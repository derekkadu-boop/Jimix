# Jimix

## API appendix

FastAPI serves the full request and response schemas at `/openapi.json`. Open
`/docs` to execute a call and inspect its exact HTTP status and response body.
Portal checks run in the background on creation, so a subsequent response may
show a different status from the creation response.

### Tender fields

| JSON field | Value |
| --- | --- |
| `id` | Integer identifier assigned by Jimix for the stored entry. |
| `portal_id` | Integer identifier of the portal that supplied the tender. |
| `reference` | Tender identifier supplied by the feed. |
| `address` | HTTP(S) link from the tender notice, or `null`. |
| `url` | Compatibility alias of `address`. |
| `title` | Tender title. |
| `buyerName` | Name from the release buyer or its matching party, or `null`. |
| `submissionOpensAt` | Submission period start as a timezone-aware ISO 8601 timestamp, or `null`. |
| `submissionDeadline` | Submission period end as a timezone-aware ISO 8601 timestamp, or `null`. |
| `lastChangedAt` | Timestamp of the newest release containing the tender. |
| `status` | `"open"` for an active tender whose known deadline has yet to pass. |
| `documents` | Array of objects with `fileName` and `url`. An empty array is valid. |

### Calls and responses

| Call | Success response | Other responses |
| --- | --- | --- |
| `GET /` | 200, object with `portal_count`, `ok_count`, `error_count`, `tender_count`, `portals`, and `recent_runs` | |
| `GET /health` | 200, `{"status":"ok"}` | |
| `GET /portals` | 200, array of Portal objects | |
| `POST /portals` with `{"address":"https://example.org/feed.json"}` | 201, Portal object with status `integrating` | 409, `{"detail":"Portal already exists"}`. 422 for invalid input. |
| `GET /portals/{portal_id}` | 200, Portal object | 404 or 422 |
| `POST /portals/{portal_id}/refresh` | 200, Portal object after the check | 404 or 422 |
| `GET /portals/{portal_id}/tenders` | 200, array of Tender objects | 404 or 422 |
| `GET /portals/{portal_id}/runs` | 200, array of Run objects | 404 or 422 |
| `DELETE /portals/{portal_id}` | 204, empty body | 404 or 422 |
| `GET /api/items` | 200, array of Item objects | |
| `POST /api/items` with `{"name":"Example","description":""}` | 201, Item object | 422 for invalid input |
| `GET /api/items/{item_id}` | 200, Item object | 404 or 422 |
| `DELETE /api/items/{item_id}` | 204, empty body | 404 or 422 |

A missing portal returns `{"detail":"Portal not found"}`. A missing item
returns `{"detail":"Item not found"}`. Validation errors use FastAPI's 422
response with a `detail` array describing each invalid input.
