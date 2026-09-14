FROM node:22-bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip tesseract-ocr tesseract-ocr-pan tesseract-ocr-eng poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY package*.json ./
RUN npm install --omit=dev
RUN python3 -m pip install --break-system-packages --no-cache-dir PyMuPDF pytesseract Pillow
RUN python3 -c "import fitz,pytesseract; from PIL import Image; print('PYTHON OCR DEPENDENCIES OK')"
COPY . .
RUN mkdir -p data uploads
ENV PORT=10000
EXPOSE 10000
CMD ["npm","start"]
