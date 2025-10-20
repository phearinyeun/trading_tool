from flask import Flask, jsonify, request
from datetime import datetime

app = Flask(__name__)
latest_signal = {}

@app.route("/signals/update", methods=["POST"])
def update_signal():
    global latest_signal
    latest_signal = request.json
    latest_signal["timestamp"] = datetime.now().isoformat()
    print("✅ Signal updated:", latest_signal)
    return jsonify({"status": "ok"})

@app.route("/signals/latest", methods=["GET"])
def get_latest_signal():
    return jsonify(latest_signal)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
