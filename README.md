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


## OCR reliability update
- Embedded-text PDFs are parsed first.
- Scanned/image-only pages fall back to Punjabi+English Tesseract OCR.
- Marker strings such as `16/20/xxx` and `ws-xxxx` are never treated as serial or part numbers.
- The boxed/standalone numeric serial is the record key.
- OCR failures are returned to Processing History with the worker error instead of a generic JSON failure.
- Photo requests accept the authenticated browser token used by the search UI.


## Performance update
- Embedded-text voter cards use the PDF text layer first and avoid per-card Tesseract OCR when a plausible name is already available.
- Scanned pages use a single lower-DPI Punjabi+English OCR pass to locate serial markers, then limited card OCR only for the detected cards.
- Conservative parallel card OCR and configurable OCR environment variables reduce CPU/RAM pressure on Render.
- Worker progress is reported to the server, so the UI no longer artificially stops at 88%; it shows the worker stage/page and reaches 100% only after database insertion completes.
- OCR worker timeout is 20 minutes with a clear Processing History error.

## V4 field extraction
For the supplied Punjab electoral-roll layout, Punjabi name/relative fields are restored with one page-level Punjabi OCR pass while EPIC, house, age and serial use the PDF text geometry. Booth No. is used as Part No. and voter photo coordinates are preserved.
