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

# Global dictionary to store bot instances
BOTS = {}
BOT_LOCK = threading.Lock()
CHAT_LOGS = []

# Mobile User Agents to mimic real phones
USER_AGENTS = [
    "Mozilla/5.0 (Linux; Android 10; SM-G960U) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.77 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 11; Pixel 5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/92.0.4515.159 Mobile Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 14_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.1.1 Mobile/15E148 Safari/604.1"
]

# =============================================================================
# 2. AI & GENERATION LOGIC (Hinglish)
# =============================================================================
def generate_hinglish_reply(incoming_text, context_history):
    if not client:
        return "Are bhai API key nahi hai."

    system_prompt = (
        "You are one real Indian user in a two-person chatroom conversation.\n"
        "Personality: Calm, casual, natural Hinglish. Type like a normal person on a phone.\n"
        "Rules: Only one sentence, max 18 words. Don't repeat what the other person said.\n"
        "No moral lectures, no robotic tone, no mentioning AI."
    )
    try:
        messages = [{"role": "system", "content": system_prompt}]
        for msg in context_history[-3:]:
            messages.append({"role": "user", "content": msg})
        messages.append({"role": "user", "content": incoming_text})

        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=messages,
            temperature=0.7,
            max_tokens=60,
        )
        reply = completion.choices[0].message.content
        return reply.replace('"', '').strip()
    except Exception as e:
        return "Haa bhai sahi baat hai."

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
        self.conversation_history = []

    def log(self, msg):
        timestamp = time.strftime("%H:%M:%S")
        entry = f"[{timestamp}] [{self.username.upper()}]: {msg}"
        CHAT_LOGS.append(entry)
        if len(CHAT_LOGS) > 50: CHAT_LOGS.pop(0)

    def login_and_start(self):
        self.running = True
        self.status = "LOGGING IN..."
        url = "https://api.howdies.app/api/login"
        payload = {"username": self.username, "password": self.password}
        try:
            resp = requests.post(url, json=payload, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                # Extract token & id logic
                self.token = data.get("token") or (data.get("data", {}).get("token"))
                if self.token:
                    self.status = "CONNECTING..."
                    self.connect_ws()
                else: self.status = "LOGIN FAILED"
            else: self.status = f"HTTP ERROR {resp.status_code}"
        except Exception as e:
            self.status = "NET ERROR"

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
        self.status = "CONNECTED"
        self.log("Online")
        ws.send(json.dumps({"handler": "login", "username": self.username, "password": self.password}))
        time.sleep(1)
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
        starters = ["Aur bhai kya haal hai?", "Oye sunna", "Kya chal rha hai bhai?", "Hello bhai kidhar hai?"]
        self.send_msg(random.choice(starters))

    def send_msg(self, text):
        if not self.ws: return
        target = self.room_id if self.room_id else self.room
        pkt = {"handler": "chatroommessage", "id": str(time.time()), "type": "text", "roomid": target, "text": text, "length": "0"}
        try:
            self.ws.send(json.dumps(pkt))
            self.log(f"Sent: {text}")
            self.conversation_history.append(text)
        except: pass

    def on_message(self, ws, message):
        try:
            data = json.loads(message)
            if data.get("handler") == "joinchatroom": self.room_id = data.get("roomid")
            if data.get("handler") in ["chatroommessage", "message"]:
                sender = data.get("from") or data.get("username")
                msg_text = data.get("text") or data.get("body")
                if sender and msg_text and sender.lower() == self.partner_name.lower():
                    self.conversation_history.append(msg_text)
                    threading.Thread(target=self.process_reply, args=(msg_text,)).start()
        except: pass

    def process_reply(self, incoming_text):
        time.sleep(random.uniform(5.0, 10.0))
        reply = generate_hinglish_reply(incoming_text, self.conversation_history)
        self.send_msg(reply)

    def on_error(self, ws, error): self.status = "ERROR"
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

        # Bot Setup
        bot1 = ChatBot(u1, pwd, room, partner_name=u2, auto_start=True)
        bot2 = ChatBot(u2, pwd, room, partner_name=u1, auto_start=False)
        BOTS['bot1'], BOTS['bot2'] = bot1, bot2

        # Start Bot 1
        threading.Thread(target=bot1.login_and_start, daemon=True).start()
        
        # --- THE 10 SECOND GAP ---
        # Bot 1 ke baad Bot 2 ke login mein 10 second ka distance
        time.sleep(10) 
        
        # Start Bot 2
        threading.Thread(target=bot2.login_and_start, daemon=True).start()

    return jsonify({"status": "success", "message": "Login distance active (10s). Bots launching..."})

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
    return jsonify({"bots": status_data, "logs": CHAT_LOGS[-15:]})

# =============================================================================
# 5. HTML DASHBOARD
# =============================================================================
DASHBOARD_HTML = """
<!DOCTYPE html>
<html>
<head>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>DuoChat Control</title>
    <style>
        body { background: #121212; color: #e0e0e0; font-family: sans-serif; display: flex; flex-direction: column; align-items: center; padding: 20px; }
        .container { background: #1e1e1e; padding: 20px; border-radius: 10px; width: 100%; max-width: 400px; }
        input { width: 100%; padding: 10px; margin: 8px 0; background: #2c2c2c; border: 1px solid #444; color: #fff; border-radius: 5px; box-sizing: border-box; }
        button { width: 48%; padding: 12px; border-radius: 5px; border: none; font-weight: bold; cursor: pointer; }
        .btn-start { background: #00e676; color: #000; }
        .btn-stop { background: #ff5252; color: #fff; }
        .status-box { background: #000; padding: 10px; margin-top: 20px; border-radius: 5px; font-family: monospace; font-size: 11px; height: 180px; overflow-y: auto; border: 1px solid #333; }
        .bot-info { display: flex; justify-content: space-between; font-size: 13px; margin: 10px 0; color: #00e676; }
    </style>
</head>
<body>
    <div class="container">
        <h3 style="text-align:center;">DuoChat AI Bot</h3>
        <input id="u1" placeholder="Username Bot A">
        <input id="u2" placeholder="Username Bot B">
        <input id="p" type="password" placeholder="Password">
        <input id="r" placeholder="Room Name">
        
        <div class="bot-info">
            <span id="st-b1">Bot 1: OFFLINE</span>
            <span id="st-b2">Bot 2: OFFLINE</span>
        </div>

        <div style="display:flex; justify-content: space-between;">
            <button class="btn-start" onclick="startBots()">START</button>
            <button class="btn-stop" onclick="stopBots()">STOP</button>
        </div>

        <div class="status-box" id="logs">Logs will appear here...</div>
    </div>

    <script>
        function startBots() {
            const data = { u1: document.getElementById('u1').value, u2: document.getElementById('u2').value, p: document.getElementById('p').value, r: document.getElementById('r').value };
            fetch('/start_bots', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data) });
        }
        function stopBots() { fetch('/stop_bots', {method: 'POST'}); }
        setInterval(() => {
            fetch('/get_status').then(r => r.json()).then(d => {
                document.getElementById('st-b1').innerText = d.bots.bot1;
                document.getElementById('st-b2').innerText = d.bots.bot2;
                document.getElementById('logs').innerHTML = d.logs.map(l => `<div>${l}</div>`).join('');
            });
        }, 2000);
    </script>
</body>
</html>
"""

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
