from flask import Flask, request, jsonify, render_template
import requests, os

app = Flask(__name__)

RASA_URL = os.getenv("RASA_URL", "http://localhost:5005/webhooks/rest/webhook")

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json(silent=True) or {}
    user_message = data.get("message", "").strip()
    sender_id = data.get("sender", "user")  # puedes pasar un session_id desde el front

    if not user_message:
        return jsonify({"error": "Mensaje vacío"}), 400

    try:
        resp = requests.post(
            RASA_URL,
            json={"sender": sender_id, "message": user_message},
            timeout=8
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        return jsonify({"error": f"Error connecting to Rasa: {e}"}), 502

    rasa_msgs = resp.json() if isinstance(resp.json(), list) else []

    bot_text = []
    buttons = []
    images = []
    custom = []

    for msg in rasa_msgs:
        if "text" in msg:
            bot_text.append(msg["text"])
        if "buttons" in msg and isinstance(msg["buttons"], list):
            buttons.extend(msg["buttons"])  # acumula
        if "image" in msg:
            images.append(msg["image"])
        if "custom" in msg:
            custom.append(msg["custom"])

    return jsonify({
        "bot_response": " ".join(bot_text).strip(),
        "buttons": buttons,
        "images": images,
        "custom": custom
    })

if __name__ == "__main__":
    # Para producción: debug=False
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8000)), debug=True)
