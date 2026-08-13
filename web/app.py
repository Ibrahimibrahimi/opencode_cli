from flask import Flask, request, jsonify, render_template
import httpx
import uuid

app = Flask(__name__)

# ── Model ────────────────────────────────────────────────────────────────────

class Model:
    def __init__(self):
        self.model    = "big-pickle"
        self.api_key  = "Bearer public"
        self.base_url = "https://opencode.ai/zen/v1/chat/completions"
        self.uuid     = uuid.uuid4().hex[:20]
        self.headers  = {
            "Authorization"    : self.api_key,
            "Content-Type"     : "application/json",
            "x-opencode-client": "cli",
            "x-opencode-project": "global",
            "x-opencode-request": f"msg_{self.uuid}",
            "x-opencode-session": f"ses_{self.uuid}",
            "User-Agent"       : "opencode/1.15.0",
        }
        self.messages = [
            {"role": "system", "content": "The user is called Ibrahim."}
        ]

    def ask(self, question: str) -> str:
        self._add_user(question)
        response = httpx.post(
            self.base_url,
            headers=self.headers,
            json={"model": self.model, "messages": self.messages},
            timeout=60,
        )
        data = response.json()
        answer = data["choices"][0]["message"]["content"]
        self._add_assistant(answer)
        return answer

    def _add_user(self, q: str):
        self.messages.append({"role": "user", "content": q})

    def _add_assistant(self, q: str):
        self.messages.append({"role": "assistant", "content": q})

    def reset(self):
        self.messages = [
            {"role": "system", "content": "The user is called Ibrahim."}
        ]
        self.uuid = uuid.uuid4().hex[:20]
        self.headers["x-opencode-request"] = f"msg_{self.uuid}"
        self.headers["x-opencode-session"]  = f"ses_{self.uuid}"


model = Model()

# ── HTML ─────────────────────────────────────────────────────────────────────


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/ask", methods=["POST"])
def ask():
    data = request.get_json()
    question = (data or {}).get("question", "").strip()
    if not question:
        return jsonify({"error": "empty question"}), 400
    try:
        answer = model.ask(question)
        return jsonify({"answer": answer})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/reset", methods=["POST"])
def reset():
    model.reset()
    return jsonify({"ok": True})

if __name__ == "__main__":
    app.run(debug=True, port=5000)
