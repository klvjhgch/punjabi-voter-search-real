# GitHub + Render deployment

1. Create a new empty GitHub repository, for example `punjabi-voter-search-real`.
2. Upload every file/folder from this project to the repository root. Do not put Dockerfile code into `server.js`.
3. In Render choose **New + Web Service**, connect the GitHub repository, and choose **Docker**.
4. The included `render.yaml` is also suitable for Blueprint deployment.
5. Set `ADMIN_PASSWORD` to your chosen admin password. Keep the generated `JWT_SECRET` secret.
6. Deploy. The health URL is `/health`.
7. Login with username `admin` and the `ADMIN_PASSWORD` you configured.
8. Open **Upload Voter List** and upload the real PDF.

The upload endpoint accepts the multipart field name `file` or `pdf`, so the previous Multer `Unexpected field` problem is intentionally prevented.

The application processes the PDF in a background worker. The browser polls Processing History, so a long OCR job does not require the HTTP upload request to stay open.
