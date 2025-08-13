web: sh -lc "python -m gunicorn --bind 0.0.0.0:${PORT:-7860} --workers 2 --log-level info App:app"
rasa: sh -lc "rasa run -m models --enable-api --cors \"*\" --port 5005"
actions: sh -lc "rasa run actions --port 5055"