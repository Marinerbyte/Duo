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
# Structure: {'bot1': BotInstance, 'bot2': BotInstance}
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
    """
    Uses Groq Llama-3-Instant to generate a human-like Hinglish reply.
    """
    if not client:
        return "Are bhai API key nahi hai."

    system_prompt = (
        "You are one real Indian user in a two-person chatroom conversation.\n\n"
        "You are NOT both users.\n"
        "You only respond as yourself.\n"
        "The other messages belong to a different person.\n\n"
        "Personality:\n"
        "Calm, casual, slightly laid-back.\n"
        "Type like a normal person using a phone.\n"
        "Imperfect and natural.\n"
        "Not dramatic. Not robotic.\n\n"
        "Conversation Rules:\n"
        "Reply in natural Hinglish.\n"
        "Only one sentence.\n"
        "Maximum 18 words.\n"
        "Do not repeat ideas already discussed.\n"
        "Do not rephrase or mirror the other person's sentence.\n"
        "Add something slightly new to move the conversation forward.\n"
        "No moral lectures.\n"
        "No over-explaining.\n"
        "No predictable structure.\n\n"
        "Context Awareness:\n"
        "If topic continues, progress it naturally.\n"
        "If topic repeats, change angle subtly.\n"
        "If nothing meaningful to add, reply short and neutral.\n"
        "Do not summarize past messages.\n\n"
        "Strict Avoid:\n"
        "Do not act like both users.\n"
        "Do not echo the previous message.\n"
        "Do not copy tone or structure exactly.\n"
        "Do not repeat wording.\n"
        "Do not explain yourself.\n"
        "Do not mention being AI.\n\n"
        "Previous conversation exists. Avoid repetition. Continue naturally."
    )
    try:
        # Construct message chain
        messages = [{"role": "system", "content": system_prompt}]
        
        # Add last 3 messages for context
        for msg in context_history[-3:]:
            messages.append({"role": "user", "content": msg})
            
        messages.append({"role": "user", "content": incoming_text})

        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant", # The fast model
            messages=messages,
            temperature=0.7,
            max_tokens=60,
            top_p=1,
            stream=False,
            stop=None,
        )
        reply = completion.choices[0].message.content
        return reply.replace('"', '').strip()
    except Exception as e:
        print(f"AI Error: {e}")
        return "Haa bhai sahi baat hai."

# =============================================================================
# 3. THE BOT CLASS
# =============================================================================
class ChatBot:
    def __init__(self, username, password, room, partner_name=None, auto_start=False):
        self.username = username
        self.password = password
        self.room = room
        self.partner_name = partner_name # The username of the OTHER bot
        self.token = ""
        self.user_id = ""
        self.room_id = ""
        self.ws = None
        self.running = False
        self.status = "INIT"
        self.auto_start = auto_start # If true, this bot starts the convo
        self.ua = random.choice(USER_AGENTS) # Assign a random phone signature
        self.conversation_history = []

    def log(self, msg):
        timestamp = time.strftime("%H:%M:%S")
        entry = f"[{timestamp}] [{self.username.upper()}]: {msg}"
        print(entry)
        CHAT_LOGS.append(entry)
        if len(CHAT_LOGS) > 50: CHAT_LOGS.pop(0)

    def login_and_start(self):
        self.running = True
        self.status = "LOGGING IN..."
        
        # 1. Login API
        url = "https://api.howdies.app/api/login"
        payload = {"username": self.username, "password": self.password}
        try:
            resp = requests.post(url, json=payload, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                # Handle different API response structures
                if "token" in data: self.token = data["token"]
                elif "data" in data and "token" in data["data"]: self.token = data["data"]["token"]
                
                if "id" in data: self.user_id = data["id"]
                elif "userId" in data: self.user_id = data["userId"]
                elif "data" in data and "id" in data["data"]: self.user_id = data["data"]["id"]
                
                if self.token:
                    self.status = "CONNECTING WS..."
                    self.connect_ws()
                else:
                    self.status = "LOGIN FAILED (No Token)"
            else:
                self.status = f"LOGIN ERROR {resp.status_code}"
        except Exception as e:
            self.status = f"NET ERROR: {e}"

    def connect_ws(self):
        ws_url = f"wss://app.howdies.app/howdies?token={self.token}"
        # Standard headers + User Agent to look like Mobile
        headers = {
            "User-Agent": self.ua,
            "Origin": "https://howdies.app"
        }
        self.ws = websocket.WebSocketApp(
            ws_url,
            header=headers,
            on_open=self.on_open,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close
        )
        self.ws.run_forever(sslopt={"cert_reqs": ssl.CERT_NONE})

    def on_open(self, ws):
        self.status = "CONNECTED"
        self.log("WebSocket Connected. Authenticating...")
        
        # Login Packet
        ws.send(json.dumps({"handler": "login", "username": self.username, "password": self.password}))
        time.sleep(1)
        
        # Join Room Packet
        ws.send(json.dumps({"handler": "joinchatroom", "id": str(time.time()), "name": self.room, "roomPassword": ""}))
        
        # Start Pinger
        threading.Thread(target=self.pinger, daemon=True).start()
        
        # If this bot is the "Starter", send the first message after a delay
        if self.auto_start:
            threading.Timer(6.0, self.trigger_first_message).start()

    def pinger(self):
        while self.running and self.ws and self.ws.sock and self.ws.sock.connected:
            time.sleep(25)
            try: self.ws.send(json.dumps({"handler": "ping"}))
            except: break

    def trigger_first_message(self):
        starters = ["Aur bhai kya haal hai?", "Oye sunna", "Kya chal rha hai bhai?", "Hello bhai kidhar hai?"]
        msg = random.choice(starters)
        self.send_msg(msg)

    def send_msg(self, text):
        if not self.ws: return
        # Target needs room_id if available, else name
        target = self.room_id if self.room_id else self.room
        pkt = {
            "handler": "chatroommessage",
            "id": str(time.time()),
            "type": "text",
            "roomid": target,
            "text": text,
            "length": "0"
        }
        try:
            self.ws.send(json.dumps(pkt))
            self.log(f"Sent: {text}")
            # Add my own text to history so context remains clean
            self.conversation_history.append(text)
        except Exception as e:
            self.log(f"Send Failed: {e}")

    def on_message(self, ws, message):
        try:
            data = json.loads(message)
            
            # Capture Room ID
            if data.get("handler") == "joinchatroom" and data.get("roomid"):
                self.room_id = data["roomid"]
            
            # Message Handling
            if data.get("handler") in ["chatroommessage", "message"]:
                sender = data.get("from") or data.get("username")
                msg_text = data.get("text") or data.get("body")
                
                if sender and msg_text:
                    # Ignore my own messages
                    if sender == self.username: return
                    
                    # Logic: Only reply if the sender is my Partner Bot
                    # This ensures they talk to each other and don't reply to randoms (unless you want that)
                    if self.partner_name and sender.lower() == self.partner_name.lower():
                        self.log(f"Heard from {sender}: {msg_text}")
                        
                        # Add to history
                        self.conversation_history.append(msg_text)
                        if len(self.conversation_history) > 10: self.conversation_history.pop(0)
                        
                        # Trigger Human-Like Reply Logic
                        threading.Thread(target=self.process_reply, args=(msg_text,)).start()
                        
        except Exception as e:
            pass

    def process_reply(self, incoming_text):
        # 1. Human Delay (Reading + Typing time)
        # Random delay between 4 to 10 seconds
        delay = random.uniform(4.5, 9.0)
        time.sleep(delay)
        
        # 2. Generate AI Reply
        reply = generate_hinglish_reply(incoming_text, self.conversation_history)
        
        # 3. Send
        self.send_msg(reply)

    def on_error(self, ws, error):
        self.log(f"Error: {error}")

    def on_close(self, ws, c, m):
        self.status = "DISCONNECTED"
        self.running = False
        self.log("Disconnected.")

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
    u1 = data.get('u1')
    u2 = data.get('u2')
    pwd = data.get('p')
    room = data.get('r')

    if not u1 or not u2 or not pwd or not room:
        return jsonify({"status": "error", "message": "All fields required"})

    with BOT_LOCK:
        # Stop existing if any
        for b in BOTS.values(): b.stop()
        BOTS.clear()

        # Init Bot 1 (The initiator)
        # We pass u2 as partner name so it knows who to reply to
        bot1 = ChatBot(u1, pwd, room, partner_name=u2, auto_start=True)
        
        # Init Bot 2 (The responder)
        # We pass u1 as partner name
        bot2 = ChatBot(u2, pwd, room, partner_name=u1, auto_start=False)

        BOTS['bot1'] = bot1
        BOTS['bot2'] = bot2

        # Start threads
        threading.Thread(target=bot1.login_and_start, daemon=True).start()
        # Small delay for Bot 2 so they don't hit login endpoint exactly same ms
        time.sleep(2)
        threading.Thread(target=bot2.login_and_start, daemon=True).start()

    return jsonify({"status": "success", "message": "Bots launching..."})

@app.route('/stop_bots', methods=['POST'])
def stop_bots():
    with BOT_LOCK:
        for b in BOTS.values():
            b.stop()
        BOTS.clear()
    return jsonify({"status": "success", "message": "Bots stopped."})

@app.route('/get_status')
def get_status():
    status_data = {}
    with BOT_LOCK:
        if 'bot1' in BOTS:
            status_data['bot1'] = f"{BOTS['bot1'].username}: {BOTS['bot1'].status}"
        else:
            status_data['bot1'] = "OFFLINE"
            
        if 'bot2' in BOTS:
            status_data['bot2'] = f"{BOTS['bot2'].username}: {BOTS['bot2'].status}"
        else:
            status_data['bot2'] = "OFFLINE"
            
    return jsonify({"bots": status_data, "logs": CHAT_LOGS[-15:]}) # Return last 15 logs

# =============================================================================
# 5. HTML DASHBOARD
# =============================================================================
DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>DuoChat Bot Controller</title>
    <style>
        body { background: #121212; color: #e0e0e0; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; display: flex; flex-direction: column; align-items: center; min-height: 100vh; margin: 0; }
        .container { background: #1e1e1e; padding: 25px; border-radius: 12px; box-shadow: 0 4px 15px rgba(0,0,0,0.5); width: 90%; max-width: 500px; margin-top: 30px; }
        h2 { text-align: center; color: #00e676; margin-bottom: 20px; text-transform: uppercase; letter-spacing: 2px; }
        .input-group { margin-bottom: 15px; }
        label { display: block; font-size: 0.9em; margin-bottom: 5px; color: #aaa; }
        input { width: 100%; padding: 12px; background: #2c2c2c; border: 1px solid #444; color: #fff; border-radius: 6px; box-sizing: border-box; font-size: 16px; }
        input:focus { outline: none; border-color: #00e676; }
        
        .row { display: flex; gap: 10px; }
        .col { flex: 1; }
        
        .btn-group { display: flex; gap: 10px; margin-top: 20px; }
        button { flex: 1; padding: 12px; border: none; border-radius: 6px; font-weight: bold; cursor: pointer; transition: 0.2s; font-size: 16px; }
        .btn-start { background: #00e676; color: #000; }
        .btn-start:hover { background: #00c853; }
        .btn-stop { background: #ff5252; color: #fff; }
        .btn-stop:hover { background: #ff1744; }
        
        .status-box { background: #000; padding: 10px; margin-top: 20px; border-radius: 6px; font-family: monospace; font-size: 12px; height: 150px; overflow-y: auto; border: 1px solid #333; }
        .bot-status { display: flex; justify-content: space-between; margin-top: 15px; font-size: 14px; font-weight: bold; }
        .status-led { width: 10px; height: 10px; border-radius: 50%; display: inline-block; background: #555; margin-right: 5px; }
        .on { background: #00e676; }
    </style>
</head>
<body>

    <div class="container">
        <h2>🤖 DuoChat AI</h2>
        
        <div class="row">
            <div class="col">
                <label>Username 1 (Bot A)</label>
                <input id="u1" placeholder="Enter Bot 1 Name">
            </div>
            <div class="col">
                <label>Username 2 (Bot B)</label>
                <input id="u2" placeholder="Enter Bot 2 Name">
            </div>
        </div>

        <div class="input-group" style="margin-top:10px;">
            <label>Common Password</label>
            <input id="p" type="password" placeholder="Password for both">
        </div>

        <div class="input-group">
            <label>Target Room</label>
            <input id="r" placeholder="Room Name to Join">
        </div>

        <div class="bot-status">
            <span id="st-b1"><div class="status-led" id="led-b1"></div> Bot 1: OFFLINE</span>
            <span id="st-b2"><div class="status-led" id="led-b2"></div> Bot 2: OFFLINE</span>
        </div>

        <div class="btn-group">
            <button class="btn-start" onclick="startBots()">LOGIN & START</button>
            <button class="btn-stop" onclick="stopBots()">LOGOFF</button>
        </div>

        <div class="status-box" id="logs">
            [System]: Ready to connect...
        </div>
    </div>

    <script>
        function log(msg) {
            const box = document.getElementById('logs');
            box.innerHTML += `<div>${msg}</div>`;
            box.scrollTop = box.scrollHeight;
        }

        function startBots() {
            const u1 = document.getElementById('u1').value;
            const u2 = document.getElementById('u2').value;
            const p = document.getElementById('p').value;
            const r = document.getElementById('r').value;
            
            if(!u1 || !u2 || !p || !r) return alert("Please fill all fields!");

            fetch('/start_bots', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({u1, u2, p, r})
            }).then(res => res.json()).then(data => {
                log(data.message);
            });
        }

        function stopBots() {
            fetch('/stop_bots', {method: 'POST'}).then(res => res.json()).then(data => {
                log(data.message);
            });
        }

        setInterval(() => {
            fetch('/get_status').then(r => r.json()).then(d => {
                // Update text
                document.getElementById('st-b1').innerText = d.bots.bot1;
                document.getElementById('st-b2').innerText = d.bots.bot2;
                
                // Update Logs
                const logBox = document.getElementById('logs');
                logBox.innerHTML = d.logs.map(l => `<div>${l}</div>`).join('');
                logBox.scrollTop = logBox.scrollHeight;
            });
        }, 2000);
    </script>

</body>
</html>
"""

# =============================================================================
# 6. RUNNER
# =============================================================================
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
