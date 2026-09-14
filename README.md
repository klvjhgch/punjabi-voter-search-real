# Punjabi Voter Search — Real Production Project

A responsive voter-list management/search system designed for real PDF voter rolls.

## Extraction rules
- Booth Number and Part Number are the same identifier.
- The boxed number on each voter card is the Serial Number.
- Marker strings such as `16/20/xxx` and `ws-xxxx` are ignored as identifiers.
- Records are sorted by actual Serial Number.
- Punjabi voter/relative names are OCR'd from the visual voter card; structured fields come from the PDF text layer where available.
- Voter photos are cropped from the original PDF card coordinates.

## Deploy
Push the repository to GitHub, connect it to Render as a Docker web service, and set `ADMIN_PASSWORD` in Render. A persistent disk is configured at `/var/data`.

Default admin username: `admin`.
