FROM python:3.10-slim


RUN apt-get update && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
	rasa==3.6.* \
	rasa-sdk==3.6.* \
	flask>=2.2 \
	gunicorn>=21.2 \
	honcho>=1.1 \
	requests>=2.31

WORKDIR /app

COPY . /app

ENV RASA_TELEMETRY_ENABLED=false

#Flask hablará con Rasa dentro del contenedor:
ENV RASA_URL=http://127.0.0.1:5005/webhooks/rest/webhook

# Puerto por defecto en local (HF/Render lo inyectan en prod):
ENV PORT=7860

CMD ["honcho","start"]
