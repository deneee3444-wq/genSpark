#!/usr/bin/env python3
"""
Genspark AI - Flask Web Interface
=============================================================================
Use-AI arayüzü ile uyumlu, Genspark AI çoklu model destekli web uygulaması.
- 31+ Resmi Model + Mixture-of-Agents (MoA) tam desteği
- Start - Stop (canlı akışı durdurma ve geçmişi koruma) mantığı
- Kredi bitince sıradaki hesaba geçiş (genspark_hesaplar.txt) veya otomatik yeni hesap açma
- PROXY_MODE (0: Kapalı, 1: Açık)
"""

import base64
import datetime
import json
import mimetypes
import os
import random
import re
import ssl
import string
import sys
import threading
import time
import traceback
import urllib.parse
import uuid
import functools
from typing import Dict, Generator, List, Optional, Tuple

# Windows konsol UTF-8 desteği ve Satır Tamponlama (Line Buffering)
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except Exception:
        pass

# Tüm print çağrılarının anında PowerShell/Terminal ekranına basılması garanti edilir (Buffer beklemez)
print = functools.partial(print, flush=True)

def log(msg: str):
    """Konsola anında, zaman damgalı log basar."""
    now_str = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{now_str}] {msg}", flush=True)

import requests as py_requests
from curl_cffi import requests as cffi_requests
import websocket
from flask import Flask, Response, jsonify, make_response, render_template, request

app = Flask(__name__)
app.secret_key = os.urandom(24)
app.config["PROPAGATE_EXCEPTIONS"] = False

# ==============================================================================
#  SABİTLER VE PROXY AYARLARI
# ==============================================================================
GENSPARK_HOST = "https://www.genspark.ai"
DEFAULT_PASSWORD = "Password123!"
APP_PASSWORD = "123"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ACCOUNTS_FILE = os.path.join(BASE_DIR, "genspark_hesaplar.txt")
FLASK_ACCOUNTS_FILE = ACCOUNTS_FILE
ROOT_ACCOUNTS_FILE = os.path.join(os.path.dirname(BASE_DIR), "genspark_hesaplar.txt")

# Proxy Ayarı: 0 -> Kapalı (Doğrudan bağlantı), 1 -> Açık (Proxy üzerinden)
PROXY_MODE = int(os.environ.get("PROXY_MODE", 0))
PROXY_URL = os.environ.get("PROXY_URL", "http://tghpxuwn:dxrsnpsyz3df@198.23.243.226:6361")

def get_proxies() -> Optional[Dict[str, str]]:
    """PROXY_MODE aktif ise curl_cffi ve requests proxy sözlüğü döner."""
    if PROXY_MODE == 1 and PROXY_URL:
        return {"http": PROXY_URL, "https": PROXY_URL}
    return None

# PixelBunny AI (Opus 4.7 Captcha Motoru)
PB_API_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImVzbmRocGFzb3hyd3p4cHpqbGZnIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzIzNDgxNjgsImV4cCI6MjA4NzkyNDE2OH0.cStXgyUmRDoaIctjoH4aNL2DUjjcnZLn_7VFNyEbdzE"
PB_BASE_URL = "https://esndhpasoxrwzxpzjlfg.supabase.co"
PB_PASSWORD = "SifreniYaz123!"

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}

# ==============================================================================
#  RESMİ ÖNYÜZ MODELLERİ (GENSPARK FRONTEND MODELS)
# ==============================================================================
FRONTEND_MODELS = [
    # --- Anthropic ---
    {"id": "claude-haiku-5-5", "name": "Claude Haiku 5.5", "provider": "Anthropic"},
    {"id": "claude-opus-4-7", "name": "Claude Opus 4.7", "provider": "Anthropic"},
    {"id": "claude-opus-4-8", "name": "Claude Opus 4.8", "provider": "Anthropic"},
    {"id": "claude-opus-5", "name": "Claude Opus 5", "provider": "Anthropic"},
    {"id": "claude-opus-5-5", "name": "Claude Opus 5.5", "provider": "Anthropic"},
    {"id": "claude-sonnet-5", "name": "Claude Sonnet 5", "provider": "Anthropic"},
    {"id": "claude-sonnet-5-5", "name": "Claude Sonnet 5.5", "provider": "Anthropic"},

    # --- DeepSeek ---
    {"id": "deep-seek-v4.1-flash", "name": "DeepSeek V4.1 Flash", "provider": "DeepSeek"},

    # --- Google ---
    {"id": "gemini-3.6-flash", "name": "Gemini 3.6 Flash", "provider": "Google"},
    {"id": "gemini-3.7-flash", "name": "Gemini 3.7 Flash", "provider": "Google"},
    {"id": "gemini-3.8-flash", "name": "Gemini 3.8 Flash", "provider": "Google"},
    {"id": "gemini-4-argon", "name": "Gemini 4 Argon", "provider": "Google"},

    # --- Kimi ---
    {"id": "kimi-k3", "name": "Kimi K3", "provider": "Kimi"},

    # --- Meta ---
    {"id": "muse-spark-open", "name": "Muse Spark Open", "provider": "Meta"},
    {"id": "muse-wonder", "name": "Muse Wonder", "provider": "Meta"},

    # --- MiniMax ---
    {"id": "minimax-m3", "name": "Minimax M3", "provider": "MiniMax"},

    # --- NVIDIA ---
    {"id": "nemotron-3-ultra", "name": "Nemotron 3 Ultra", "provider": "NVIDIA"},
    {"id": "nemotron-3.5-super-vl", "name": "Nemotron 3.5 Super VL", "provider": "NVIDIA"},

    # --- OpenAI ---
    {"id": "gpt-5.4", "name": "GPT-5.4", "provider": "OpenAI"},
    {"id": "gpt-5.5", "name": "GPT-5.5", "provider": "OpenAI"},
    {"id": "gpt-5.6-luna", "name": "GPT-5.6 Luna", "provider": "OpenAI"},
    {"id": "gpt-5.6-sol", "name": "GPT-5.6 Sol", "provider": "OpenAI"},
    {"id": "gpt-5.6-terra", "name": "GPT-5.6 Terra", "provider": "OpenAI"},
    {"id": "gpt-6-luna", "name": "GPT-6 Luna", "provider": "OpenAI"},
    {"id": "gpt-6-sol", "name": "GPT-6 Sol", "provider": "OpenAI"},
    {"id": "gpt-6.1-sol", "name": "GPT-6.1 Sol", "provider": "OpenAI"},

    # --- xAI ---
    {"id": "grok-4.6", "name": "Grok 4.6", "provider": "xAI"},
    {"id": "grok-4.7", "name": "Grok 4.7", "provider": "xAI"},

    # --- Xiaomi ---
    {"id": "mimo-v2.6-flash", "name": "MiMo V2.6 Flash", "provider": "Xiaomi"},
    {"id": "mimo-v2.6-pro", "name": "MiMo V2.6 Pro", "provider": "Xiaomi"},

    # --- ZhipuAI ---
    {"id": "glm-5p3", "name": "GLM-5.3", "provider": "ZhipuAI"},
    {"id": "glm-5p3-flash-baseten", "name": "GLM-5.3 Flash", "provider": "ZhipuAI"},

    # --- Genspark (Ensemble) ---
    {"id": "moa", "name": "Mixture-of-Agents", "provider": "Genspark", "is_moa": True},
]

DEFAULT_MODEL_ID = "gemini-4-argon"

# ==============================================================================
#  ÖZEL İSTİSNALAR (EXCEPTIONS)
# ==============================================================================
class CreditExhaustedError(Exception):
    """Genspark hesap kredisi tükendiğinde fırlatılır."""
    pass

class GensparkSessionError(Exception):
    """Genspark HTTP 401/403 oturum hatası."""
    pass

# ==============================================================================
#  BÖLÜM 1-2-3: GENSPARK ÇEKİRDEK MOTORU (CAPTCHA, TEMPMAIL, HESAP AÇMA)
# ==============================================================================
class SpamokMail:
    def random_email(self) -> str:
        s = "".join(random.SystemRandom().choice(string.ascii_lowercase + string.digits) for _ in range(15))
        return f"{s}@spamok.com"

    def get_confirm_link(self, mail: str, timeout: int = 30) -> Optional[str]:
        addr = mail.replace("@spamok.com", "")
        for _ in range(timeout):
            try:
                r = py_requests.get(f"https://api.spamok.com/v2/EmailBox/{addr}", timeout=10)
                for m in r.json().get("mails", []):
                    if "Confirm" in m.get("subject", "") or "Pixel Bunny" in m.get("fromDisplay", ""):
                        email_r = py_requests.get(f"https://api.spamok.com/v2/Email/{addr}/{m['id']}", timeout=10)
                        html = email_r.json().get("messageHtml", "")
                        match = re.search(r'href="(https://mt-link\.pixelbunny\.ai/cl/[^\"]+)"[^>]*background-color:#7c3aed', html)
                        if match:
                            return match.group(1)
                        links = re.findall(r'href="(https://mt-link\.pixelbunny\.ai/cl/[^\"]+)"', html)
                        return links[1] if len(links) >= 2 else (links[0] if links else None)
            except Exception:
                pass
            time.sleep(1)
        return None


class OpusCaptchaSolver:
    def __init__(self):
        self.token = None
        self.user_id = None
        self.model_id = "claude-opus-4.7"

    def _ensure_authenticated(self):
        if self.token and self.user_id:
            return

        print("[*] Opus 4.7 için PixelBunny oturumu başlatılıyor...")
        temp = SpamokMail()
        email = temp.random_email()

        headers = {
            "apikey": PB_API_KEY,
            "authorization": f"Bearer {PB_API_KEY}",
            "content-type": "application/json;charset=UTF-8",
            "origin": "https://pixelbunny.ai",
            "referer": "https://pixelbunny.ai/",
        }
        payload = {
            "email": email,
            "password": PB_PASSWORD,
            "data": {},
            "gotrue_meta_security": {},
            "code_challenge": None,
            "code_challenge_method": None,
        }
        r = py_requests.post(f"{PB_BASE_URL}/auth/v1/signup?redirect_to=https://pixelbunny.ai", headers=headers, json=payload, timeout=15)
        if r.status_code not in [200, 201]:
            raise RuntimeError("PixelBunny kayıt isteği başarısız oldu!")

        link = temp.get_confirm_link(email)
        if not link:
            raise RuntimeError("PixelBunny onay maili gelmedi!")

        py_requests.get(link, allow_redirects=True, timeout=15)

        r_tok = py_requests.post(
            f"{PB_BASE_URL}/auth/v1/token?grant_type=password",
            headers={"apikey": PB_API_KEY, "content-type": "application/json;charset=UTF-8"},
            json={"email": email, "password": PB_PASSWORD},
            timeout=15
        )
        data = r_tok.json()
        self.token = data.get("access_token")
        self.user_id = data.get("user", {}).get("id")
        print(f"[+] PixelBunny hazır! (User: {self.user_id})")

    def _upload_attachment(self, captcha_path: str, image_bytes: bytes) -> dict:
        headers = {
            "authorization": f"Bearer {self.token}",
            "origin": "https://pixelbunny.ai",
            "referer": "https://pixelbunny.ai/",
        }
        # 1. Deneme: functions/v1/upload-input (2 deneme, 15s zaman aşımı)
        for _ in range(2):
            try:
                with open(captcha_path, "rb") as f:
                    files = {"file": ("captcha.jpg", f, "image/jpeg")}
                    r_up = py_requests.post(
                        f"{PB_BASE_URL}/functions/v1/upload-input",
                        headers=headers,
                        files=files,
                        timeout=15,
                    )
                if r_up.status_code == 200:
                    img_url = r_up.json().get("url")
                    if img_url:
                        return {"url": img_url, "type": "image/png"}
            except Exception:
                time.sleep(1)

        # 2. Deneme: storage/v1/object/chat-attachments fallback (2 deneme, 15s)
        rand_name = f"cap_{int(time.time())}_{random.randint(100, 999)}.jpg"
        for _ in range(2):
            try:
                st_headers = {
                    "apikey": PB_API_KEY,
                    "authorization": f"Bearer {self.token}",
                }
                files = {"file": (rand_name, image_bytes, "image/jpeg")}
                r_st = py_requests.post(
                    f"{PB_BASE_URL}/storage/v1/object/chat-attachments/{rand_name}",
                    headers=st_headers,
                    files=files,
                    timeout=15,
                )
                if r_st.status_code in [200, 201]:
                    pub_url = f"{PB_BASE_URL}/storage/v1/object/public/chat-attachments/{rand_name}"
                    return {"name": rand_name, "url": pub_url, "type": "image/jpeg"}
            except Exception:
                time.sleep(1)

        raise RuntimeError("Görsel yüklenemedi (Zaman aşımı / Ağ hatası)")

    def solve(self, image_bytes: bytes, filename: str = "captcha_challenge.jpg") -> str:
        captcha_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
        with open(captcha_path, "wb") as f:
            f.write(image_bytes)

        try:
            return self._solve_inner(captcha_path, image_bytes)
        finally:
            try:
                if os.path.exists(captcha_path):
                    os.remove(captcha_path)
            except Exception:
                pass

    def _solve_inner(self, captcha_path: str, image_bytes: bytes) -> str:
        for solve_attempt in range(1, 4):
            self._ensure_authenticated()

            try:
                print("[*] Captcha görseli yükleniyor...")
                attachment = self._upload_attachment(captcha_path, image_bytes)
            except Exception as e:
                print(f"[!] Görsel yükleme hatası: {e}. Yeni PixelBunny oturumu açılıyor...")
                self.token = None
                self.user_id = None
                time.sleep(1)
                continue

            # Conversation başlat (2 deneme)
            conv_id = None
            for _ in range(2):
                try:
                    r_conv = py_requests.post(
                        f"{PB_BASE_URL}/rest/v1/chat_conversations?select=id",
                        headers={
                            "apikey": PB_API_KEY,
                            "authorization": f"Bearer {self.token}",
                            "content-type": "application/json",
                            "prefer": "return=representation",
                            "accept": "application/vnd.pgrst.object+json",
                        },
                        json={"user_id": self.user_id, "title": "Captcha"},
                        timeout=15,
                    )
                    c_data = r_conv.json()
                    if isinstance(c_data, list) and len(c_data) > 0:
                        conv_id = c_data[0].get("id")
                    elif isinstance(c_data, dict):
                        conv_id = c_data.get("id")
                    if conv_id:
                        break
                except Exception:
                    time.sleep(1)

            if not conv_id:
                print("[!] Conversation açılamadı. Yeni PixelBunny hesabı deneniyor...")
                self.token = None
                self.user_id = None
                time.sleep(1)
                continue

            prompt = (
                "Bu görseldeki CAPTCHA (güvenlik kodu) karakterlerini oku. "
                "Karakterler üstte ve altta veya yamuk olabilir; mantıksal sırasına göre tüm harf ve rakamları yan yana birleştirerek yaz. "
                "SADECE ve SADECE tespit ettiğin harf ve rakamları düz yazı olarak ver. "
                "Hiçbir açıklama, selamlama, markdown, boşluk veya noktalama işareti ekleme."
            )

            print("[*] Claude Opus 4.7'ye gönderiliyor...")
            chat_payload = {
                "conversation_id": conv_id,
                "model_id": self.model_id,
                "message": prompt,
                "attachments": [attachment],
                "incognito": False,
                "history": [],
            }

            full_resp = ""
            credit_exhausted = False

            for _ in range(2):
                try:
                    with py_requests.post(
                        f"{PB_BASE_URL}/functions/v1/chat-completion",
                        headers={"authorization": f"Bearer {self.token}", "content-type": "application/json"},
                        json=chat_payload,
                        stream=True,
                        timeout=35,
                    ) as res:
                        if res.status_code == 402:
                            credit_exhausted = True
                            break

                        res.encoding = "utf-8"
                        for raw_line in res.iter_lines():
                            if not raw_line:
                                continue
                            line = raw_line.decode("utf-8", errors="replace") if isinstance(raw_line, bytes) else raw_line
                            line = line.strip()
                            if not line.startswith("data:"):
                                continue
                            d_str = line[5:].strip()
                            if d_str == "[DONE]":
                                break
                            if "INSUFFICIENT_CREDITS" in d_str:
                                credit_exhausted = True
                                break
                            try:
                                data = json.loads(d_str)
                                choices = data.get("choices", [])
                                if choices:
                                    c = choices[0].get("delta", {}).get("content", "")
                                    if c:
                                        full_resp += c
                            except Exception:
                                pass
                    if full_resp or credit_exhausted:
                        break
                except Exception:
                    time.sleep(1)

            if credit_exhausted:
                print(f"\n[!] PixelBunny Claude Opus 4.7 kredisi bitti (402 / INSUFFICIENT_CREDITS)!")
                print(f"[*] Aynı CAPTCHA için anında yeni bir PixelBunny hesabı açılıp devam ediliyor (Deneme {solve_attempt}/3)...")
                self.token = None
                self.user_id = None
                time.sleep(1)
                continue

            clean_text = re.sub(r"[^A-Za-z0-9]", "", full_resp).upper()
            if not clean_text or len(clean_text) < 3:
                log(f"[!] Opus 4.7 geçersiz/boş yanıt döndü: '{full_resp}'. Yeni oturum ile tekrar deneniyor...")
                self.token = None
                self.user_id = None
                time.sleep(1)
                continue

            log(f"[+] Opus 4.7 Captcha Çözümü: {clean_text}")
            return clean_text

        raise RuntimeError("PixelBunny Opus 4.7 ile captcha çözülemedi (Denemeler tükendi).")


# ==============================================================================
#  BÖLÜM 2: TEMPMAILNINJA WEBSOCKET İLE DOĞRULAMA KODU MOTORU
# ==============================================================================
class TempMailNinja:
    def __init__(self, token: Optional[str] = None, locale: str = "en"):
        self.locale = locale
        self.token = token
        self.ws_url = f"wss://tm-app.solucioneswc.com:2083/socket.io/?locale={self.locale}&EIO=4&transport=websocket"
        self.headers = [
            "Origin: https://tempmail.ninja",
            "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        ]
        self.ws = None
        self.ack_id = 0
        self.active_email_addr = None
        self.is_connected = False
        self.is_running = False
        self._lock = threading.Lock()
        self._listener_thread = None
        self._pending_acks = {}
        self.received_codes = []

    def connect(self, retries: int = 3):
        if self.is_connected and self.ws:
            return

        import websocket
        last_err = None
        for attempt in range(1, retries + 1):
            try:
                self.ws = websocket.create_connection(
                    self.ws_url,
                    header=self.headers,
                    sslopt={"cert_reqs": ssl.CERT_NONE},
                    timeout=10.0,
                )
                self.ws.recv()
                self.ws.send("40")
                self.ws.recv()

                self.is_connected = True
                self.is_running = True
                self.ack_id = 0
                self._pending_acks.clear()

                self._listener_thread = threading.Thread(target=self._listen_loop, daemon=True)
                self._listener_thread.start()

                # Session başlat
                res = self._send_event("get_guest_user_data", {"token": self.token})
                if res and isinstance(res, dict) and res.get("activeEmail"):
                    self.active_email_addr = res["activeEmail"].get("address")
                self._subscribe_inbox()
                return
            except Exception as e:
                last_err = e
                time.sleep(1.0)

        raise RuntimeError(f"TempMailNinja bağlantısı kurulamadı: {last_err}")

    def _listen_loop(self):
        import websocket
        while self.is_running and self.ws:
            try:
                try:
                    msg = self.ws.recv()
                except (websocket.WebSocketTimeoutException, TimeoutError):
                    continue
                except Exception:
                    break

                if not msg:
                    continue
                if msg == "2":
                    try:
                        self.ws.send("3")
                    except Exception:
                        pass
                    continue

                if msg.startswith("43"):
                    rest = msg[2:]
                    num_str = ""
                    for ch in rest:
                        if ch.isdigit():
                            num_str += ch
                        else:
                            break
                    if num_str:
                        ack_num = int(num_str)
                        payload_str = rest[len(num_str):]
                        try:
                            payload = json.loads(payload_str)
                            res = payload[0] if isinstance(payload, list) and len(payload) > 0 else payload
                            if ack_num in self._pending_acks:
                                event, result_holder = self._pending_acks[ack_num]
                                result_holder["result"] = res
                                event.set()
                        except Exception:
                            pass

                elif msg.startswith("42"):
                    payload_str = msg[2:]
                    try:
                        data = json.loads(payload_str)
                        if isinstance(data, list) and len(data) >= 2 and data[0] == "new_message_notify":
                            mail = data[1].get("email_message", data[1])
                            subject = mail.get("subject", "")
                            m_code = re.search(r"\b(\d{6})\b", subject)
                            if m_code:
                                code = m_code.group(1)
                                self.received_codes.append(code)
                                print(f"[+] Doğrulama Kodu Yakalandı (WebSocket Push): {code}")
                    except Exception:
                        pass
            except Exception:
                continue
        self.is_connected = False

    def _send_event(self, event_name: str, data=None, timeout: int = 15):
        if not self.is_connected or not self.ws:
            self.connect()

        with self._lock:
            current_ack = self.ack_id
            self.ack_id += 1
            done_event = threading.Event()
            result_holder = {"result": None}
            self._pending_acks[current_ack] = (done_event, result_holder)

            packet = f'42{current_ack}["{event_name}",{json.dumps(data)}]' if data is not None else f'42{current_ack}["{event_name}"]'
            try:
                self.ws.send(packet)
            except Exception:
                self._pending_acks.pop(current_ack, None)
                return None

        if not done_event.wait(timeout):
            self._pending_acks.pop(current_ack, None)
            return None

        self._pending_acks.pop(current_ack, None)
        return result_holder["result"]

    def _subscribe_inbox(self):
        try:
            self._send_event("get_email_messages", {"messageType": "inbound"}, timeout=5)
        except Exception:
            pass

    def generate_quick_ninja(self) -> str:
        res = self._send_event("generate_temp_mail")
        if res and "emailData" in res:
            ed = res["emailData"]
            self.active_email_addr = ed.get("address") or ed.get("emailAddress")
            self.token = res.get("newToken", self.token)
            self._subscribe_inbox()
            return self.active_email_addr
        raise RuntimeError(f"Temp mail oluşturulamadı: {res}")

    def delete_email(self, email_address: str) -> bool:
        res = self._send_event("delete_email", {"emailAddress": email_address})
        return bool(res and ("emailDeleted" in res or res.get("success")))

    def get_messages(self):
        res = self._send_event("get_email_messages", {"messageType": "inbound"})
        return res.get("email_messages", []) if (res and isinstance(res, dict)) else []

    def get_message_detail(self, message_id):
        try:
            mid = int(message_id)
        except (ValueError, TypeError):
            mid = message_id
        res = self._send_event("get_email_message", {"messageId": mid}, timeout=20)
        if res and isinstance(res, dict) and "emailMessage" in res:
            msg_obj = res["emailMessage"]
            data = msg_obj.get("data", {})
            return {"subject": data.get("subject", ""), "content": data.get("content", "")}
        return None

    def close(self):
        self.is_running = False
        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass
            self.ws = None
        self.is_connected = False


def wait_for_code(mail_client: TempMailNinja, timeout: int = 180) -> Optional[str]:
    log(f"[*] Onay kodu bekleniyor ({timeout}s zaman aşımı)...")
    start = time.time()
    seen_ids = set()
    poll_count = 0
    while time.time() - start < timeout:
        poll_count += 1
        if mail_client.received_codes:
            code = mail_client.received_codes.pop(0)
            log(f"[+] Doğrulama Kodu Yakalandı (WebSocket Push): {code}")
            return code

        try:
            messages = mail_client.get_messages()
            if messages:
                for m in messages:
                    mid = m.get("id")
                    subject = m.get("subject", "")
                    code_match = re.search(r"\b(\d{6})\b", subject)
                    if code_match:
                        code = code_match.group(1)
                        log(f"[+] Doğrulama Kodu Yakalandı (E-posta Başlığı): {code}")
                        return code

                    if mid and mid not in seen_ids:
                        seen_ids.add(mid)
                        detail = mail_client.get_message_detail(mid)
                        if not detail:
                            continue
                        code_match = re.search(r"\b(\d{6})\b", detail.get("subject", "") + " " + detail.get("content", ""))
                        if code_match:
                            code = code_match.group(1)
                            log(f"[+] Doğrulama Kodu Yakalandı (Gelen Kutusu İçeriği): {code}")
                            return code
        except Exception:
            pass

        if poll_count % 5 == 0:
            elapsed = int(time.time() - start)
            log(f"[*] Onay kodu bekleniyor... ({elapsed}s geçti)")

        time.sleep(2)
    return None


# ==============================================================================
#  BÖLÜM 3: OTURUM ÇEREZLERİ & HESAP YÖNETİMİ
# ==============================================================================
def ensure_client_cookies(session: cffi_requests.Session):
    """Cloudflare ve Genspark bot filtrelerini aşmak için gereken istemci çerezlerini garantiye alır."""
    try:
        cookies = session.cookies.get_dict()
        if "ai_user" not in cookies:
            rand_id = "".join(random.choices(string.ascii_letters + string.digits + "+", k=22))
            iso_now = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
            session.cookies.set("ai_user", f"{rand_id}|{iso_now}", domain=".genspark.ai")
        if "ai_session" not in cookies:
            rand_id = "".join(random.choices(string.ascii_letters + string.digits, k=20))
            t1 = int(time.time() * 1000)
            session.cookies.set("ai_session", f"{rand_id}|{t1}|{t1}", domain=".genspark.ai")
        if "agree_terms" not in cookies:
            session.cookies.set("agree_terms", "1", domain=".genspark.ai")
        if "gslogin" not in cookies:
            session.cookies.set("gslogin", "1", domain=".genspark.ai")
        if "sidebar_expanded" not in cookies:
            session.cookies.set("sidebar_expanded", "false", domain=".genspark.ai")
        if "i18n_set" not in cookies:
            session.cookies.set("i18n_set", "en-US", domain=".genspark.ai")
    except Exception:
        pass


def get_account_balance(session: cffi_requests.Session) -> Optional[int]:
    """Aktif oturumun kalan kredi bakiyesini sorgular."""
    try:
        proxies = get_proxies()
        r = session.get(f"{GENSPARK_HOST}/api/payment/get_credit_balance?surface=super_agent", proxies=proxies, timeout=10)
        return r.json().get("data", {}).get("balance")
    except Exception:
        return None


def login_existing_account(email: str, password: str = DEFAULT_PASSWORD) -> Optional[Tuple[cffi_requests.Session, int]]:
    """Kayıtlı mevcut bir hesaba web üzerinden doğrudan giriş yapar."""
    log(f"[*] Kayıtlı hesaba web girişi deneniyor: {email}...")
    session = cffi_requests.Session(impersonate="chrome120")
    session.headers.update({
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "accept-language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
    })
    proxies = get_proxies()

    try:
        r0 = session.get(f"{GENSPARK_HOST}/api/login", allow_redirects=False, proxies=proxies, timeout=15)
        auth_url = r0.headers.get("location")
        if not auth_url:
            log(f"[-] {email}: Authorize URL alınamadı!")
            return None

        r1 = session.get(auth_url, proxies=proxies, timeout=15)
        s1 = json.loads(re.search(r"var SETTINGS = ({.*?});", r1.text).group(1))
        csrf = s1["csrf"]
        tx = s1["transId"]
        tenant = s1.get("hosts", {}).get("tenant", "/gensparkad.onmicrosoft.com/b2c_1_new_login")
        policy = s1.get("hosts", {}).get("policy", "b2c_1_new_login")
        b2c_base = f"https://login.genspark.ai{tenant}"

        headers = {
            "accept": "application/json, text/javascript, */*; q=0.01",
            "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
            "origin": "https://login.genspark.ai",
            "referer": r1.url,
            "x-csrf-token": csrf,
            "x-requested-with": "XMLHttpRequest",
        }
        r2 = session.post(
            f"{b2c_base}/SelfAsserted?tx={tx}&p={policy}",
            headers=headers,
            data={"request_type": "RESPONSE", "email": email, "password": password},
            proxies=proxies,
            timeout=15,
        )
        if r2.json().get("status") != "200":
            log(f"[-] {email}: B2C kimlik doğrulama başarısız (status != 200)")
            return None

        r3 = session.get(
            f"{b2c_base}/api/CombinedSigninAndSignup/confirmed?rememberMe=false&csrf_token={csrf}&tx={tx}&p={policy}",
            allow_redirects=False,
            proxies=proxies,
            timeout=15,
        )
        loc = r3.headers.get("Location")
        if not loc:
            log(f"[-] {email}: confirmed Location yönlendirmesi alınamadı!")
            return None
        session.get(loc, allow_redirects=True, proxies=proxies, timeout=15)

        ensure_client_cookies(session)
        try:
            session.get(f"{GENSPARK_HOST}/api/user", proxies=proxies, timeout=10)
            session.get(f"{GENSPARK_HOST}/api/quota/plan_usage", proxies=proxies, timeout=10)
        except Exception:
            pass

        bal = get_account_balance(session)
        balance = bal if bal is not None else 100
        log(f"[+] {email} giriş başarılı! Güncel Bakiye: {balance} Kredi")
        return session, balance
    except Exception as e:
        log(f"[!] {email} giriş hatası: {e}")
        return None


def create_and_login_genspark_account(password: str = DEFAULT_PASSWORD) -> Tuple[cffi_requests.Session, str, int]:
    """Sıfırdan otomatik hesap açar, onaylatır, 100 kredisini yükler ve oturum ile bakiyeyi döner."""
    log("\n" + "=" * 65)
    log("  GENSPARK SIFIRDAN OTOMATİK HESAP AÇMA VE GİRİŞ AKIŞI")
    log("=" * 65)

    session = cffi_requests.Session(impersonate="chrome120")
    session.headers.update({
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "accept-language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
    })
    proxies = get_proxies()

    log("[1/7] Web OAuth akışı (/api/login) başlatılıyor...")
    r0 = session.get(f"{GENSPARK_HOST}/api/login", allow_redirects=False, proxies=proxies)
    authorize_url = r0.headers.get("location")
    if not authorize_url:
        raise RuntimeError("Authorize URL alınamadı!")

    r1 = session.get(authorize_url, proxies=proxies)
    s1 = json.loads(re.search(r"var SETTINGS = ({.*?});", r1.text).group(1))
    csrf1 = s1["csrf"]
    tx1 = s1["transId"]
    tenant_path = s1.get("hosts", {}).get("tenant", "/gensparkad.onmicrosoft.com/b2c_1_new_login")
    b2c_base = f"https://login.genspark.ai{tenant_path}"
    policy = s1.get("hosts", {}).get("policy", "b2c_1_new_login")

    log("[2/7] B2C Kayıt (SignUp) adımına geçiliyor...")
    signup_url = f"{b2c_base}/api/CombinedSigninAndSignup/unified?local=signup&csrf_token={csrf1}&tx={tx1}&p={policy}"
    r2 = session.get(signup_url, proxies=proxies)
    s2 = json.loads(re.search(r"var SETTINGS = ({.*?});", r2.text).group(1))
    csrf_token = s2["csrf"]
    tx = s2["transId"]
    log(f"[+] Kayıt Canlı Oturumu Hazır! TX={tx[:25]}...")

    log("[3/7] TempMailNinja başlatılıyor ve geçici e-posta üretiliyor...")
    mail_client = TempMailNinja(locale="en")
    mail_client.connect()
    email = mail_client.generate_quick_ninja()
    log(f"[+] Geçici E-Posta Hazır: {email}")

    solver = OpusCaptchaSolver()

    try:
        b2c_headers = {
            "accept": "application/json, text/javascript, */*; q=0.01",
            "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
            "origin": "https://login.genspark.ai",
            "referer": signup_url,
            "x-csrf-token": csrf_token,
            "x-requested-with": "XMLHttpRequest",
        }

        captcha_solved = False
        captcha_text = ""
        for attempt in range(1, 16):
            log(f"\n[4/7] CAPTCHA Çözülüyor (Deneme {attempt}/15)...")
            try:
                if attempt > 1 and attempt % 2 == 1:
                    solver.token = None
                    solver.user_id = None

                get_cap_url = f"{b2c_base}/SelfAsserted/DisplayControlAction/vbeta/captchaControlChallengeCode/GetChallenge?tx={tx}&p={policy}&challengeType=Visual&_={int(time.time() * 1000)}"
                r_cap = session.get(get_cap_url, headers=b2c_headers, proxies=proxies, timeout=15)
                cap_data = r_cap.json()
                ch_id = cap_data.get("challengeId")
                ch_str = cap_data.get("challengeString", "")
                az_reg = cap_data.get("azureregion", "")
                b64_str = ch_str.split(",", 1)[1] if "," in ch_str else ch_str
                image_bytes = base64.b64decode(b64_str)

                log("[*] Görsel Claude Opus 4.7'ye aktarılıyor...")
                captcha_text = solver.solve(image_bytes, filename="captcha_challenge.jpg")
                if not captcha_text or len(captcha_text) < 3:
                    raise RuntimeError(f"Opus 4.7 geçersiz sonuç: '{captcha_text}'")

                log(f"[*] Çözüm B2C servisine doğrulatılıyor ({captcha_text})...")
                ver_url = f"{b2c_base}/SelfAsserted/DisplayControlAction/vbeta/captchaControlChallengeCode/VerifyChallenge?tx={tx}&p={policy}"
                ver_payload = {
                    "challengeId": ch_id,
                    "captchaEntered": captcha_text,
                    "challengeType": "Visual",
                    "azureRegion": az_reg,
                }
                r_ver = session.post(ver_url, headers=b2c_headers, data=ver_payload, proxies=proxies, timeout=15)
                res_ver = r_ver.json()
                if str(res_ver.get("isCaptchaSolved")).lower() == "true":
                    log(f"[+] CAPTCHA Başarıyla Doğrulandı! ({captcha_text})")
                    captcha_solved = True
                    break
                log(f"[!] CAPTCHA eşleşmedi ({captcha_text}), yeni görsel çekiliyor...")
                time.sleep(1)

            except Exception as e:
                log(f"[!] CAPTCHA Deneme ({attempt}) hatası: {e}")
                time.sleep(1.5)

        if not captcha_solved:
            raise RuntimeError("CAPTCHA 15 denemede çözülemedi!")

        log(f"[5/7] E-postaya doğrulama kodu gönderiliyor ({email})...")
        send_url = f"{b2c_base}/SelfAsserted/DisplayControlAction/vbeta/emailVerificationControl/SendCode?tx={tx}&p={policy}"
        r_send = session.post(send_url, headers=b2c_headers, data={"email": email}, proxies=proxies)
        if r_send.json().get("status") != "200":
            raise RuntimeError(f"Onay kodu gönderilemedi: {r_send.text}")

        log("[*] Onay kodu bekleniyor (TempMailNinja dinleniyor)...")
        code = wait_for_code(mail_client, timeout=180)
        if not code:
            raise RuntimeError("Doğrulama kodu 3 dakika içinde gelmedi!")

        log(f"[+] Doğrulama kodu geldi: {code}. B2C onaylanıyor...")
        ver_code_url = f"{b2c_base}/SelfAsserted/DisplayControlAction/vbeta/emailVerificationControl/VerifyCode?tx={tx}&p={policy}"
        r_vcode = session.post(ver_code_url, headers=b2c_headers, data={"email": email, "emailVerificationCode": code}, proxies=proxies)
        if r_vcode.json().get("status") != "200":
            raise RuntimeError(f"Doğrulama kodu onaylanamadı: {r_vcode.text}")

        log("[6/7] SelfAsserted ile hesap kaydı tamamlanıyor...")
        self_url = f"{b2c_base}/SelfAsserted?tx={tx}&p={policy}"
        self_payload = {
            "email": email,
            "emailVerificationCode": code,
            "captchaControlChallengeCode": captcha_text,
            "newPassword": password,
            "reenterPassword": password,
            "request_type": "RESPONSE",
        }
        r_self = session.post(self_url, headers=b2c_headers, data=self_payload, proxies=proxies)
        if r_self.json().get("status") != "200":
            raise RuntimeError(f"Hesap kaydı tamamlanamadı: {r_self.text}")

        log("\n" + "=" * 65)
        log("  [SUCCESS] HESAP BAŞARIYLA AÇILDI!")
        log(f"  E-POSTA : {email}")
        log(f"  ŞİFRE   : {password}")
        log("=" * 65)

        save_new_account(email, password)

        log("[7/7] Web Girişi (/api/auth) tamamlanıyor...")
        confirmed_url = f"{b2c_base}/api/CombinedSigninAndSignup/confirmed?rememberMe=false&csrf_token={csrf_token}&tx={tx}&p={policy}"
        r_conf = session.get(confirmed_url, allow_redirects=False, proxies=proxies)
        auth_cb = r_conf.headers.get("Location")
        if not auth_cb:
            raise RuntimeError("Confirmed yönlendirme linki alınamadı!")

        session.get(auth_cb, allow_redirects=True, proxies=proxies)
        ensure_client_cookies(session)

        session_id = session.cookies.get_dict().get("session_id")
        if not session_id:
            raise RuntimeError("Genspark session_id çerezi alınamadı!")

        log(f"[+] Web Girişi Başarılı! (Session: {session_id[:25]}...)")

        log("[*] Hesap profili ve 100 hoş geldin kredisi aktifleştiriliyor...")
        balance = 100
        try:
            session.get(f"{GENSPARK_HOST}/api/user", proxies=proxies, timeout=10)
            r_bal = session.get(f"{GENSPARK_HOST}/api/payment/get_credit_balance?surface=super_agent", proxies=proxies, timeout=10)
            bal_data = r_bal.json().get("data", {})
            balance = bal_data.get("balance", 100)
            session.get(f"{GENSPARK_HOST}/api/quota/plan_usage", proxies=proxies, timeout=10)
            log(f"[+] 100 Kredi Başarıyla Aktifleştirildi! (Güncel Bakiye: {balance} Kredi)")
        except Exception as e:
            log(f"[!] Kredi aktivasyonu sırasında uyarı: {e}")

        return session, email, balance

    finally:
        mail_client.delete_email(email)
        mail_client.close()

# ==============================================================================
#  HESAP YÖNETİMİ & SIRALI ROTASYON (genspark_hesaplar.txt)
# ==============================================================================
def read_saved_accounts() -> List[Tuple[str, str]]:
    """genspark_hesaplar.txt dosyasından kayıtlı email:password çiftlerini okur."""
    accounts = []
    seen = set()
    files_to_check = [ACCOUNTS_FILE]
    if os.path.exists(ROOT_ACCOUNTS_FILE) and ROOT_ACCOUNTS_FILE != ACCOUNTS_FILE:
        files_to_check.append(ROOT_ACCOUNTS_FILE)

    for fpath in files_to_check:
        if os.path.exists(fpath):
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        if ":" in line:
                            parts = line.split(":", 1)
                            email = parts[0].strip()
                            pwd = parts[1].strip()
                            if email and email not in seen:
                                seen.add(email)
                                accounts.append((email, pwd))
            except Exception:
                pass
    return accounts


def save_new_account(email: str, password: str = DEFAULT_PASSWORD):
    """Yeni açılan hesabı genspark_hesaplar.txt dosyasına ekler (tekilleştirerek)."""
    accounts = read_saved_accounts()
    existing_emails = {a[0].lower().strip() for a in accounts}
    if email.lower().strip() in existing_emails:
        return

    try:
        with open(ACCOUNTS_FILE, "a", encoding="utf-8") as f:
            f.write(f"{email}:{password}\n")
        log(f"[+] Yeni hesap dosyaya eklendi: {ACCOUNTS_FILE}")
    except Exception as e:
        log(f"[!] Hesap dosyaya yazılamadı: {e}")


_exhausted_emails = set()

def get_or_rotate_account(current_email: Optional[str] = None) -> Tuple[cffi_requests.Session, str, int]:
    """
    1. genspark_hesaplar.txt dosyasında hesap varsa sırayla dener.
    2. Kredisi biten hesabı atlayıp sıradakine geçer.
    3. Txt boşsa veya dosya yoksa sıfırdan yeni hesap açar, kaydeder ve döner.
    """
    if current_email:
        _exhausted_emails.add(current_email.lower().strip())
        log(f"[*] Önceki hesap ({current_email}) listede sonraya atılıyor.")

    accounts = read_saved_accounts()
    if accounts:
        log(f"[*] 'genspark_hesaplar.txt' dosyasında {len(accounts)} kayıtlı hesap bulundu: {[a[0] for a in accounts]}")
        start_idx = 0
        if current_email:
            for idx, (acc_email, _) in enumerate(accounts):
                if acc_email.lower().strip() == current_email.lower().strip():
                    start_idx = idx + 1
                    break

        candidates = accounts[start_idx:] + accounts[:start_idx]
        for acc_email, acc_pwd in candidates:
            if acc_email.lower().strip() in _exhausted_emails:
                log(f"[-] {acc_email} kredisi tükenmiş olarak işaretlenmiş, atlanıyor...")
                continue
            try:
                res = login_existing_account(acc_email, acc_pwd)
                if res:
                    s, bal = res
                    if bal > 0:
                        log(f"[+] AKTİF HESAP SEÇİLDİ: {acc_email} (Bakiye: {bal} Kredi)")
                        return s, acc_email, bal
                    else:
                        log(f"[-] Hesabın kredisi bitmiş (0 Kredi): {acc_email}")
                        _exhausted_emails.add(acc_email.lower().strip())
                else:
                    log(f"[-] Giriş başarısız: {acc_email}")
            except Exception as e:
                log(f"[!] Hesap giriş hatası ({acc_email}): {e}")

    log("[*] Kayıtlı hesap kalmadı veya liste boş. Sıfırdan yeni hesap açılıyor...")
    s, new_email, new_bal = create_and_login_genspark_account(password=DEFAULT_PASSWORD)
    return s, new_email, new_bal

# ==============================================================================
#  BÖLÜM 4: DOSYA & EK İŞLEME (AZURE BLOB & BASE64)
# ==============================================================================
def process_uploaded_file(session: cffi_requests.Session, filename: str, file_bytes: bytes, mime: Optional[str] = None) -> dict:
    clean_name = os.path.basename(filename)
    ext_with_dot = os.path.splitext(clean_name)[1].lower()
    ext = ext_with_dot.lstrip(".") or "bin"
    file_size = len(file_bytes)

    if not mime or mime == "application/octet-stream":
        guessed, _ = mimetypes.guess_type(clean_name)
        if guessed:
            mime = guessed
        elif ext in ["txt", "py", "json", "js", "html", "css", "md", "csv", "xml"]:
            mime = "text/plain"
        elif ext in ["mp4", "webm", "mkv"]:
            mime = "video/mp4"
        elif ext in ["mp3", "wav", "ogg", "m4a"]:
            mime = "audio/mpeg"
        elif ext == "pdf":
            mime = "application/pdf"
        else:
            mime = "application/octet-stream"

    # Görsel Dosyası -> Base64
    if ext_with_dot in IMAGE_EXTENSIONS and file_size <= 15 * 1024 * 1024:
        b64_data = base64.b64encode(file_bytes).decode("ascii")
        img_mime = mime if mime.startswith("image/") else "image/png"
        return {
            "url": f"data:{img_mime};base64,{b64_data}",
            "type": img_mime,
            "mediaType": img_mime,
            "name": clean_name,
            "filename": clean_name,
        }

    # Diğer Dosyalar -> Genspark Azure Blob Yüklemesi
    params = {
        "content_type": mime,
        "name": clean_name,
        "long_lived": "true",
    }
    proxies = get_proxies()
    r = session.get(f"{GENSPARK_HOST}/api/files/get_upload_url", params=params, proxies=proxies, timeout=25)
    if r.status_code != 200:
        raise RuntimeError(f"Genspark get_upload_url hatası (HTTP {r.status_code})")

    res_json = r.json()
    if res_json.get("status") != 0:
        raise RuntimeError(f"Genspark get_upload_url başarısız: {res_json.get('message')}")

    data = res_json["data"]
    upload_url = data["upload_url"]
    file_wrapper_url = data["file_wrapper_url"]

    azure_headers = {
        "x-ms-blob-type": "BlockBlob",
        "Content-Type": mime,
    }
    r_put = py_requests.put(upload_url, data=file_bytes, headers=azure_headers, proxies=proxies, timeout=120)
    if r_put.status_code not in [200, 201]:
        raise RuntimeError(f"Azure Blob yükleme hatası (HTTP {r_put.status_code})")

    return {
        "url": file_wrapper_url,
        "type": mime,
        "mediaType": mime,
        "name": clean_name,
        "filename": clean_name,
    }

# ==============================================================================
#  BÖLÜM 5: GENSPARK SOHBET AKIŞI (SSE GENERATOR)
# ==============================================================================
def stream_genspark_chat(
    session: cffi_requests.Session,
    user_prompt: str,
    history: Optional[List[Dict]] = None,
    model: str = DEFAULT_MODEL_ID,
    attachments: Optional[List[dict]] = None,
    web_search: bool = True,
) -> Generator[str, None, None]:
    if history is None:
        history = []

    msg_id = str(uuid.uuid4())
    req_trace = uuid.uuid4().hex
    span_id = uuid.uuid4().hex[:16]

    messages_payload = []
    for turn in history:
        r = turn.get("role", "user")
        t = turn.get("text", "")
        atts = turn.get("attachments") or []
        if atts:
            content_parts = []
            for a in atts:
                u = a.get("url", "")
                fname = a.get("filename") or a.get("name", "file")
                mtype = a.get("mediaType") or a.get("type", "application/octet-stream")
                if u.startswith("data:image"):
                    content_parts.append({"type": "image_url", "image_url": {"url": u}})
                else:
                    content_parts.append({
                        "type": "private_file",
                        "private_file": {
                            "name": fname,
                            "type": mtype,
                            "private_storage_url": u,
                            "uploadProgress": 1,
                            "localId": f"file_chip_{int(time.time()*1000)}",
                        }
                    })
            if t:
                content_parts.append({"type": "text", "text": t})
            content = content_parts
        else:
            content = t

        messages_payload.append({
            "role": r,
            "id": str(uuid.uuid4()),
            "content": content,
            "pending": False,
            "sendStatus": "success",
            "_deepDiveStateNegContent": t if isinstance(content, str) else "",
        })

    # Güncel mesaj ve ekleri
    if attachments:
        clean_parts = []
        for a in attachments:
            u = a.get("url", "")
            fname = a.get("filename") or a.get("name", "file")
            mtype = a.get("mediaType") or a.get("type", "application/octet-stream")
            if u.startswith("data:image"):
                clean_parts.append({"type": "image_url", "image_url": {"url": u}})
            else:
                clean_parts.append({
                    "type": "private_file",
                    "private_file": {
                        "name": fname,
                        "type": mtype,
                        "private_storage_url": u,
                        "uploadProgress": 1,
                        "localId": f"file_chip_{int(time.time()*1000)}",
                    }
                })
        if user_prompt:
            clean_parts.append({"type": "text", "text": user_prompt})
        current_content = clean_parts
    else:
        current_content = user_prompt

    messages_payload.append({
        "role": "user",
        "id": msg_id,
        "content": current_content,
        "pending": True,
        "sendStatus": "sending",
        "_deepDiveStateNegContent": user_prompt,
    })

    headers = {
        "accept": "*/*",
        "accept-language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
        "content-type": "application/json",
        "origin": GENSPARK_HOST,
        "referer": f"{GENSPARK_HOST}/",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "request-id": f"|{req_trace}.{span_id}",
        "traceparent": f"00-{req_trace}-{span_id}-01",
        "x-timezone": "Europe/Istanbul",
    }

    is_moa = model.lower() in ["moa", "mixture-of-agents"]
    api_model = "gpt-5.6-luna" if is_moa else model
    moa_models = ["gpt-5.6-luna", "gpt-6.1-sol", "gemini-3.7-flash"] if is_moa else []

    payload = {
        "ai_chat_model": api_model,
        "ai_chat_enable_search": bool(web_search),
        "ai_chat_disable_personalization": False,
        "use_moa_proxy": is_moa,
        "moa_models": moa_models,
        "writingContent": None,
        "sas_ask_origin": "typed",
        "type": "ai_chat",
        "project_id": None,
        "messages": messages_payload,
        "user_s_input": user_prompt,
        "client_message_id": msg_id,
        "g_recaptcha_token": "",
        "is_private": True,
        "push_token": "",
        "session_state": {
            "steps": [],
            "messages": messages_payload,
        },
        "last_seen_event_index": -1,
        "chat_session_id": None,
    }

    ask_url = f"{GENSPARK_HOST}/api/agent/ask_proxy"
    proxies = get_proxies()

    res = session.post(ask_url, headers=headers, json=payload, stream=True, proxies=proxies, timeout=60)

    if res.status_code != 200:
        if res.status_code in [401, 403]:
            raise GensparkSessionError(f"HTTP {res.status_code} Yetkilendirme Hatası")
        elif res.status_code == 429:
            raise RuntimeError("HTTP 429 Hız Sınırı")
        else:
            raise RuntimeError(f"Genspark API Hatası: HTTP {res.status_code}")

    streamed_any = False
    for raw_line in res.iter_lines():
        if not raw_line:
            continue
        line = raw_line.decode("utf-8", errors="replace") if isinstance(raw_line, bytes) else raw_line
        if not line.startswith("data:"):
            continue

        raw_json = line[5:].strip()
        if not raw_json:
            continue

        try:
            obj = json.loads(raw_json)
        except Exception:
            continue

        delta = obj.get("delta")
        if delta:
            streamed_any = True
            yield delta
            continue

        msg_obj = obj.get("message")
        if msg_obj and isinstance(msg_obj, dict):
            action = msg_obj.get("action")
            if action and isinstance(action, dict):
                act_type = action.get("type", "")
                block_reason = action.get("action_params", {}).get("block_reason", "")
                if act_type == "ACTION_CREDIT_EXHAUSTED" or block_reason == "free_tier_exhausted":
                    raise CreditExhaustedError("Genspark ücretsiz kredi kotası tükendi.")

            s_state = msg_obj.get("session_state")
            if s_state and isinstance(s_state, dict) and s_state.get("consume_usage_quota_exceeded"):
                raise CreditExhaustedError("Genspark kullanım kotası aşıldı.")

            content = msg_obj.get("content", "")
            if "credit_exhausted" in content or "used all your credits" in content:
                raise CreditExhaustedError("Genspark kredileri tükendi.")

            if content and not streamed_any:
                streamed_any = True
                yield content

        if obj.get("field_name") == "status" and obj.get("field_value") == "FINISHED":
            break


def stream_genspark_message(
    client,
    text_message: str,
    attachments: list,
    history: list,
    model: str,
    web_search: bool,
    sess: dict,
    on_start=None,
    on_delta=None,
):
    """
    Start-Stop (Abort) ve Kredi Takibi ile use-ai SSE uyumlu akış üretir.
    """
    assistant_text = ""
    start_fired = False
    assistant_id = str(uuid.uuid4())

    try:
        stream_gen = stream_genspark_chat(
            session=client.session,
            user_prompt=text_message,
            history=history,
            model=model,
            attachments=attachments,
            web_search=web_search,
        )

        for delta in stream_gen:
            # Canlı durdurma (Stop / Abort) kontrolü
            if sess.get("aborted"):
                log("[*] Akış kullanıcı tarafından durduruldu (Stop / Abort).")
                break

            if not start_fired:
                start_fired = True
                log(f"[+] Yanıt akışı başladı ({model})...")
                if on_start:
                    on_start()
                yield f"data: {json.dumps({'type': 'start', 'message_id': assistant_id})}\n\n"

            assistant_text += delta
            if on_delta:
                on_delta(assistant_text)

            yield f"data: {json.dumps({'type': 'chunk', 'content': delta})}\n\n"

        current_bal = None
        try:
            current_bal = get_account_balance(client.session)
            if current_bal is not None:
                client.balance = current_bal
                sess["total_credits"] = current_bal
        except Exception:
            pass

        if assistant_text:
            bal_str = f" | Güncel Bakiye: {current_bal} Kredi" if current_bal is not None else ""
            log(f"[+] Yanıt tamamlandı ({len(assistant_text)} karakter){bal_str}")
            done_payload = {'type': 'done', 'full_response': assistant_text}
            if current_bal is not None:
                done_payload['balance'] = current_bal
            yield f"data: {json.dumps(done_payload)}\n\n"
        elif sess.get("aborted"):
            log("[*] Akış kullanıcı tarafından durduruldu (Stop / Abort).")
            interrupted_payload = {'type': 'stream_interrupted', 'full_response': assistant_text}
            if current_bal is not None:
                interrupted_payload['balance'] = current_bal
            yield f"data: {json.dumps(interrupted_payload)}\n\n"
        else:
            log("[-] Genspark'tan boş yanıt döndü.")
            yield f"data: {json.dumps({'type': 'error', 'code': 'STREAM_CLOSED_EMPTY'})}\n\n"

    except CreditExhaustedError:
        log("[!] Genspark kredisi tükendi (CreditExhaustedError)!")
        client.is_credit_exhausted = True
        client.balance = 0
        sess["total_credits"] = 0
        yield f"data: {json.dumps({'type': 'balance', 'balance': 0})}\n\n"
        yield f"data: {json.dumps({'type': 'error', 'code': 'INSUFFICIENT_CREDITS'})}\n\n"

    except GensparkSessionError as e:
        log(f"[!] Genspark Oturum hatası: {e}")
        yield f"data: {json.dumps({'type': 'error', 'code': f'WS_CONNECT_FAILED: {str(e)}'})}\n\n"

    except Exception as e:
        err_msg = str(e)
        log(f"[!] Akış hatası: {err_msg}")
        traceback.print_exc()
        if "credit_exhausted" in err_msg.lower():
            yield f"data: {json.dumps({'type': 'error', 'code': 'INSUFFICIENT_CREDITS'})}\n\n"
        else:
            yield f"data: {json.dumps({'type': 'error', 'code': f'Hata: {err_msg}'})}\n\n"

# ==============================================================================
#  BÖLÜM 6: FLASK OTURUM VE GEÇMİŞ YÖNETİMİ
# ==============================================================================
class GensparkClient:
    def __init__(self, session=None, email=None, balance=0, model=DEFAULT_MODEL_ID):
        self.session = session
        self.email = email or ""
        self.balance = balance
        self.model = model
        self.is_credit_exhausted = False

_sessions = {}

def get_sid() -> Optional[str]:
    return request.cookies.get("ua_sid")

def get_sess() -> Optional[dict]:
    sid = get_sid()
    return _sessions.get(sid) if sid else None

def new_sess(sid: str) -> dict:
    sess = {
        "app_unlocked": False,
        "client": None,
        "model": DEFAULT_MODEL_ID,
        "history": [],
        "conversations": [],
        "active_local_conv_id": None,
        "web_search": False,
        "auto_attach": False,
        "aborted": False,
        "total_credits": 0,
        "total_cost": 0.0,
    }
    _sessions[sid] = sess
    return sess

def make_local_conv_id() -> str:
    return str(uuid.uuid4())

def append_history(history: list, role: str, text: str, attachments: list = None) -> dict:
    entry = {
        "role": role,
        "text": text,
        "at": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if attachments:
        entry["attachments"] = [
            {
                "filename": a.get("filename") or a.get("name", "file"),
                "mediaType": a.get("mediaType") or a.get("type", "application/octet-stream"),
                "url": a.get("url", ""),
            }
            for a in attachments
        ]
    history.append(entry)
    return entry

def set_turn_content(turn: dict, text: str, attachments: list = None):
    turn["text"] = text
    if attachments is not None:
        turn["attachments"] = attachments

def _derive_title(history: list) -> str:
    for item in history:
        if item.get("role") == "user" and item.get("text"):
            t = item["text"].strip().split("\n")[0][:48].strip()
            if t:
                return t
    return "Yeni Sohbet"

def save_conv_to_history(sess: dict):
    h = sess.get("history", [])
    if not h:
        return
    conv_id = sess.get("active_local_conv_id")
    if not conv_id:
        conv_id = make_local_conv_id()
        sess["active_local_conv_id"] = conv_id

    title = _derive_title(h)
    convs = sess.setdefault("conversations", [])

    for c in convs:
        if c.get("conv_id") == conv_id:
            c["history"] = list(h)
            if not c.get("title_locked"):
                c["title"] = title
            c["updated_at"] = time.time()
            return

    convs.insert(0, {
        "conv_id": conv_id,
        "title": title,
        "history": list(h),
        "created_at": time.time(),
        "updated_at": time.time(),
        "title_locked": False,
    })
    if len(convs) > 30:
        sess["conversations"] = convs[:30]

# ==============================================================================
#  BÖLÜM 7: FLASK ROTALARI (ROUTES)
# ==============================================================================
@app.route("/")
@app.route("/chat/<conv_id>")
def index(conv_id=None):
    sid = get_sid()
    resp = make_response(render_template("index.html"))
    if not sid or sid not in _sessions:
        new_id = sid or str(uuid.uuid4())
        new_sess(new_id)
        resp.set_cookie("ua_sid", new_id, max_age=86400 * 30, samesite="Lax")
    return resp


@app.route("/api/models")
def api_models():
    models_flat = []
    for m in FRONTEND_MODELS:
        models_flat.append({
            "id": m["id"],
            "label": m["name"],
            "category": m["provider"],
        })
    return jsonify({"models": models_flat, "default": DEFAULT_MODEL_ID})


@app.route("/api/model", methods=["POST"])
def api_model():
    sess = get_sess()
    if not sess:
        return jsonify({"error": "Oturum bulunamadı."}), 401
    data = request.json or {}
    model = data.get("model")
    if model:
        sess["model"] = model
        if sess.get("client"):
            sess["client"].model = model
        print(f"[*] Aktif model değiştirildi: {model}")
    return jsonify({"success": True, "model": sess.get("model")})


@app.route("/api/status")
def api_status():
    sess = get_sess()
    if not sess or not sess.get("app_unlocked"):
        return jsonify({"initialized": False})
    client = sess.get("client")
    if client:
        try:
            fresh_bal = get_account_balance(client.session)
            if fresh_bal is not None:
                client.balance = fresh_bal
                sess["total_credits"] = fresh_bal
        except Exception:
            pass
    email = client.email if client else ""
    balance = client.balance if client else 0
    return jsonify({
        "initialized": True,
        "account_created": bool(client),
        "email": email,
        "balance": balance,
        "model": sess.get("model", DEFAULT_MODEL_ID),
        "message_count": len(sess.get("history", [])) // 2,
        "total_credits": balance,
        "total_cost": 0,
    })


@app.route("/api/init", methods=["POST"])
def api_init():
    data = request.json or {}
    if data.get("password") != APP_PASSWORD:
        return jsonify({"success": False, "error": "Hatalı şifre."}), 401

    sid = get_sid() or str(uuid.uuid4())
    sess = new_sess(sid)
    sess["app_unlocked"] = True
    log(f"[+] Uygulama kilidi açıldı (Oturum: {sid[:8]}...)")

    # Kullanıcı önyüzden işlem başlatana kadar arkaplanda hesap açılmaz
    resp = jsonify({
        "success": True,
        "account_created": False,
        "email": "",
        "balance": 0,
        "model": sess["model"]
    })
    resp.set_cookie("ua_sid", sid, max_age=86400 * 30, samesite="Lax")
    return resp


@app.route("/api/logout", methods=["POST"])
def api_logout():
    sid = get_sid()
    if sid and sid in _sessions:
        del _sessions[sid]
    resp = jsonify({"success": True})
    resp.delete_cookie("ua_sid")
    log(f"[*] Çıkış yapıldı (Oturum: {sid[:8] if sid else 'Yok'})")
    return resp


@app.route("/api/reset", methods=["POST"])
def api_reset():
    """
    Hesap değiştirme / sıfırlama rotası:
    1. genspark_hesaplar.txt içindeki sıradaki hesaba geçer.
    2. Hesap kalmadıysa sıfırdan yeni hesap açar.
    3. Konuşma geçmişini kaybetmeden yeni hesaba aktarır.
    """
    log("\n" + "=" * 65)
    log("  [/api/reset] HESAP DEĞİŞTİRME / YENİ GİRİŞ İSTEĞİ ALINDI")
    log("=" * 65)

    data = request.json or {}
    carry = data.get("carry_history", False)
    carry_conv_id = data.get("conv_id")

    sid = get_sid() or str(uuid.uuid4())
    old_sess = get_sess()
    old_client = old_sess.get("client") if old_sess else None
    current_email = old_client.email if old_client else None
    model = old_sess.get("model", DEFAULT_MODEL_ID) if old_sess else DEFAULT_MODEL_ID

    carry_history = None
    carry_local_id = None
    if carry and old_sess:
        if carry_conv_id:
            for c in old_sess.get("conversations", []):
                if c.get("conv_id") == carry_conv_id:
                    carry_history = c["history"]
                    carry_local_id = carry_conv_id
                    break
        if carry_history is None and old_sess.get("history"):
            carry_history = old_sess["history"]
            carry_local_id = old_sess.get("active_local_conv_id")

    if old_sess:
        save_conv_to_history(old_sess)
    old_conversations = [
        c for c in (old_sess.get("conversations", []) if old_sess else [])
        if any(t.get("text") or t.get("attachments") for t in c.get("history", []))
    ]

    sess = new_sess(sid)
    sess["app_unlocked"] = True
    sess["model"] = model
    sess["conversations"] = old_conversations

    try:
        session, email, balance = get_or_rotate_account(current_email=current_email)
        client = GensparkClient(session=session, email=email, balance=balance, model=model)
        sess["client"] = client
        sess["total_credits"] = balance
        log(f"[+] [/api/reset] Hesap başarıyla bağlandı: {email} (Bakiye: {balance} Kredi)")
    except Exception as e:
        log(f"[-] [/api/reset] HATA OLUŞTU:")
        traceback.print_exc()
        if old_sess:
            _sessions[sid] = old_sess
        return jsonify({"success": False, "error": f"Hesap açılamadı / bağlanamadı: {str(e)}"}), 500

    new_conv_id = None
    if carry_history:
        sess["history"] = carry_history
        sess["active_local_conv_id"] = carry_local_id
        new_conv_id = carry_local_id
        save_conv_to_history(sess)
        log(f"[+] Konuşma geçmişi ({len(carry_history)} mesaj) yeni hesaba devredildi.")

    resp_data = {
        "success": True,
        "email": client.email,
        "balance": client.balance,
        "model": model,
        "carried": bool(carry_history),
        "new_conv_id": new_conv_id,
    }
    resp = jsonify(resp_data)
    resp.set_cookie("ua_sid", sid, max_age=86400 * 30, samesite="Lax")
    return resp


@app.route("/api/send", methods=["POST"])
def api_send():
    """
    Ana sohbet uç noktası (SSE akışı).
    Start, stop (abort) ve kredi tükenme kontrollerini yönetir.
    """
    sess = get_sess()
    if not sess or not sess.get("app_unlocked"):
        return jsonify({"error": "Lütfen önce şifre ile giriş yapın."}), 401

    if not sess.get("client"):
        log("[*] /api/send: Aktif hesap bağlı değil, sıradaki hesap bağlanıyor...")
        try:
            s, email, balance = get_or_rotate_account()
            client = GensparkClient(session=s, email=email, balance=balance, model=sess.get("model", DEFAULT_MODEL_ID))
            sess["client"] = client
            sess["total_credits"] = balance
        except Exception as e:
            log(f"[-] Otomatik hesap bağlantısı başarısız: {e}")
            return jsonify({"error": f"Hesap açılamadı / bağlanamadı: {str(e)}"}), 500

    sess["aborted"] = False

    data = request.json or {}
    message = data.get("message", "").strip()
    attachments = data.get("attachments", [])

    if not message and not attachments:
        return jsonify({"error": "Mesaj boş."}), 400

    client = sess["client"]
    model = sess.get("model", DEFAULT_MODEL_ID)
    web_search = data.get("web_search", sess.get("web_search", False))
    auto_attach = data.get("auto_attach", sess.get("auto_attach", False))

    log(f"[*] Yeni istek alındı [{client.email} | Model: {model} | Web Arama: {'AÇIK' if web_search else 'KAPALI'}]")

    if auto_attach:
        existing_urls = {a.get("url") for a in attachments if a.get("url")}
        for turn in sess.get("history", []):
            for past_att in turn.get("attachments", []):
                p_url = past_att.get("url")
                if p_url and p_url not in existing_urls:
                    existing_urls.add(p_url)
                    attachments.append(past_att)

    is_new_conv = not sess.get("active_local_conv_id")
    new_local_conv_id = make_local_conv_id() if is_new_conv else None
    if is_new_conv:
        sess["active_local_conv_id"] = new_local_conv_id

    history_committed = False
    assistant_turn = None

    def commit_history_on_start():
        nonlocal history_committed, assistant_turn
        if history_committed:
            return
        history_committed = True
        target_history = sess.setdefault("history", [])
        append_history(
            target_history,
            "user",
            message,
            attachments=attachments or None,
        )
        assistant_turn = append_history(
            target_history,
            "assistant",
            "",
        )
        save_conv_to_history(sess)

    def on_start():
        commit_history_on_start()

    def on_delta(text_so_far):
        if not history_committed:
            commit_history_on_start()
        if assistant_turn is not None:
            set_turn_content(assistant_turn, text_so_far, None)

    conv_id_sent = False

    def generate():
        nonlocal conv_id_sent
        try:
            prior_turns = sess.get("history", [])[:-2] if history_committed else sess.get("history", [])
            for event in stream_genspark_message(
                client=client,
                text_message=message,
                attachments=attachments,
                history=prior_turns,
                model=model,
                web_search=web_search,
                sess=sess,
                on_start=on_start,
                on_delta=on_delta,
            ):
                if is_new_conv and not conv_id_sent:
                    if '"type": "start"' in event or '"type": "chunk"' in event or history_committed:
                        conv_id_sent = True
                        yield f"data: {json.dumps({'type': 'conv_id', 'conv_id': new_local_conv_id})}\n\n"
                yield event
        except GeneratorExit:
            pass
        finally:
            try:
                if history_committed:
                    save_conv_to_history(sess)
            except Exception:
                pass

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "close",
        },
    )


@app.route("/api/abort", methods=["POST"])
def api_abort():
    sess = get_sess()
    if not sess:
        return jsonify({"error": "Oturum bulunamadı."}), 401
    sess["aborted"] = True
    print("[*] Akış durdurma isteği alındı (/api/abort).")
    return jsonify({"success": True})


@app.route("/api/upload", methods=["POST"])
def api_upload():
    sess = get_sess()
    if not sess or not sess.get("client"):
        return jsonify({"error": "Oturum yok"}), 401
    if "file" not in request.files:
        return jsonify({"error": "Dosya eksik"}), 400

    file = request.files["file"]
    filename = file.filename
    file_bytes = file.read()
    mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"

    try:
        att = process_uploaded_file(sess["client"].session, filename, file_bytes, mime)
        print(f"[+] Dosya yüklendi: {filename} ({len(file_bytes)} bayt)")
        return jsonify(att)
    except Exception as e:
        print(f"[-] Dosya yükleme hatası ({filename}): {e}")
        return jsonify({"error": f"Yükleme hatası: {str(e)}"}), 500


@app.route("/api/new_chat", methods=["POST"])
def api_new_chat():
    sess = get_sess()
    if not sess:
        return jsonify({"error": "Oturum bulunamadı."}), 401

    data = request.json or {}
    carry = data.get("carry_history", False)
    carry_conv_id = data.get("conv_id")

    carry_history = None
    if carry:
        if carry_conv_id:
            for c in sess.get("conversations", []):
                if c.get("conv_id") == carry_conv_id:
                    carry_history = list(c["history"])
                    break
        if carry_history is None and sess.get("history"):
            carry_history = list(sess["history"])

    save_conv_to_history(sess)

    new_conv_id = make_local_conv_id()
    sess["active_local_conv_id"] = new_conv_id
    sess["history"] = carry_history or []
    if carry_history:
        save_conv_to_history(sess)

    print(f"[+] Yeni sohbet başlatıldı: {new_conv_id}")
    return jsonify({"success": True, "new_conv_id": new_conv_id, "carried": bool(carry_history)})


@app.route("/api/clear", methods=["POST"])
def api_clear():
    sess = get_sess()
    if not sess:
        return jsonify({"error": "Oturum bulunamadı."}), 401
    save_conv_to_history(sess)
    sess["history"] = []
    sess["active_local_conv_id"] = None
    print("[*] Mevcut sohbet temizlendi.")
    return jsonify({"success": True})


@app.route("/api/history")
def api_history():
    sess = get_sess()
    if not sess:
        return jsonify({"history": [], "conv_id": None, "streaming": False})

    h = sess.get("history", [])
    conv_id = sess.get("active_local_conv_id")
    cleaned = []
    for turn in h:
        entry = {
            "role": turn.get("role", "user"),
            "text": turn.get("text", ""),
            "at": turn.get("at", ""),
            "attachments": turn.get("attachments", []),
        }
        cleaned.append(entry)

    return jsonify({"history": cleaned, "conv_id": conv_id, "streaming": False})


@app.route("/api/conversations")
def api_conversations():
    sess = get_sess()
    if not sess:
        return jsonify({"conversations": []})

    save_conv_to_history(sess)
    convs = sess.get("conversations", [])
    result = []
    for idx, c in enumerate(convs):
        cnt = len(c.get("history", [])) // 2
        result.append({
            "idx": idx,
            "conv_id": c.get("conv_id"),
            "title": c.get("title", "Yeni Sohbet"),
            "count": cnt,
            "msg_count": cnt,
            "created_at": c.get("created_at"),
            "updated_at": c.get("updated_at"),
        })
    return jsonify({"conversations": result, "active_conv_id": sess.get("active_local_conv_id")})


@app.route("/api/conversation/load", methods=["POST"])
def api_conversation_load():
    sess = get_sess()
    if not sess:
        return jsonify({"error": "Oturum bulunamadı."}), 401

    data = request.json or {}
    conv_id = data.get("conv_id")
    idx = data.get("idx")

    convs = sess.get("conversations", [])
    target = None
    if conv_id:
        target = next((c for c in convs if c.get("conv_id") == conv_id), None)
    elif idx is not None and 0 <= idx < len(convs):
        target = convs[idx]

    if not target:
        return jsonify({"error": "Sohbet bulunamadı."}), 404

    save_conv_to_history(sess)
    sess["history"] = list(target.get("history", []))
    sess["active_local_conv_id"] = target.get("conv_id")

    print(f"[*] Sohbet yüklendi: {target.get('title')} ({target.get('conv_id')})")
    return jsonify({"success": True, "conv_id": target.get("conv_id")})


@app.route("/api/conversation/delete", methods=["POST"])
def api_conversation_delete():
    sess = get_sess()
    if not sess:
        return jsonify({"error": "Oturum bulunamadı."}), 401

    data = request.json or {}
    conv_id = data.get("conv_id")
    if not conv_id:
        return jsonify({"error": "conv_id eksik"}), 400

    convs = sess.get("conversations", [])
    sess["conversations"] = [c for c in convs if c.get("conv_id") != conv_id]

    if sess.get("active_local_conv_id") == conv_id:
        sess["history"] = []
        sess["active_local_conv_id"] = None

    print(f"[*] Sohbet silindi: {conv_id}")
    return jsonify({"success": True})


@app.route("/api/conversation/rename", methods=["POST"])
def api_conversation_rename():
    sess = get_sess()
    if not sess:
        return jsonify({"error": "Oturum bulunamadı."}), 401

    data = request.json or {}
    conv_id = data.get("conv_id")
    title = (data.get("title") or "").strip()[:48]
    if not conv_id or not title:
        return jsonify({"error": "Eksik parametre"}), 400

    for c in sess.get("conversations", []):
        if c.get("conv_id") == conv_id:
            c["title"] = title
            c["title_locked"] = True
            print(f"[*] Sohbet yeniden adlandırıldı: {title}")
            return jsonify({"success": True, "title": title})

    return jsonify({"error": "Sohbet bulunamadı."}), 404


@app.route("/api/files")
def api_files():
    sess = get_sess()
    if not sess:
        return jsonify({"files": []})

    seen = set()
    files = []
    all_turns = list(sess.get("history", []))
    for c in sess.get("conversations", []):
        all_turns.extend(c.get("history", []))

    for turn in all_turns:
        for att in turn.get("attachments", []):
            url = att.get("url")
            if url and url not in seen:
                seen.add(url)
                files.append(att)

    return jsonify({"files": files})


@app.route("/api/toggle_feature", methods=["POST"])
def api_toggle_feature():
    sess = get_sess()
    if not sess:
        return jsonify({"error": "Oturum bulunamadı."}), 401

    data = request.json or {}
    feature = data.get("feature")
    value = data.get("value")

    if feature in ["web_search", "auto_attach"]:
        sess[feature] = bool(value)
        return jsonify({"success": True, feature: sess[feature]})

    for k in ["web_search", "auto_attach"]:
        if k in data:
            sess[k] = bool(data[k])

    return jsonify({"success": True})


if __name__ == "__main__":
    log("=" * 65)
    log("  GENSPARK AI - FLASK WEB SUNUCUSU BAŞLATILDI")
    log("=" * 65)
    log(f"  * Web Arayüzü  : http://127.0.0.1:5000")
    log(f"  * Giriş Şifresi: {APP_PASSWORD}")
    log(f"  * Proxy Durumu : {'1 (AÇIK - ' + PROXY_URL.split('@')[-1] + ')' if PROXY_MODE == 1 else '0 (KAPALI - Doğrudan Bağlantı)'}")
    log(f"  * Hesap Dosyası: {ACCOUNTS_FILE}")
    saved = read_saved_accounts()
    log(f"  * Kayıtlı Hesap: {len(saved)} adet ({[s[0] for s in saved]})")
    log("=" * 65 + "\n")
    app.run(host="0.0.0.0", port=5000, threaded=True, debug=False, use_reloader=False)
