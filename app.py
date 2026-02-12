import os
import json
import time
import threading
import random
import uuid
import websocket
import ssl
import requests
from flask import Flask, render_template_string, request, jsonify
from groq import Groq

# Initialize Flask
app = Flask(__name__)

# =============================================================================
# 1. CONFIG & GLOBALS
# =============================================================================
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
client = None
if GROQ_API_KEY:
    client = Groq(api_key=GROQ_API_KEY)

BOTS = {}
BOT_LOCK = threading.Lock()
CHAT_LOGS = []

USER_AGENTS = [
    "Mozilla/5.0 (Linux; Android 10; SM-G960U) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.77 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 11; Pixel 5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/92.0.4515.159 Mobile Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 14_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.1.1 Mobile/15E148 Safari/604.1"
]

# =============================================================================
# 2. AI & GENERATION LOGIC (Refined Role Structure)
# =============================================================================
def generate_hinglish_reply(my_username, context_history):
    """
    Role-based context handling: 
    - My previous messages = 'assistant'
    - Partner's messages = 'user'
    """
    if not client:
        return "Bhai API key set karle pehle."

    system_prompt = (
        "You are a real Indian user in a private 1-on-1 chatroom.\n"
        "Your task: Reply to the other user naturally.\n\n"
        "PERSONALITY:\n"
        "- Casual, slightly lazy, natural Hinglish (Hindi + English mix).\n"
        "- Use lower-case sometimes, just like mobile typing.\n"
        "- Be chill, not over-excited.\n\n"
        "STRICT CONSTRAINTS:\n"
        "- Only ONE sentence per reply.\n"
        "- Maximum 18 words.\n"
        "- DO NOT echo or repeat what the other person said.\n"
        "- DO NOT start sentences with the same words used previously.\n"
        "- DO NOT explain that you are an AI.\n"
        "- Never act as both users. You are ONLY the Assistant in this conversation.\n\n"
        "CONTEXT HANDLING:\n"
        "- Read the chat history carefully to see the flow.\n"
        "- If the topic is finished, start a very brief new one or give a neutral closing."
    )

    try:
        messages = [{"role": "system", "content": system_prompt}]
        
        # Build structured history: Assign roles based on who sent the message
        # history structure: [{'sender': 'name', 'text': 'content'}, ...]
        for entry in context_history:
            role = "assistant" if entry['sender'] == my_username else "user"
            messages.append({"role": role, "content": entry['text']})

        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=messages,
            temperature=0.8, # Thoda randomness for natural feel
            max_tokens=60,
            top_p=0.9
        )
        reply = completion.choices[0].message.content
        return reply.replace('"', '').strip()
    except Exception as e:
        print(f"AI Error: {e}")
        return "Hmm, sahi keh raha hai."

# =============================================================================
# 3. THE BOT CLASS
# =============================================================================
class ChatBot:
    def __init__(self, username, password, room, partner_name=None, auto_start=False):
        self.username = username
        self.password = password
        self.room = room
        self.partner_name = partner_name 
        self.token = ""
        self.user_id = ""
        self.room_id = ""
        self.ws = None
        self.running = False
        self.status = "INIT"
        self.auto_start = auto_start 
        self.ua = random.choice(USER_AGENTS) 
        # Structured history to store roles
        self.conversation_history = [] 

    def log(self, msg):
        timestamp = time.strftime("%H:%M:%S")
        entry = f"[{timestamp}] [{self.username.upper()}]: {msg}"
        CHAT_LOGS.append(entry)
        if len(CHAT_LOGS) > 60: CHAT_LOGS.pop(0)

    def login_and_start(self):
        self.running = True
        self.status = "LOGGING IN..."
        url = "https://api.howdies.app/api/login"
        payload = {"username": self.username, "password": self.password}
        try:
            resp = requests.post(url, json=payload, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                self.token = data.get("token") or data.get("data", {}).get("token")
                if self.token:
                    self.status = "CONNECTING..."
                    self.connect_ws()
                else: self.status = "NO TOKEN"
            else: self.status = f"LOGIN ERROR {resp.status_code}"
        except Exception as e: self.status = f"NET ERROR"

    def connect_ws(self):
        ws_url = f"wss://app.howdies.app/howdies?token={self.token}"
        headers = {"User-Agent": self.ua, "Origin": "https://howdies.app"}
        self.ws = websocket.WebSocketApp(
            ws_url, header=headers,
            on_open=self.on_open, on_message=self.on_message,
            on_error=self.on_error, on_close=self.on_close
        )
        self.ws.run_forever(sslopt={"cert_reqs": ssl.CERT_NONE})

    def on_open(self, ws):
        self.status = "ONLINE"
        self.log("Connected to WebSocket.")
        ws.send(json.dumps({"handler": "login", "username": self.username, "password": self.password}))
        time.sleep(1.5)
        ws.send(json.dumps({"handler": "joinchatroom", "id": str(time.time()), "name": self.room, "roomPassword": ""}))
        threading.Thread(target=self.pinger, daemon=True).start()
        
        if self.auto_start:
            threading.Timer(8.0, self.trigger_first_message).start()

    def pinger(self):
        while self.running and self.ws and self.ws.sock and self.ws.sock.connected:
            time.sleep(25)
            try: self.ws.send(json.dumps({"handler": "ping"}))
            except: break

    def trigger_first_message(self):
        starters = ["Aur bhai kya haal hain?", "Kya chal raha hai aaj kal?", "Oye free hai kya?", "Bhai ek baat bata"]
        msg = random.choice(starters)
        self.send_msg(msg)

    def send_msg(self, text):
        if not self.ws: return
        target = self.room_id if self.room_id else self.room
        pkt = {"handler": "chatroommessage", "id": str(time.time()), "type": "text", "roomid": target, "text": text, "length": "0"}
        try:
            self.ws.send(json.dumps(pkt))
            self.log(f"Sent: {text}")
            # Add to history as ASSISTANT role for this bot
            self.conversation_history.append({"sender": self.username, "text": text})
            if len(self.conversation_history) > 10: self.conversation_history.pop(0)
        except: pass

    def on_message(self, ws, message):
        try:
            data = json.loads(message)
            if data.get("handler") == "joinchatroom": self.room_id = data.get("roomid")
            if data.get("handler") in ["chatroommessage", "message"]:
                sender = data.get("from") or data.get("username")
                msg_text = data.get("text") or data.get("body")
                
                if sender and msg_text and sender != self.username:
                    if self.partner_name and sender.lower() == self.partner_name.lower():
                        self.log(f"Message from {sender}: {msg_text}")
                        # Add to history as USER role
                        self.conversation_history.append({"sender": sender, "text": msg_text})
                        if len(self.conversation_history) > 10: self.conversation_history.pop(0)
                        
                        threading.Thread(target=self.process_reply).start()
        except: pass

    def process_reply(self):
        # Typing delay
        time.sleep(random.uniform(6.0, 11.0))
        # Pass full structured history
        reply = generate_hinglish_reply(self.username, self.conversation_history)
        self.send_msg(reply)

    def on_error(self, ws, error): self.log(f"WS Error: {error}")
    def on_close(self, ws, c, m): 
        self.status = "OFFLINE"
        self.running = False

    def stop(self):
        self.running = False
        if self.ws: self.ws.close()

# =============================================================================
# 4. WEB DASHBOARD & ROUTES
# =============================================================================

@app.route('/')
def index():
    return render_template_string(DASHBOARD_HTML)

@app.route('/start_bots', methods=['POST'])
def start_bots():
    data = request.json
    u1, u2, pwd, room = data.get('u1'), data.get('u2'), data.get('p'), data.get('r')

    if not all([u1, u2, pwd, room]):
        return jsonify({"status": "error", "message": "All fields required"})

    with BOT_LOCK:
        for b in BOTS.values(): b.stop()
        BOTS.clear()

        bot1 = ChatBot(u1, pwd, room, partner_name=u2, auto_start=True)
        bot2 = ChatBot(u2, pwd, room, partner_name=u1, auto_start=False)
        BOTS['bot1'], BOTS['bot2'] = bot1, bot2

        # Start Bot 1
        threading.Thread(target=bot1.login_and_start, daemon=True).start()
        
        # --- THE 10 SECOND GAP ---
        print(f"[System] Bot 1 started. Waiting 10s for Bot 2...")
        time.sleep(10)
        
        # Start Bot 2
        threading.Thread(target=bot2.login_and_start, daemon=True).start()

    return jsonify({"status": "success", "message": "Bots launching with 10s gap and structured AI logic."})

@app.route('/stop_bots', methods=['POST'])
def stop_bots():
    with BOT_LOCK:
        for b in BOTS.values(): b.stop()
        BOTS.clear()
    return jsonify({"status": "success", "message": "Bots stopped."})

@app.route('/get_status')
def get_status():
    status_data = {}
    with BOT_LOCK:
        status_data['bot1'] = f"{BOTS['bot1'].username}: {BOTS['bot1'].status}" if 'bot1' in BOTS else "OFFLINE"
        status_data['bot2'] = f"{BOTS['bot2'].username}: {BOTS['bot2'].status}" if 'bot2' in BOTS else "OFFLINE"
    return jsonify({"bots": status_data, "logs": CHAT_LOGS[-18:]})

# =============================================================================
# 5. HTML DASHBOARD (FULL STYLED)
# =============================================================================
DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>DuoChat Pro AI</title>
    <style>
        body { background: #0f0f0f; color: #cfcfcf; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; display: flex; flex-direction: column; align-items: center; padding: 20px; }
        .card { background: #1a1a1a; padding: 25px; border-radius: 15px; box-shadow: 0 10px 30px rgba(0,0,0,0.7); width: 100%; max-width: 450px; border: 1px solid #333; }
        h2 { text-align: center; color: #00ff88; margin-bottom: 25px; text-transform: uppercase; letter-spacing: 3px; }
        .input-box { margin-bottom: 15px; }
        label { display: block; font-size: 0.8em; margin-bottom: 5px; color: #888; text-transform: uppercase; }
        input { width: 100%; padding: 12px; background: #252525; border: 1px solid #444; color: #fff; border-radius: 8px; box-sizing: border-box; outline: none; transition: 0.3s; }
        input:focus { border-color: #00ff88; box-shadow: 0 0 10px rgba(0,255,136,0.2); }
        .status-area { display: flex; justify-content: space-between; margin: 20px 0; padding: 10px; background: #000; border-radius: 8px; border: 1px solid #333; }
        .status-text { font-size: 12px; font-weight: bold; color: #00ff88; }
        .btn-row { display: flex; gap: 10px; }
        button { flex: 1; padding: 13px; border-radius: 8px; border: none; font-weight: bold; cursor: pointer; transition: 0.2s; text-transform: uppercase; }
        .btn-start { background: #00ff88; color: #000; }
        .btn-start:hover { background: #00cc6e; transform: translateY(-2px); }
        .btn-stop { background: #ff4d4d; color: #fff; }
        .btn-stop:hover { background: #e60000; transform: translateY(-2px); }
        .log-container { margin-top: 25px; background: #050505; border-radius: 8px; padding: 12px; height: 200px; overflow-y: auto; border: 1px solid #222; font-family: 'Courier New', monospace; font-size: 11px; line-height: 1.5; }
        .log-entry { margin-bottom: 5px; border-bottom: 1px solid #111; padding-bottom: 2px; }
    </style>
</head>
<body>
    <div class="card">
        <h2>DuoChat Pro</h2>
        <div style="display:flex; gap:10px;">
            <div class="input-box" style="flex:1;">
                <label>Bot A</label>
                <input id="u1" placeholder="User 1">
            </div>
            <div class="input-box" style="flex:1;">
                <label>Bot B</label>
                <input id="u2" placeholder="User 2">
            </div>
        </div>
        <div class="input-box">
            <label>Common Password</label>
            <input id="p" type="password" placeholder="••••••••">
        </div>
        <div class="input-group">
            <label>Target Room</label>
            <input id="r" placeholder="Room Name">
        </div>

        <div class="status-area">
            <div id="st-b1" class="status-text">A: OFFLINE</div>
            <div id="st-b2" class="status-text">B: OFFLINE</div>
        </div>

        <div class="btn-row">
            <button class="btn-start" onclick="startBots()">Launch Bots</button>
            <button class="btn-stop" onclick="stopBots()">Shutdown</button>
        </div>

        <div class="log-container" id="logs">
            <div class="log-entry">[System] Waiting for user action...</div>
        </div>
    </div>

    <script>
        function startBots() {
            const payload = { 
                u1: document.getElementById('u1').value, 
                u2: document.getElementById('u2').value, 
                p: document.getElementById('p').value, 
                r: document.getElementById('r').value 
            };
            if(!payload.u1 || !payload.u2 || !payload.p || !payload.r) return alert("Fill everything!");
            fetch('/start_bots', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload)
            });
        }
        function stopBots() { fetch('/stop_bots', {method: 'POST'}); }
        
        setInterval(() => {
            fetch('/get_status').then(r => r.json()).then(d => {
                document.getElementById('st-b1').innerText = d.bots.bot1;
                document.getElementById('st-b2').innerText = d.bots.bot2;
                const logBox = document.getElementById('logs');
                logBox.innerHTML = d.logs.map(l => `<div class="log-entry">${l}</div>`).join('');
                logBox.scrollTop = logBox.scrollHeight;
            });
        }, 2000);
    </script>
</body>
</html>
"""

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
