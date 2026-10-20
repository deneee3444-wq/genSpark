#!/usr/bin/env python3
"""
Genspark AI - Gemini 4 Argon Canlı Sohbet & Otomasyon Web Uygulaması
=============================================================================
Flask Backend + Modern Responsive templates/index.html UI
- Claude Opus 4.7 & TempMailNinja ile Sıfırdan Hesap Açma
- 100 Kredi Aktivasyonu & Canlı Kredi Takibi
- Kesintisiz Gemini 4 Argon SSE Sohbeti
- Kredi Bittiğinde Otomatik / Onaylı Yeni Hesaba Geçiş Döngüsü
"""

import os
import re
import sys
import time
import json
import uuid
import base64
import random
import string
import ssl
import threading
import datetime
from typing import Optional, Tuple, Dict, List, Generator

import requests as py_requests
from curl_cffi import requests as cffi_requests
from curl_cffi.curl import CurlOpt
import websocket
from flask import Flask, jsonify, request, Response, render_template, stream_with_context

# Windows konsol UTF-8 desteği
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# ==============================================================================
#  BÖLÜM 1: SABİTLER VE DOSYA YOLLARI
# ==============================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
ACCOUNTS_FILE = os.path.join(BASE_DIR, "genspark_hesaplar.txt")

GENSPARK_HOST = "https://www.genspark.ai"
DEFAULT_PASSWORD = "Password123!"

# ==============================================================================
#  PROXY YAPILANDIRMASI
# ==============================================================================
# PROXY_MODE: 0 -> Kapalı (Doğrudan bağlantı), 1 -> Açık (Proxy üzerinden bağlantı)
PROXY_MODE = int(os.environ.get("PROXY_MODE", 1))
PROXY_URL = os.environ.get("PROXY_URL", "http://tghpxuwn:dxrsnpsyz3df@198.23.243.226:6361")

def get_proxies() -> Optional[Dict[str, str]]:
    if PROXY_MODE == 1 and PROXY_URL:
        return {"http": PROXY_URL, "https": PROXY_URL}
    return None

# PixelBunny AI / Claude Opus 4.7
PB_API_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImVzbmRocGFzb3hyd3p4cHpqbGZnIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzIzNDgxNjgsImV4cCI6MjA4NzkyNDE2OH0.cStXgyUmRDoaIctjoH4aNL2DUjjcnZLn_7VFNyEbdzE"
PB_BASE_URL = "https://esndhpasoxrwzxpzjlfg.supabase.co"
PB_DEFAULT_PASSWORD = "SifreniYaz123!"


# ==============================================================================
#  BÖLÜM 2: GLOBAL İŞ DURUMU & OTURUM HAFIZASI
# ==============================================================================
state_lock = threading.Lock()

job_state = {
    "is_running": False,
    "status": "Hazır",
    "step": 0,           # 0: Boşta, 1: OAuth, 2: Mail, 3: Captcha, 4: Kod, 5: Giriş, 6: Tamamlandı
    "attempt": 0,
    "captcha_b64": "",
    "captcha_text": "",
    "email": "",
    "code": "",
    "logs": [],
    "last_account": None,
    "error": None,
    "started_at": None,
}

active_chat = {
    "session": None,          # cffi_requests.Session
    "email": None,
    "balance": 0,
    "history": [],            # [{"role": "user", "content": "..."}, ...]
    "is_credit_exhausted": False,
}


def add_log(msg: str):
    """Zaman damgalı log kaydeder."""
    t_str = datetime.datetime.now().strftime("%H:%M:%S")
    line = f"[{t_str}] {msg}"
    print(line, flush=True)
    with state_lock:
        job_state["logs"].append(line)
        if len(job_state["logs"]) > 400:
            job_state["logs"].pop(0)


def update_state(**kwargs):
    with state_lock:
        for k, v in kwargs.items():
            job_state[k] = v


class CreditExhaustedError(Exception):
    """Genspark 100 ücretsiz kullanım kredisi bittiğinde fırlatılır."""
    pass


class GensparkSessionError(Exception):
    """Genspark web oturumu süresi dolduğunda veya yetkilendirme reddedildiğinde (HTTP 401/403) fırlatılır."""
    pass


def ensure_client_cookies(session: cffi_requests.Session):
    """Genspark Cloudflare ve bot korumasını aşmak için gerekli istemci çerezlerini garantiye alır."""
    try:
        cookies = session.cookies.get_dict()
        if "ai_user" not in cookies:
            rand_id = "".join(random.choices(string.ascii_letters + string.digits + "+", k=22))
            iso_now = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
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


# ==============================================================================
#  BÖLÜM 3: PIXELBUNNY CLAUDE OPUS 4.7 CAPTCHA ÇÖZÜCÜ
# ==============================================================================
class SpamokTempMail:
    def random_email(self, length: int = 15) -> str:
        rand_str = "".join(random.SystemRandom().choice(string.ascii_lowercase + string.digits) for _ in range(length))
        return f"{rand_str}@spamok.com"

    def get_confirm_link(self, mail: str, timeout: int = 30) -> Optional[str]:
        address = mail.replace("@spamok.com", "")
        for _ in range(timeout):
            try:
                r = py_requests.get(f"https://api.spamok.com/v2/EmailBox/{address}", timeout=10)
                for m in r.json().get("mails", []):
                    if "Confirm" in m.get("subject", "") or "Pixel Bunny" in m.get("fromDisplay", ""):
                        email_r = py_requests.get(f"https://api.spamok.com/v2/Email/{address}/{m['id']}", timeout=10)
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


def register_pixelbunny(password: str = PB_DEFAULT_PASSWORD) -> Tuple[Optional[str], Optional[str]]:
    temp = SpamokTempMail()
    email = temp.random_email()
    headers = {
        "apikey": PB_API_KEY,
        "authorization": f"Bearer {PB_API_KEY}",
        "content-type": "application/json;charset=UTF-8",
        "origin": "https://pixelbunny.ai",
        "referer": "https://pixelbunny.ai/",
        "x-client-info": "supabase-js-web/2.98.0",
        "x-supabase-api-version": "2024-01-01",
    }
    r = py_requests.post(f"{PB_BASE_URL}/auth/v1/signup", headers=headers, json={"email": email, "password": password, "data": {}}, timeout=15)
    if r.status_code not in [200, 201]:
        return None, None

    link = temp.get_confirm_link(email, timeout=30)
    if not link:
        return None, None

    conf_resp = py_requests.get(link, allow_redirects=True, timeout=15)
    access_token = None
    if "#" in conf_resp.url:
        fragment = conf_resp.url.split("#", 1)[1]
        for part in fragment.split("&"):
            if part.startswith("access_token="):
                access_token = part.split("=", 1)[1]
                break

    if not access_token:
        login_r = py_requests.post(
            f"{PB_BASE_URL}/auth/v1/token?grant_type=password",
            headers=headers,
            json={"email": email, "password": password},
            timeout=15,
        )
        if login_r.status_code == 200:
            access_token = login_r.json().get("access_token")

    user_id = r.json().get("id") or (r.json().get("user") or {}).get("id")
    return access_token, user_id


class OpusCaptchaSolver:
    def __init__(self):
        self.token = None
        self.user_id = None
        self.model_id = "claude-opus-4.7"

    def _ensure_authenticated(self):
        if not self.token or not self.user_id:
            add_log("[*] Opus 4.7 için PixelBunny oturumu başlatılıyor...")
            self.token, self.user_id = register_pixelbunny()
            if not self.token or not self.user_id:
                raise RuntimeError("PixelBunny Opus 4.7 oturumu açılamadı!")
            add_log(f"[+] PixelBunny hazır! (User: {self.user_id})")

    def _upload_attachment(self, captcha_path: str, image_bytes: bytes) -> dict:
        headers = {
            "authorization": f"Bearer {self.token}",
            "origin": "https://pixelbunny.ai",
            "referer": "https://pixelbunny.ai/",
        }
        # Deneme 1: functions/v1/upload-input (2 deneme, 15s)
        for _ in range(2):
            try:
                with open(captcha_path, "rb") as f:
                    files = {"file": ("captcha.jpg", f, "image/jpeg")}
                    r_up = py_requests.post(f"{PB_BASE_URL}/functions/v1/upload-input", headers=headers, files=files, timeout=15)
                if r_up.status_code == 200:
                    img_url = r_up.json().get("url")
                    if img_url:
                        return {"url": img_url, "type": "image/png"}
            except Exception:
                time.sleep(1)

        # Deneme 2: storage/v1/object/chat-attachments fallback (2 deneme, 15s)
        rand_name = f"cap_{int(time.time())}_{random.randint(100, 999)}.jpg"
        for _ in range(2):
            try:
                st_headers = {"apikey": PB_API_KEY, "authorization": f"Bearer {self.token}"}
                files = {"file": (rand_name, image_bytes, "image/jpeg")}
                r_st = py_requests.post(f"{PB_BASE_URL}/storage/v1/object/chat-attachments/{rand_name}", headers=st_headers, files=files, timeout=15)
                if r_st.status_code in [200, 201]:
                    pub_url = f"{PB_BASE_URL}/storage/v1/object/public/chat-attachments/{rand_name}"
                    return {"name": rand_name, "url": pub_url, "type": "image/jpeg"}
            except Exception:
                time.sleep(1)

        raise RuntimeError("Görsel yüklenemedi (Zaman aşımı / Ağ hatası)")

    def solve(self, image_bytes: bytes, filename: str = "captcha_challenge.jpg") -> str:
        self._ensure_authenticated()

        captcha_path = os.path.join(BASE_DIR, filename)
        with open(captcha_path, "wb") as f:
            f.write(image_bytes)

        add_log("[*] Captcha görseli yükleniyor...")
        attachment = self._upload_attachment(captcha_path, image_bytes)

        # Conversation başlat
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
            raise RuntimeError("Conversation id alınamadı!")

        prompt = (
            "Bu görseldeki CAPTCHA (güvenlik kodu) karakterlerini oku. "
            "Karakterler üstte ve altta veya yamuk olabilir; mantıksal sırasına göre tüm harf ve rakamları yan yana birleştirerek yaz. "
            "SADECE ve SADECE tespit ettiğin harf ve rakamları düz yazı olarak ver. "
            "Hiçbir açıklama, selamlama, markdown, boşluk veya noktalama işareti ekleme."
        )

        add_log("[*] Claude Opus 4.7'ye gönderiliyor...")
        chat_payload = {
            "conversation_id": conv_id,
            "model_id": self.model_id,
            "message": prompt,
            "attachments": [attachment],
            "incognito": False,
            "history": [],
        }

        full_resp = ""
        for _ in range(2):
            try:
                with py_requests.post(
                    f"{PB_BASE_URL}/functions/v1/chat-completion",
                    headers={"authorization": f"Bearer {self.token}", "content-type": "application/json"},
                    json=chat_payload,
                    stream=True,
                    timeout=35,
                ) as res:
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
                        try:
                            data = json.loads(d_str)
                            choices = data.get("choices", [])
                            if choices:
                                c = choices[0].get("delta", {}).get("content", "")
                                if c:
                                    full_resp += c
                        except Exception:
                            pass
                if full_resp:
                    break
            except Exception:
                time.sleep(1)

        clean_text = re.sub(r"[^A-Za-z0-9]", "", full_resp).upper()
        add_log(f"[+] Opus 4.7 Captcha Sonucu: {clean_text}")
        return clean_text


# ==============================================================================
#  BÖLÜM 4: TEMPMALNINJA (GEÇİCİ E-POSTA İSTEMCİSİ - TEST.PY UYUMLU)
# ==============================================================================
class TempMailNinja:
    def __init__(self, locale: str = "en"):
        self.locale = locale
        self.ws_url = f"wss://tm-app.solucioneswc.com:2083/socket.io/?locale={self.locale}&EIO=4&transport=websocket"
        self.headers = [
            "Origin: https://tempmail.ninja",
            "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        ]
        self.ws = None
        self.ack_id = 0
        self.token = None
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
                                add_log(f"[+] Doğrulama Kodu Yakalandı (WebSocket Push): {code}")
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
            addr = res["emailData"].get("address") or res["emailData"].get("emailAddress")
            self.active_email_addr = addr
            self.token = res.get("newToken", self.token)
            self._subscribe_inbox()
            return addr
        raise RuntimeError(f"E-posta üretilemedi: {res}")

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
        res = self._send_event("get_email_message", {"messageId": mid}, timeout=15)
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
    add_log(f"[*] Onay kodu bekleniyor ({timeout}s zaman aşımı)...")
    start = time.time()
    while time.time() - start < timeout:
        if mail_client.received_codes:
            code = mail_client.received_codes.pop(0)
            add_log(f"[+] Doğrulama Kodu Yakalandı (Push): {code}")
            return code

        try:
            messages = mail_client.get_messages()
            if messages:
                for m in messages:
                    mid = m.get("id")
                    detail = mail_client.get_message_detail(mid)
                    if not detail:
                        continue
                    code_match = re.search(r"\b(\d{6})\b", detail.get("subject", "") + " " + detail.get("content", ""))
                    if code_match:
                        code = code_match.group(1)
                        add_log(f"[+] Doğrulama Kodu Yakalandı (Inbox): {code}")
                        return code
        except Exception:
            pass

        time.sleep(2)
    return None


# ==============================================================================
#  BÖLÜM 5: GENSPARK SIFIRDAN HESAP AÇMA & WEB GİRİŞİ
# ==============================================================================
def create_and_login_genspark_account(password: str = DEFAULT_PASSWORD) -> Tuple[cffi_requests.Session, str, int]:
    """
    Sıfırdan tam otomatik hesap açar, onaylatır, oturum açıp 100 kredisini aktifleştirir.
    Döner: (session, email, balance)
    """
    add_log("==================================================")
    add_log("  GENSPARK SIFIRDAN HESAP AÇMA VE GİRİŞ AKIŞI")
    add_log("==================================================")

    session = cffi_requests.Session(impersonate="chrome120")
    session.headers.update({
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "accept-language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
    })

    # 1. Authorize URL al
    update_state(step=1, status="Web OAuth Oturumu Başlatılıyor...")
    add_log("[*] 1/7 Web OAuth akışı (/api/login) başlatılıyor...")
    r0 = session.get(f"{GENSPARK_HOST}/api/login", allow_redirects=False)
    authorize_url = r0.headers.get("location")
    if not authorize_url:
        raise RuntimeError("Genspark Authorize URL alınamadı!")

    r1 = session.get(authorize_url)
    s1 = json.loads(re.search(r"var SETTINGS = ({.*?});", r1.text).group(1))
    csrf1 = s1["csrf"]
    tx1 = s1["transId"]
    tenant_path = s1.get("hosts", {}).get("tenant", "/gensparkad.onmicrosoft.com/b2c_1_new_login")
    b2c_base = f"https://login.genspark.ai{tenant_path}"
    policy = s1.get("hosts", {}).get("policy", "b2c_1_new_login")

    # 2. Kayıt Adımına Geç (SignUp)
    add_log("[*] 2/7 B2C Kayıt (SignUp) adımına geçiliyor...")
    signup_url = f"{b2c_base}/api/CombinedSigninAndSignup/unified?local=signup&csrf_token={csrf1}&tx={tx1}&p={policy}"
    r2 = session.get(signup_url)
    s2 = json.loads(re.search(r"var SETTINGS = ({.*?});", r2.text).group(1))
    csrf_token = s2["csrf"]
    tx = s2["transId"]
    add_log(f"[+] Kayıt Canlı Oturumu Hazır! TX={tx[:30]}...")

    # 3. Geçici E-Posta
    update_state(step=2, status="Geçici E-Posta Alınıyor...")
    add_log("[*] 3/7 TempMailNinja başlatılıyor...")
    mail_client = TempMailNinja(locale="en")
    mail_client.connect()
    email = mail_client.generate_quick_ninja()
    update_state(email=email)
    add_log(f"[+] Geçici E-Posta Hazır: {email}")

    solver = OpusCaptchaSolver()

    try:
        # 4. CAPTCHA Çözme (10 Deneme)
        update_state(step=3, status="CAPTCHA Çözülüyor (Opus 4.7)...")
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
        for attempt in range(1, 11):
            update_state(attempt=attempt, status=f"CAPTCHA Çözülüyor ({attempt}/10)...")
            add_log(f"\n[*] 4/7 CAPTCHA Denemesi ({attempt}/10)...")
            try:
                get_cap_url = f"{b2c_base}/SelfAsserted/DisplayControlAction/vbeta/captchaControlChallengeCode/GetChallenge?tx={tx}&p={policy}&challengeType=Visual&_={int(time.time() * 1000)}"
                r_cap = session.get(get_cap_url, headers=b2c_headers, timeout=15)
                cap_data = r_cap.json()
                ch_id = cap_data.get("challengeId")
                ch_str = cap_data.get("challengeString", "")
                az_reg = cap_data.get("azureregion", "")
                b64_str = ch_str.split(",", 1)[1] if "," in ch_str else ch_str
                image_bytes = base64.b64decode(b64_str)

                # Arayüz için Base64 kaydet
                b64_img = "data:image/jpeg;base64," + base64.b64encode(image_bytes).decode("ascii")
                update_state(captcha_b64=b64_img)

                captcha_text = solver.solve(image_bytes, filename="captcha_challenge.jpg")
                update_state(captcha_text=captcha_text)

                ver_url = f"{b2c_base}/SelfAsserted/DisplayControlAction/vbeta/captchaControlChallengeCode/VerifyChallenge?tx={tx}&p={policy}"
                ver_payload = {
                    "challengeId": ch_id,
                    "captchaEntered": captcha_text,
                    "challengeType": "Visual",
                    "azureRegion": az_reg,
                }
                r_ver = session.post(ver_url, headers=b2c_headers, data=ver_payload, timeout=15)
                res_ver = r_ver.json()
                if str(res_ver.get("isCaptchaSolved")).lower() == "true":
                    captcha_solved = True
                    add_log(f"[+] CAPTCHA Başarıyla Doğrulandı! ({captcha_text})")
                    break
                else:
                    add_log("[-] CAPTCHA eşleşmedi, oturum bozulmadan yeni görsel çekiliyor...")
                    time.sleep(1)
            except Exception as e:
                add_log(f"[!] Deneme ({attempt}) sırasında geçici bağlantı/zaman aşımı hatası: {e}")
                add_log("[*] Oturum korunarak yeni görsel çekiliyor...")
                time.sleep(1.5)

        if not captcha_solved:
            raise RuntimeError("CAPTCHA 10 denemede çözülemedi.")

        # 5. E-Posta Onay Kodu
        update_state(step=4, status="Doğrulama Kodu Bekleniyor...")
        add_log(f"[*] 5/7 E-postaya doğrulama kodu gönderiliyor ({email})...")
        send_code_url = f"{b2c_base}/SelfAsserted/DisplayControlAction/vbeta/emailVerificationControl/SendCode?tx={tx}&p={policy}"
        r_scode = session.post(send_code_url, headers=b2c_headers, data={"email": email})
        if r_scode.json().get("status") != "200":
            raise RuntimeError(f"Doğrulama kodu gönderilemedi: {r_scode.text}")

        code = wait_for_code(mail_client, timeout=180)
        if not code:
            raise RuntimeError("3 dakika içinde e-posta onay kodu gelmedi.")
        update_state(code=code)
        add_log(f"[+] Doğrulama Kodu Onaylanıyor: {code}")

        ver_code_url = f"{b2c_base}/SelfAsserted/DisplayControlAction/vbeta/emailVerificationControl/VerifyCode?tx={tx}&p={policy}"
        r_vcode = session.post(ver_code_url, headers=b2c_headers, data={"email": email, "emailVerificationCode": code})
        if r_vcode.json().get("status") != "200":
            raise RuntimeError(f"Doğrulama kodu onaylanamadı: {r_vcode.text}")

        # 6. SelfAsserted (Kayıt Tamamlama)
        update_state(step=5, status="Hesap Oluşturuluyor...")
        add_log("[*] 6/7 SelfAsserted ile hesap kaydı tamamlanıyor...")
        self_url = f"{b2c_base}/SelfAsserted?tx={tx}&p={policy}"
        self_payload = {
            "email": email,
            "emailVerificationCode": code,
            "captchaControlChallengeCode": captcha_text,
            "newPassword": password,
            "reenterPassword": password,
            "request_type": "RESPONSE",
        }
        r_self = session.post(self_url, headers=b2c_headers, data=self_payload)
        if r_self.json().get("status") != "200":
            raise RuntimeError(f"Hesap kaydı tamamlanamadı: {r_self.text}")

        # Dosyaya kaydet
        try:
            with open(ACCOUNTS_FILE, "a", encoding="utf-8") as f:
                f.write(f"{email}:{password}\n")
            add_log("[+] Hesap 'genspark_hesaplar.txt' dosyasına kaydedildi.")
        except Exception:
            pass

        # 7. Web Oturumu Girişi (/api/auth)
        add_log("[*] 7/7 Web Girişi (/api/auth) tamamlanıyor...")
        confirmed_url = f"{b2c_base}/api/CombinedSigninAndSignup/confirmed?rememberMe=false&csrf_token={csrf_token}&tx={tx}&p={policy}"
        r_conf = session.get(confirmed_url, allow_redirects=False)
        auth_cb = r_conf.headers.get("Location")
        if not auth_cb:
            raise RuntimeError("Confirmed yönlendirme linki alınamadı!")

        session.get(auth_cb, allow_redirects=True)
        session_id = session.cookies.get_dict().get("session_id")
        if not session_id:
            raise RuntimeError("Genspark session_id çerezi alınamadı!")
        add_log(f"[+] Web Girişi Başarılı! (Session: {session_id[:25]}...)")

        # 8. 100 Kredi Aktivasyonu
        add_log("[*] Hesap profili ve 100 hoş geldin kredisi aktifleştiriliyor...")
        session.get(f"{GENSPARK_HOST}/api/user", timeout=10)
        r_bal = session.get(f"{GENSPARK_HOST}/api/payment/get_credit_balance?surface=super_agent", timeout=10)
        bal_data = r_bal.json().get("data", {})
        balance = bal_data.get("balance", 100)
        session.get(f"{GENSPARK_HOST}/api/quota/plan_usage", timeout=10)
        ensure_client_cookies(session)

        add_log(f"[+] 100 Kredi Başarıyla Aktifleştirildi! (Güncel Bakiye: {balance} Kredi)")
        update_state(
            step=6,
            status="Hesap & 100 Kredi Hazır!",
            last_account={"email": email, "password": password, "balance": balance},
        )
        return session, email, balance

    finally:
        mail_client.delete_email(email)
        mail_client.close()


# ==============================================================================
#  BÖLÜM 6: GEMINI 4 ARGON STREAM & SOHBET MOTORU
# ==============================================================================
def stream_genspark_chat(
    session: cffi_requests.Session,
    user_prompt: str,
    history: Optional[List[Dict]] = None,
    model: str = "gemini-4-argon",
) -> Generator[str, None, None]:
    """Genspark ask_proxy endpoint'i üzerinden canlı SSE sohbet yanıtı üretir."""
    if history is None:
        history = []

    msg_id = str(uuid.uuid4())
    req_trace = uuid.uuid4().hex
    span_id = uuid.uuid4().hex[:16]

    messages_payload = []
    for h in history:
        messages_payload.append({
            "role": h.get("role", "user"),
            "id": h.get("id", str(uuid.uuid4())),
            "content": h.get("content", ""),
            "pending": False,
            "sendStatus": "success",
            "_deepDiveStateNegContent": h.get("content", ""),
        })

    messages_payload.append({
        "role": "user",
        "id": msg_id,
        "content": user_prompt,
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

    payload = {
        "ai_chat_model": model,
        "ai_chat_enable_search": True,
        "ai_chat_disable_personalization": False,
        "use_moa_proxy": False,
        "moa_models": [],
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
    if proxies:
        proxy_host = PROXY_URL.split("@")[-1] if "@" in PROXY_URL else PROXY_URL
        add_log(f"[*] Chat isteği proxy üzerinden gönderiliyor ({proxy_host})...")

    res = session.post(ask_url, headers=headers, json=payload, stream=True, proxies=proxies, timeout=60)

    if res.status_code != 200:
        err_body = b""
        try:
            for chunk in res.iter_content(chunk_size=1024):
                if chunk:
                    err_body += chunk
                    if len(err_body) >= 2048:
                        break
        except Exception:
            pass

        err_snippet = err_body.decode("utf-8", errors="replace").strip()
        cf_ray = res.headers.get("cf-ray", "")
        server_hdr = res.headers.get("server", "")
        ctype = res.headers.get("content-type", "")

        add_log(f"[-] ask_proxy HTTP {res.status_code}! Server: {server_hdr} | CF-Ray: {cf_ray} | Type: {ctype}")
        if err_snippet:
            add_log(f"[-] ask_proxy Hata Yanıtı: {err_snippet[:300]}")
        else:
            add_log("[-] ask_proxy Yanıt gövdesi boş döndü (Genspark/Cloudflare TCP sıfırladı veya boş 403 verdi).")

        err_msg = err_snippet[:200] if err_snippet else f"Server={server_hdr}, CF-Ray={cf_ray}"
        if res.status_code in [401, 403]:
            raise GensparkSessionError(f"HTTP {res.status_code}: {err_msg}")
        elif res.status_code == 429:
            raise RuntimeError(f"HTTP 429 Hız Sınırı: {err_msg}")
        else:
            raise RuntimeError(f"HTTP {res.status_code}: {err_msg}")

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
                    raise CreditExhaustedError("Genspark ücretsiz kredi kotası tükendi (ACTION_CREDIT_EXHAUSTED).")

            s_state = msg_obj.get("session_state")
            if s_state and isinstance(s_state, dict) and s_state.get("consume_usage_quota_exceeded"):
                raise CreditExhaustedError("Genspark kullanım kotası aşıldı (consume_usage_quota_exceeded).")

            content = msg_obj.get("content", "")
            if "credit_exhausted" in content or "used all your credits" in content:
                raise CreditExhaustedError("Genspark kredileri tükendi.")

            if content and not streamed_any:
                streamed_any = True
                yield content

        if obj.get("field_name") == "status" and obj.get("field_value") == "FINISHED":
            break


def login_existing_account(email: str, password: str = DEFAULT_PASSWORD) -> Optional[Tuple[cffi_requests.Session, int]]:
    """Kayıtlı mevcut bir hesaba web üzerinden giriş yapar ve oturum ile bakiyeyi döner."""
    session = cffi_requests.Session(impersonate="chrome120")
    session.headers.update({
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "accept-language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
    })
    try:
        r0 = session.get(f"{GENSPARK_HOST}/api/login", allow_redirects=False)
        auth_url = r0.headers.get("location")
        if not auth_url:
            return None

        r1 = session.get(auth_url)
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
            timeout=15,
        )
        if r2.json().get("status") != "200":
            return None

        r3 = session.get(
            f"{b2c_base}/api/CombinedSigninAndSignup/confirmed?rememberMe=false&csrf_token={csrf}&tx={tx}&p={policy}",
            allow_redirects=False,
            timeout=15,
        )
        loc = r3.headers.get("Location")
        if not loc:
            return None
        session.get(loc, allow_redirects=True, timeout=15)

        # Genspark kullanıcı profilini ve kota durumunu yükle (oturum ısıtma)
        try:
            session.get(f"{GENSPARK_HOST}/api/user", timeout=10)
            session.get(f"{GENSPARK_HOST}/api/quota/plan_usage", timeout=10)
        except Exception:
            pass

        # Kredi bakiyesini sorgula
        r_bal = session.get(f"{GENSPARK_HOST}/api/payment/get_credit_balance?surface=super_agent", timeout=10)
        bal = r_bal.json().get("data", {}).get("balance", 0)
        ensure_client_cookies(session)
        return session, bal
    except Exception as e:
        add_log(f"[-] Giriş denemesi hatası ({email}): {e}")
        return None


def reauth_active_session() -> Optional[cffi_requests.Session]:
    """Aktif oturum 401/403 aldığında hesaba sıfırdan şifresiyle tekrar web girişi yapar."""
    with state_lock:
        email = active_chat.get("email")
    if not email:
        return None
    add_log(f"[*] Genspark oturumu yenileniyor: {email}...")
    res = login_existing_account(email, DEFAULT_PASSWORD)
    if res:
        new_session, bal = res
        with state_lock:
            active_chat["session"] = new_session
            active_chat["balance"] = bal
            active_chat["is_credit_exhausted"] = (bal <= 0)
        add_log(f"[+] Oturum başarıyla yenilendi! (Güncel Bakiye: {bal})")
        return new_session
    return None


def auto_connect_latest_account():
    """Başlangıçta genspark_hesaplar.txt dosyasındaki son hesabı otomatik bağlamayı dener."""
    if not os.path.exists(ACCOUNTS_FILE):
        return
    try:
        with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
            lines = [l.strip() for l in f if l.strip() and ":" in l]
        if not lines:
            return
        last_line = lines[-1]
        email, password = last_line.split(":", 1)
        add_log(f"[*] Başlangıçta kayıtlı son hesap kontrol ediliyor: {email}...")
        res = login_existing_account(email, password)
        if res:
            session, bal = res
            with state_lock:
                active_chat["session"] = session
                active_chat["email"] = email
                active_chat["balance"] = bal
                active_chat["is_credit_exhausted"] = (bal <= 0)
            add_log(f"[+] Başlangıç hesabı aktif: {email} ({bal} Kredi)")
    except Exception as e:
        add_log(f"[-] Otomatik son hesap bağlantı uyarısı: {e}")


# ==============================================================================
#  BÖLÜM 7: ARKA PLAN HESAP İŞÇİSİ
# ==============================================================================
def background_account_worker(password: str = DEFAULT_PASSWORD):
    """Arka planda tek seferlik yeni hesap açar ve aktif chat oturumuna bağlar."""
    update_state(
        is_running=True,
        status="Yeni Hesap Açma Başlatılıyor...",
        step=1,
        attempt=0,
        captcha_b64="",
        captcha_text="",
        email="",
        code="",
        error=None,
        started_at=datetime.datetime.now().strftime("%H:%M:%S"),
    )

    try:
        session, email, balance = create_and_login_genspark_account(password=password)
        with state_lock:
            active_chat["session"] = session
            active_chat["email"] = email
            active_chat["balance"] = balance
            active_chat["is_credit_exhausted"] = False

        add_log(f"\n[+] AKTİF SOHBET HESABI GÜNCELLENDİ: {email} ({balance} Kredi)\n")

    except Exception as e:
        err_msg = str(e)
        add_log(f"[HATA] Hesap açma işlemi başarısız: {err_msg}")
        update_state(error=err_msg, status=f"Hata: {err_msg}")

    finally:
        update_state(is_running=False)


# ==============================================================================
#  BÖLÜM 8: FLASK UYGULAMASI & API ENDPOINTLERİ
# ==============================================================================
app = Flask(__name__, template_folder=TEMPLATES_DIR)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/balance", methods=["GET"])
def api_balance():
    with state_lock:
        email = active_chat.get("email")
        balance = active_chat.get("balance", 0)
        is_exhausted = active_chat.get("is_credit_exhausted", False)

    return jsonify({
        "email": email,
        "balance": balance,
        "is_exhausted": is_exhausted,
    })


@app.route("/api/start", methods=["POST"])
def api_start():
    with state_lock:
        if job_state["is_running"]:
            return jsonify({"status": "already_running", "message": "Hesap açma işlemi zaten devam ediyor."}), 200

    data = request.get_json(silent=True) or {}
    password = data.get("password") or DEFAULT_PASSWORD

    t = threading.Thread(target=background_account_worker, args=(password,), daemon=True)
    t.start()
    return jsonify({"status": "started"})


@app.route("/api/status", methods=["GET"])
def api_status():
    with state_lock:
        return jsonify(job_state)


@app.route("/api/chat", methods=["POST"])
def api_chat():
    data = request.get_json(silent=True) or {}
    user_prompt = data.get("message", "").strip()
    if not user_prompt:
        return jsonify({"status": "error", "message": "Boş mesaj gönderilemez"}), 400

    with state_lock:
        session = active_chat.get("session")
        history = list(active_chat.get("history", []))

    if not session:
        return jsonify({
            "status": "no_account",
            "message": "Henüz aktif bir hesap yok. Lütfen önce sol menüden 'Yeni Hesap Aç' butonuna tıklayın.",
        }), 400

    def generate_events():
        full_reply = ""
        try:
            nonlocal session
            streamed = False
            try:
                for delta in stream_genspark_chat(session, user_prompt=user_prompt, history=history):
                    streamed = True
                    full_reply += delta
                    yield f"data: {json.dumps({'delta': delta})}\n\n"
            except GensparkSessionError:
                if not streamed:
                    add_log("[!] Oturum yetkilendirme uyarısı alındı, oturum yenilenip tekrar deneniyor...")
                    refreshed = reauth_active_session()
                    if refreshed:
                        session = refreshed
                        for delta in stream_genspark_chat(session, user_prompt=user_prompt, history=history):
                            streamed = True
                            full_reply += delta
                            yield f"data: {json.dumps({'delta': delta})}\n\n"
                    else:
                        raise RuntimeError("Genspark oturumu geçersiz ve yenilenemedi.")
                else:
                    raise

            # Başarıyla tamamlandı, geçmişe ekle
            if full_reply.strip() and not full_reply.startswith("[HATA]"):
                with state_lock:
                    active_chat["history"].append({"role": "user", "content": user_prompt})
                    active_chat["history"].append({"role": "assistant", "content": full_reply})

                # Canlı bakiyeyi güncelle
                try:
                    r_bal = session.get(f"{GENSPARK_HOST}/api/payment/get_credit_balance?surface=super_agent", timeout=5)
                    if r_bal.status_code == 200:
                        bal = r_bal.json().get("data", {}).get("balance")
                        if bal is not None:
                            with state_lock:
                                active_chat["balance"] = bal
                                if bal <= 0:
                                    active_chat["is_credit_exhausted"] = True
                except Exception:
                    pass

            yield "data: [DONE]\n\n"

        except CreditExhaustedError as ce:
            # Gerçekten kredi mi bitti? Bakiyeyi doğrula (consume_usage_quota_exceeded false positive olabilir)
            real_exhausted = True
            try:
                r_chk = session.get(f"{GENSPARK_HOST}/api/payment/get_credit_balance?surface=super_agent", timeout=5)
                if r_chk.status_code == 200:
                    real_bal = r_chk.json().get("data", {}).get("balance", 0)
                    if real_bal > 0:
                        real_exhausted = False
                        add_log(f"[!] Geçici kota uyarısı alındı ama bakiye hâlâ {real_bal}. Yeni hesap açılmayacak.")
                        with state_lock:
                            active_chat["balance"] = real_bal
            except Exception:
                pass

            if real_exhausted:
                with state_lock:
                    active_chat["is_credit_exhausted"] = True
                yield "data: [CREDIT_EXHAUSTED]\n\n"
            else:
                yield f"data: {json.dumps({'delta': f' [UYARI: Genspark geçici kullanım kotası aşıldı (Bakiye: {real_bal}). Birkaç saniye bekleyip tekrar deneyin.]'})}\n\n"
                yield "data: [DONE]\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'delta': f' [HATA: {e}]'})}\n\n"
            yield "data: [DONE]\n\n"

    return Response(stream_with_context(generate_events()), mimetype="text/event-stream")


@app.route("/api/clear_chat", methods=["POST"])
def api_clear_chat():
    with state_lock:
        active_chat["history"] = []
    return jsonify({"status": "cleared"})


@app.route("/api/clear_logs", methods=["POST"])
def api_clear_logs():
    with state_lock:
        job_state["logs"] = []
    return jsonify({"status": "cleared"})


@app.route("/api/accounts", methods=["GET"])
def api_accounts():
    accounts = []
    if os.path.exists(ACCOUNTS_FILE):
        try:
            with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and ":" in line:
                        parts = line.split(":", 1)
                        accounts.append({
                            "email": parts[0],
                            "password": parts[1],
                            "format": line,
                        })
        except Exception:
            pass
    return jsonify(accounts)


@app.route("/api/download", methods=["GET"])
def api_download():
    content = ""
    if os.path.exists(ACCOUNTS_FILE):
        try:
            with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception:
            pass
    return Response(
        content,
        mimetype="text/plain",
        headers={"Content-Disposition": "attachment;filename=genspark_hesaplar.txt"},
    )


@app.route("/api/switch_account", methods=["POST"])
def api_switch_account():
    data = request.get_json(silent=True) or {}
    email = data.get("email")
    password = data.get("password") or DEFAULT_PASSWORD
    if not email:
        return jsonify({"status": "error", "message": "E-posta belirtilmedi"}), 400

    add_log(f"[*] Kullanıcı hesabı değiştiriliyor: {email}...")
    res = login_existing_account(email, password)
    if not res:
        return jsonify({"status": "error", "message": "Bu hesaba giriş yapılamadı"}), 400

    session, balance = res
    with state_lock:
        active_chat["session"] = session
        active_chat["email"] = email
        active_chat["balance"] = balance
        active_chat["is_credit_exhausted"] = (balance <= 0)

    add_log(f"[+] Hesap başarıyla aktif edildi: {email} ({balance} Kredi)")
    return jsonify({"status": "success", "email": email, "balance": balance})


# ==============================================================================
#  BÖLÜM 9: GİRİŞ NOKTASI
# ==============================================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print("=" * 60)
    print("  GENSPARK AI - GEMINI 4 ARGON & HESAP YÖNETİCİSİ")
    print(f"  Adres: http://localhost:{port} (Tüm arayüzler: 0.0.0.0:{port})")
    print("=" * 60)
    # Arka planda kayıtlı son hesabı kontrol et ve hazırla
    threading.Thread(target=auto_connect_latest_account, daemon=True).start()
    app.run(host="0.0.0.0", port=port, debug=False)
