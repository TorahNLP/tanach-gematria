FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Pre-build the gematria database so cold starts skip the cipher computation.
# tanach.db is baked into the image; startup restores it into memory (~1-2s).
RUN python app.py builddb || echo "Pre-build skipped — will compute at runtime"

EXPOSE 7860

CMD ["streamlit", "run", "app.py", \
     "--server.port=7860", \
     "--server.address=0.0.0.0", \
     "--server.headless=true"]
