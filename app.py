#!/usr/bin/env python3
"""
Genspark AI - Canlı Gemini 4 Argon Sohbet & Otomatik Hesap Açıcı (Flask Web Panel)
=============================================================================
Render.com ve Bulut Dağıtımları İçin Hazır Tek Dosya (Standalone) Web Uygulaması.
- Canlı Gemini 4 Argon Sohbeti (Kesintisiz SSE Stream).
- 100 Kredi Tükendiğinde Otomatik veya Onaylı Yeni Hesaba Geçiş Döngüsü.
- Claude Opus 4.7 (PixelBunny) ile 10 CAPTCHA Çözme Denemesi.
- TempMailNinja ile WebSocket tabanlı hızlı onay kodu yakalama.
- 100 Hoş Geldin Kredisinin Otomatik Aktivasyonu (/api/payment/get_credit_balance).
- Canlı CAPTCHA Önizleme, Terminal Logları ve Hesap Tablosu.
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

# Kütüphaneler
import requests as py_requests
from curl_cffi import requests as cffi_requests
import websocket
from flask import Flask, jsonify, request, Response, render_template_string, stream_with_context

# Windows konsol UTF-8 desteği
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# ==============================================================================
#  BÖLÜM 1: SABİTLER & AYARLAR
# ==============================================================================
GENSPARK_HOST = "https://www.genspark.ai"
GENSPARK_B2C_BASE = "https://login.genspark.ai/gensparkad.onmicrosoft.com/b2c_1_new_login"
DEFAULT_PASSWORD = "Password123!"

# PixelBunny AI / Claude Opus 4.7
PB_API_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImVzbmRocGFzb3hyd3p4cHpqbGZnIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzIzNDgxNjgsImV4cCI6MjA4NzkyNDE2OH0.cStXgyUmRDoaIctjoH4aNL2DUjjcnZLn_7VFNyEbdzE"
PB_BASE_URL = "https://esndhpasoxrwzxpzjlfg.supabase.co"
PB_DEFAULT_PASSWORD = "SifreniYaz123!"

ACCOUNTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "genspark_hesaplar.txt")


# ==============================================================================
#  BÖLÜM 2: GLOBAL İŞ DURUMU & OTURUM YÖNETİMİ
# ==============================================================================
state_lock = threading.Lock()

job_state = {
    "is_running": False,
    "status": "Hazır",
    "step": 0,           # 0: Hazır, 1: Oturum, 2: Mail, 3: Captcha, 4: Kod, 5: Kredi/Giriş, 6: Tamamlandı
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

# Aktif Chat Oturumu (Bellekte tutulur)
active_chat = {
    "session": None,          # cffi_requests.Session nesnesi
    "email": None,
    "balance": 0,
    "history": [],            # [{"role": "user", "content": "..."}, ...]
    "is_credit_exhausted": False,
}


def add_log(msg: str):
    """Konsola ve web arayüzü hafızasına zaman damgalı log ekler."""
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


# ==============================================================================
#  BÖLÜM 3: PIXELBUNNY AI / CLAUDE OPUS 4.7 CAPTCHA ÇÖZÜCÜ
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


def upload_pixelbunny_attachment(token: str, image_bytes: bytes, filename: str = "captcha.jpg") -> Optional[dict]:
    headers = {
        "apikey": PB_API_KEY,
        "authorization": f"Bearer {token}",
        "x-client-info": "supabase-js-web/2.98.0",
    }
    files = {"file": (filename, image_bytes, "image/jpeg")}
    r = py_requests.post(
        f"{PB_BASE_URL}/storage/v1/object/chat-attachments/{filename}",
        headers=headers,
        files=files,
        timeout=20,
    )
    if r.status_code in [200, 201]:
        return {
            "name": filename,
            "url": f"{PB_BASE_URL}/storage/v1/object/public/chat-attachments/{filename}",
            "type": "image/jpeg",
            "size": len(image_bytes),
        }
    return None


def create_pixelbunny_conversation(token: str, user_id: str) -> Optional[str]:
    headers = {
        "apikey": PB_API_KEY,
        "authorization": f"Bearer {token}",
        "content-type": "application/json",
        "prefer": "return=representation",
    }
    r = py_requests.post(
        f"{PB_BASE_URL}/rest/v1/chat_conversations",
        headers=headers,
        json={"title": "Captcha Solving", "user_id": user_id},
        timeout=15,
    )
    if r.status_code in [200, 201]:
        data = r.json()
        if isinstance(data, list) and len(data) > 0:
            return data[0].get("id")
        elif isinstance(data, dict):
            return data.get("id")
    return None


def ask_pixelbunny_opus(token: str, conv_id: str, prompt: str, attachment: Optional[dict] = None) -> Optional[str]:
    headers = {
        "apikey": PB_API_KEY,
        "authorization": f"Bearer {token}",
        "content-type": "application/json",
    }
    payload = {
        "message": prompt,
        "conversationId": conv_id,
        "model": "claude-opus-4-7",
        "attachments": [attachment] if attachment else [],
    }
    r = py_requests.post(
        "https://pixelbunny.ai/api/chat",
        headers=headers,
        json=payload,
        stream=True,
        timeout=60,
    )
    full = ""
    for line in r.iter_lines():
        if line:
            decoded = line.decode("utf-8", errors="ignore")
            if decoded.startswith("0:"):
                try:
                    full += json.loads(decoded[2:])
                except Exception:
                    pass
    return full.strip() if full else None


class OpusCaptchaSolver:
    def __init__(self):
        self.token = None
        self.user_id = None

    def initialize(self):
        if not self.token:
            add_log("[*] Opus 4.7 için PixelBunny oturumu başlatılıyor...")
            self.token, self.user_id = register_pixelbunny()
            if not self.token:
                raise RuntimeError("PixelBunny Opus 4.7 başlatılamadı!")
            add_log(f"[+] PixelBunny hazır! (User: {self.user_id})")

    def solve(self, image_bytes: bytes, filename: str = "captcha_challenge.jpg") -> str:
        self.initialize()
        conv_id = create_pixelbunny_conversation(self.token, self.user_id)
        if not conv_id:
            raise RuntimeError("PixelBunny konuşması oluşturulamadı!")

        rand_name = f"cap_{int(time.time())}_{random.randint(100,999)}.jpg"
        attachment = upload_pixelbunny_attachment(self.token, image_bytes, rand_name)
        if not attachment:
            raise RuntimeError("Captcha görseli yüklenemedi!")

        prompt = (
            "Görseldeki CAPTCHA metnini (harf ve rakamlar) tam olarak yaz. "
            "Hiçbir açıklama, selamlama, markdown, boşluk veya noktalama işareti ekleme."
        )
        add_log("[*] Claude Opus 4.7'ye gönderiliyor...")
        raw = ask_pixelbunny_opus(self.token, conv_id, prompt, attachment)
        if not raw:
            raise RuntimeError("Opus 4.7'den yanıt alınamadı!")

        clean = re.sub(r"[^A-Za-z0-9]", "", raw).upper()
        add_log(f"[+] Opus 4.7 Captcha Tahmini: {clean}")
        return clean


# ==============================================================================
#  BÖLÜM 4: TEMPORARY EMAIL (TEMPMALNINJA WEBSOCKET)
# ==============================================================================
def clean_html(html_text: str) -> str:
    if not html_text:
        return ""
    text = re.sub(r"<style[^>]*>.*?</style>", "", html_text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"</?(?:p|div|tr|br|h[1-6])[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    return "\n".join(l.strip() for l in text.splitlines() if l.strip())


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
                msg = self.ws.recv()
                if not msg:
                    continue
                if msg == "2":
                    self.ws.send("3")
                    continue
                if msg.startswith("43"):
                    match = re.match(r"^43(\d+)(.*)$", msg)
                    if match:
                        ack_num = int(match.group(1))
                        payload_str = match.group(2)
                        with self._lock:
                            holder = self._pending_acks.get(ack_num)
                        if holder:
                            try:
                                holder["result"] = json.loads(payload_str)[0]
                            except Exception:
                                holder["result"] = payload_str
                            holder["event"].set()
                elif msg.startswith("42"):
                    payload_str = msg[2:]
                    try:
                        parsed = json.loads(payload_str)
                        evt_name, evt_data = parsed[0], parsed[1] if len(parsed) > 1 else {}
                        if evt_name == "new_email_message":
                            data = evt_data.get("data", {})
                            combined = f"{data.get('subject', '')} {clean_html(data.get('content', ''))}"
                            m = re.search(r"\b(\d{6})\b", combined)
                            if m:
                                code = m.group(1)
                                self.received_codes.append(code)
                                add_log(f"[+] Doğrulama Kodu Yakalandı (WebSocket Push): {code}")
                    except Exception:
                        pass
            except Exception:
                break
        self.is_connected = False

    def _send_event(self, event_name: str, data: dict = None, timeout: int = 15):
        if not self.is_connected or not self.ws:
            return None
        with self._lock:
            self.ack_id += 1
            cur_ack = self.ack_id
            holder = {"event": threading.Event(), "result": None}
            self._pending_acks[cur_ack] = holder

        msg = f"42{cur_ack}" + json.dumps([event_name, data or {}])
        try:
            self.ws.send(msg)
        except Exception:
            return None

        if holder["event"].wait(timeout):
            with self._lock:
                self._pending_acks.pop(cur_ack, None)
            return holder["result"]
        with self._lock:
            self._pending_acks.pop(cur_ack, None)
        return None

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
        res = self._send_event("get_email_message", {"messageId": int(message_id)}, timeout=15)
        if res and isinstance(res, dict) and "emailMessage" in res:
            data = res["emailMessage"].get("data", {})
            return {"subject": data.get("subject", ""), "clean_text": clean_html(data.get("content", ""))}
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


def wait_for_genspark_code(mail_client: TempMailNinja, timeout: int = 180) -> Optional[str]:
    add_log(f"[*] Onay kodu bekleniyor (Zaman aşımı: {timeout}s)...")
    start = time.time()
    while time.time() - start < timeout:
        if mail_client.received_codes:
            code = mail_client.received_codes.pop(0)
            return code

        messages = mail_client.get_messages()
        if messages:
            for m in messages:
                mid = m.get("id")
                detail = mail_client.get_message_detail(mid)
                if not detail:
                    continue
                code_match = re.search(r"\b(\d{6})\b", detail.get("clean_text", "") + " " + detail.get("subject", ""))
                if code_match:
                    code = code_match.group(1)
                    add_log(f"[+] Doğrulama Kodu Yakalandı (Inbox): {code}")
                    return code
        time.sleep(2)
    return None


# ==============================================================================
#  BÖLÜM 5: GENSPARK SIFIRDAN HESAP AÇMA VE WEB GİRİŞİ MOTORU
# ==============================================================================
def create_and_login_genspark_account(password: str = DEFAULT_PASSWORD) -> Tuple[cffi_requests.Session, str, int]:
    """
    Sıfırdan tam otomatik hesap açar, onaylatır, oturum açıp 100 kredisini aktifleştirir.
    Döner: (session, email, balance)
    """
    add_log("==================================================")
    add_log("  GENSPARK YENİ HESAP AÇMA VE GİRİŞ AKIŞI")
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

                # Yerel dizine captcha dosyasını yaz
                try:
                    cap_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "captcha_challenge.jpg")
                    with open(cap_file, "wb") as f:
                        f.write(image_bytes)
                except Exception:
                    pass

                captcha_text = solver.solve(image_bytes)
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
                add_log("[*] Oturum korunarak bir sonraki görsel deneniyor...")
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

        code = wait_for_genspark_code(mail_client, timeout=180)
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
    res = session.post(ask_url, headers=headers, json=payload, stream=True, timeout=60)

    if res.status_code != 200:
        yield f"\n[HATA] ask_proxy HTTP {res.status_code}: {res.text[:200]}\n"
        return

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


# ==============================================================================
#  BÖLÜM 7: ARKA PLAN HESAP İŞÇİSİ (BACKGROUND THREAD)
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
#  BÖLÜM 8: FLASK UYGULAMASI & HTML ŞABLONU (MODERN RESPONSIVE DARK UI)
# ==============================================================================
app = Flask(__name__)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Genspark AI - Gemini 4 Argon Sohbet & Hesap Yöneticisi</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg: #0b0f19;
            --surface: #111827;
            --surface-card: #162032;
            --border: #243247;
            --accent: #6366f1;
            --accent-hover: #4f46e5;
            --accent-glow: rgba(99, 102, 241, 0.25);
            --text-main: #f8fafc;
            --text-sub: #94a3b8;
            --success: #10b981;
            --danger: #ef4444;
            --warning: #f59e0b;
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }

        body {
            font-family: 'Plus Jakarta Sans', sans-serif;
            background-color: var(--bg);
            color: var(--text-main);
            min-height: 100vh;
            display: flex;
            flex-direction: column;
            overflow-x: hidden;
        }

        .ambient-bg {
            position: fixed;
            top: -20%;
            left: 50%;
            transform: translateX(-50%);
            width: 800px;
            height: 400px;
            background: radial-gradient(circle, rgba(99, 102, 241, 0.12) 0%, rgba(11, 15, 25, 0) 70%);
            z-index: -1;
            pointer-events: none;
        }

        header {
            background-color: rgba(17, 24, 39, 0.85);
            backdrop-filter: blur(12px);
            border-bottom: 1px solid var(--border);
            padding: 1rem 1.5rem;
            position: sticky;
            top: 0;
            z-index: 50;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 1rem;
        }

        .brand {
            display: flex;
            align-items: center;
            gap: 0.75rem;
        }

        .logo-icon {
            width: 38px;
            height: 38px;
            background: linear-gradient(135deg, #6366f1, #a855f7);
            border-radius: 10px;
            display: flex;
            align-items: center;
            justify-content: center;
            box-shadow: 0 0 15px var(--accent-glow);
        }

        .brand-title {
            font-size: 1.15rem;
            font-weight: 700;
            letter-spacing: -0.02em;
        }

        .brand-subtitle {
            font-size: 0.75rem;
            color: var(--text-sub);
        }

        .nav-tabs {
            display: flex;
            gap: 0.5rem;
            background: rgba(15, 23, 42, 0.7);
            padding: 4px;
            border-radius: 10px;
            border: 1px solid var(--border);
        }

        .nav-tab {
            padding: 0.5rem 1rem;
            border-radius: 8px;
            font-size: 0.85rem;
            font-weight: 600;
            cursor: pointer;
            border: none;
            background: transparent;
            color: var(--text-sub);
            transition: all 0.2s;
            display: flex;
            align-items: center;
            gap: 6px;
        }

        .nav-tab.active {
            background: var(--accent);
            color: #fff;
            box-shadow: 0 2px 8px var(--accent-glow);
        }

        .header-badges {
            display: flex;
            align-items: center;
            gap: 0.75rem;
        }

        .badge-account {
            display: flex;
            align-items: center;
            gap: 8px;
            background: rgba(16, 185, 129, 0.12);
            border: 1px solid rgba(16, 185, 129, 0.3);
            color: var(--success);
            padding: 0.4rem 0.8rem;
            border-radius: 999px;
            font-size: 0.8rem;
            font-weight: 600;
        }

        .badge-credits {
            display: flex;
            align-items: center;
            gap: 6px;
            background: rgba(99, 102, 241, 0.15);
            border: 1px solid rgba(99, 102, 241, 0.3);
            color: #a5b4fc;
            padding: 0.4rem 0.8rem;
            border-radius: 999px;
            font-size: 0.8rem;
            font-weight: 700;
        }

        main {
            flex: 1;
            max-width: 1300px;
            width: 100%;
            margin: 0 auto;
            padding: 1.5rem;
            display: flex;
            flex-direction: column;
            gap: 1.5rem;
        }

        .tab-content {
            display: none;
            width: 100%;
        }

        .tab-content.active {
            display: flex;
            flex-direction: column;
            gap: 1.5rem;
        }

        /* SOHBET ARAYÜZÜ (CHAT UI) */
        .chat-container {
            background-color: var(--surface);
            border: 1px solid var(--border);
            border-radius: 16px;
            height: calc(100vh - 180px);
            min-height: 550px;
            display: flex;
            flex-direction: column;
            overflow: hidden;
            box-shadow: 0 10px 30px rgba(0,0,0,0.4);
        }

        .chat-header {
            padding: 0.85rem 1.25rem;
            background: rgba(15, 23, 42, 0.6);
            border-bottom: 1px solid var(--border);
            display: flex;
            align-items: center;
            justify-content: space-between;
        }

        .chat-model-info {
            display: flex;
            align-items: center;
            gap: 8px;
            font-size: 0.85rem;
            font-weight: 600;
            color: #c7d2fe;
        }

        .chat-controls {
            display: flex;
            align-items: center;
            gap: 1rem;
        }

        .auto-switch-toggle {
            display: flex;
            align-items: center;
            gap: 8px;
            font-size: 0.8rem;
            color: var(--text-sub);
            cursor: pointer;
            user-select: none;
        }

        .auto-switch-toggle input {
            cursor: pointer;
            accent-color: var(--accent);
        }

        .chat-messages {
            flex: 1;
            padding: 1.25rem;
            overflow-y: auto;
            display: flex;
            flex-direction: column;
            gap: 1.25rem;
            scroll-behavior: smooth;
        }

        .message-row {
            display: flex;
            gap: 0.75rem;
            max-width: 85%;
        }

        .message-row.user {
            align-self: flex-end;
            flex-direction: row-reverse;
        }

        .message-row.assistant {
            align-self: flex-start;
        }

        .avatar {
            width: 34px;
            height: 34px;
            border-radius: 10px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 700;
            font-size: 0.8rem;
            flex-shrink: 0;
        }

        .avatar.user {
            background: #3b82f6;
            color: #fff;
        }

        .avatar.assistant {
            background: linear-gradient(135deg, #6366f1, #8b5cf6);
            color: #fff;
        }

        .msg-bubble {
            padding: 0.85rem 1.15rem;
            border-radius: 14px;
            font-size: 0.95rem;
            line-height: 1.55;
            white-space: pre-wrap;
            word-break: break-word;
        }

        .message-row.user .msg-bubble {
            background-color: var(--accent);
            color: #ffffff;
            border-top-right-radius: 4px;
        }

        .message-row.assistant .msg-bubble {
            background-color: var(--surface-card);
            border: 1px solid var(--border);
            color: var(--text-main);
            border-top-left-radius: 4px;
        }

        .chat-input-area {
            padding: 1rem 1.25rem;
            background: rgba(15, 23, 42, 0.75);
            border-top: 1px solid var(--border);
            display: flex;
            gap: 0.75rem;
            align-items: flex-end;
        }

        .chat-input {
            flex: 1;
            background: #0b0f19;
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 0.75rem 1rem;
            color: var(--text-main);
            font-family: inherit;
            font-size: 0.95rem;
            resize: none;
            outline: none;
            max-height: 140px;
            min-height: 44px;
        }

        .chat-input:focus {
            border-color: var(--accent);
            box-shadow: 0 0 0 2px var(--accent-glow);
        }

        .btn-send {
            background: var(--accent);
            color: #fff;
            border: none;
            border-radius: 12px;
            padding: 0.75rem 1.4rem;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s;
            display: flex;
            align-items: center;
            gap: 6px;
            height: 44px;
        }

        .btn-send:hover {
            background: var(--accent-hover);
        }

        .btn-send:disabled {
            opacity: 0.5;
            cursor: not-allowed;
        }

        /* HESAP YÖNETİCİSİ PANELİ (DASHBOARD) */
        .grid-dashboard {
            display: grid;
            grid-template-columns: 360px 1fr;
            gap: 1.5rem;
        }

        @media (max-width: 900px) {
            .grid-dashboard {
                grid-template-columns: 1fr;
            }
            .chat-container {
                height: calc(100vh - 220px);
            }
        }

        .card {
            background-color: var(--surface);
            border: 1px solid var(--border);
            border-radius: 14px;
            padding: 1.25rem;
            display: flex;
            flex-direction: column;
            gap: 1rem;
        }

        .card-title {
            font-size: 1rem;
            font-weight: 700;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }

        .btn-primary {
            background: var(--accent);
            color: #fff;
            border: none;
            border-radius: 10px;
            padding: 0.75rem 1rem;
            font-size: 0.9rem;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            width: 100%;
        }

        .btn-primary:hover {
            background: var(--accent-hover);
            box-shadow: 0 0 15px var(--accent-glow);
        }

        .btn-primary:disabled {
            opacity: 0.5;
            cursor: not-allowed;
        }

        .captcha-box {
            background: var(--surface-card);
            border: 1px solid var(--border);
            border-radius: 10px;
            padding: 1rem;
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 0.75rem;
            min-height: 140px;
            justify-content: center;
        }

        .captcha-img {
            max-width: 100%;
            height: auto;
            border-radius: 6px;
            border: 1px solid rgba(255, 255, 255, 0.1);
        }

        .terminal-container {
            background: #070a12;
            border: 1px solid var(--border);
            border-radius: 12px;
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.85rem;
            display: flex;
            flex-direction: column;
            height: 380px;
            overflow: hidden;
        }

        .terminal-header {
            background: #0d131f;
            padding: 0.5rem 1rem;
            border-bottom: 1px solid var(--border);
            display: flex;
            justify-content: space-between;
            align-items: center;
            color: var(--text-sub);
            font-size: 0.75rem;
        }

        .terminal-body {
            flex: 1;
            padding: 1rem;
            overflow-y: auto;
            color: #d1d5db;
            white-space: pre-wrap;
            line-height: 1.5;
        }

        .table-container {
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 14px;
            overflow: hidden;
        }

        table {
            width: 100%;
            border-collapse: collapse;
            text-align: left;
            font-size: 0.85rem;
        }

        th {
            background: rgba(15, 23, 42, 0.6);
            padding: 0.75rem 1rem;
            color: var(--text-sub);
            font-weight: 600;
            border-bottom: 1px solid var(--border);
        }

        td {
            padding: 0.75rem 1rem;
            border-bottom: 1px solid var(--border);
        }

        tr:last-child td {
            border-bottom: none;
        }

        .btn-copy {
            background: rgba(99, 102, 241, 0.15);
            border: 1px solid rgba(99, 102, 241, 0.3);
            color: #a5b4fc;
            padding: 0.3rem 0.6rem;
            border-radius: 6px;
            font-size: 0.75rem;
            cursor: pointer;
            transition: all 0.2s;
        }

        .btn-copy:hover {
            background: var(--accent);
            color: #fff;
        }

        /* MODAL KREDİ BİTTİ DİYALOĞU */
        .modal-overlay {
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: rgba(0, 0, 0, 0.75);
            backdrop-filter: blur(4px);
            display: none;
            align-items: center;
            justify-content: center;
            z-index: 100;
        }

        .modal-overlay.active {
            display: flex;
        }

        .modal-card {
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 16px;
            padding: 1.75rem;
            max-width: 440px;
            width: 90%;
            display: flex;
            flex-direction: column;
            gap: 1.25rem;
            box-shadow: 0 20px 40px rgba(0,0,0,0.6);
            animation: popIn 0.2s ease-out;
        }

        @keyframes popIn {
            from { opacity: 0; transform: scale(0.95); }
            to { opacity: 1; transform: scale(1); }
        }

        .modal-title {
            font-size: 1.15rem;
            font-weight: 700;
            color: var(--warning);
            display: flex;
            align-items: center;
            gap: 8px;
        }

        .modal-body {
            font-size: 0.9rem;
            color: var(--text-sub);
            line-height: 1.5;
        }

        .modal-actions {
            display: flex;
            gap: 0.75rem;
            justify-content: flex-end;
        }

        .btn-secondary {
            background: transparent;
            border: 1px solid var(--border);
            color: var(--text-sub);
            padding: 0.6rem 1rem;
            border-radius: 8px;
            cursor: pointer;
            font-weight: 600;
        }

        .btn-secondary:hover {
            background: rgba(255, 255, 255, 0.05);
            color: var(--text-main);
        }
    </style>
</head>
<body>
    <div class="ambient-bg"></div>

    <header>
        <div class="brand">
            <div class="logo-icon">
                <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="m12 3-1.912 5.813a2 2 0 0 1-1.275 1.275L3 12l5.813 1.912a2 2 0 0 1 1.275 1.275L12 21l1.912-5.813a2 2 0 0 1 1.275-1.275L21 12l-5.813-1.912a2 2 0 0 1-1.275-1.275L12 3Z"/></svg>
            </div>
            <div>
                <div class="brand-title">Genspark AI Suite</div>
                <div class="brand-subtitle">Gemini 4 Argon & Otomatik Hesap Açıcı</div>
            </div>
        </div>

        <div class="nav-tabs">
            <button class="nav-tab active" onclick="switchTab('chatTab')">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
                Canlı Sohbet
            </button>
            <button class="nav-tab" onclick="switchTab('managerTab')">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect width="18" height="18" x="3" y="3" rx="2"/><path d="M9 3v18"/><path d="m14 9 3 3-3 3"/></svg>
                Hesap Yöneticisi & Loglar
            </button>
        </div>

        <div class="header-badges">
            <div class="badge-account" id="badgeAccount">
                <span style="display:inline-block; width:8px; height:8px; border-radius:50%; background:currentColor;"></span>
                <span id="badgeAccountText">Hesap: Bekleniyor</span>
            </div>
            <div class="badge-credits" id="badgeCredits">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 6v12"/><path d="M16 10H8"/></svg>
                <span id="badgeCreditsText">Kredi: 0</span>
            </div>
        </div>
    </header>

    <main>
        <!-- SEKME 1: CANLI GEMINI 4 ARGON SOHBETİ -->
        <div id="chatTab" class="tab-content active">
            <div class="chat-container">
                <div class="chat-header">
                    <div class="chat-model-info">
                        <span style="display:inline-block; width:8px; height:8px; border-radius:50%; background:#10b981;"></span>
                        Model: <strong>gemini-4-argon</strong> (Genspark Agent Proxy)
                    </div>
                    <div class="chat-controls">
                        <label class="auto-switch-toggle" title="Kredi bittiğinde sormadan doğrudan yeni hesap açar">
                            <input type="checkbox" id="autoSwitchToggle" checked>
                            <span>Kredi bitince otomatik yeni hesaba geç</span>
                        </label>
                        <button class="btn-copy" onclick="clearChat()">Sohbeti Temizle</button>
                    </div>
                </div>

                <div class="chat-messages" id="chatMessages">
                    <div class="message-row assistant">
                        <div class="avatar assistant">G</div>
                        <div class="msg-bubble">Merhaba! Ben Gemini 4 Argon. Genspark üzerinden 100 ücretsiz kullanım kredisiyle aktifleştirildim. Bugün ne hakkında konuşmak istersiniz?</div>
                    </div>
                </div>

                <div class="chat-input-area">
                    <textarea class="chat-input" id="chatInput" placeholder="Gemini 4 Argon'a bir mesaj yazın... (Enter: Gönder, Shift+Enter: Yeni Satır)" rows="1" onkeydown="handleInputKey(event)"></textarea>
                    <button class="btn-send" id="btnSend" onclick="sendMessage()">
                        <span>Gönder</span>
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m22 2-7 20-4-9-9-4Z"/><path d="M22 2 11 13"/></svg>
                    </button>
                </div>
            </div>
        </div>

        <!-- SEKME 2: HESAP AÇICI & TERMINAL DASHBOARD -->
        <div id="managerTab" class="tab-content">
            <div class="grid-dashboard">
                <div class="card">
                    <div class="card-title">
                        <span>Manuel Hesap Açıcı</span>
                        <span id="workerIndicator" style="font-size: 0.75rem; color: var(--text-sub);">Boşta</span>
                    </div>
                    <p style="font-size: 0.85rem; color: var(--text-sub); line-height: 1.4;">
                        Claude Opus 4.7 & TempMailNinja ile sıfırdan 100 kredili onaylı hesap açar ve chat oturumuna bağlar.
                    </p>
                    <button class="btn-primary" id="btnStartRegister" onclick="startNewAccount()">
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 5v14"/><path d="M5 12h14"/></svg>
                        Yeni Hesap Aç ve Bağla
                    </button>

                    <div class="captcha-box">
                        <div id="captchaPlaceholder" style="font-size: 0.8rem; color: var(--text-sub); text-align: center;">
                            Görsel CAPTCHA burada önizlenir
                        </div>
                        <img id="captchaImage" class="captcha-img" style="display:none;" src="" alt="CAPTCHA Challenge">
                        <div id="captchaPrediction" style="font-size: 0.8rem; font-weight: 700; color: #a5b4fc;"></div>
                    </div>

                    <div style="font-size: 0.8rem; display: flex; flex-direction: column; gap: 4px; color: var(--text-sub);">
                        <div>E-Posta: <strong id="valEmail" style="color:var(--text-main);">-</strong></div>
                        <div>Onay Kodu: <strong id="valCode" style="color:var(--text-main);">-</strong></div>
                        <div>Aşama: <strong id="valStatus" style="color:var(--accent);">-</strong></div>
                    </div>
                </div>

                <div class="terminal-container">
                    <div class="terminal-header">
                        <span>CANLI İŞLEM LOGLARI</span>
                        <button class="btn-copy" onclick="clearLogs()">Temizle</button>
                    </div>
                    <div class="terminal-body" id="terminalBody">Sistem hazır. Loglar burada akacak...</div>
                </div>
            </div>

            <div class="table-container">
                <div style="padding: 1rem 1.25rem; border-bottom: 1px solid var(--border); display: flex; justify-content: space-between; align-items: center;">
                    <div style="font-weight: 700; font-size: 0.95rem;">Kayıtlı Genspark Hesapları (genspark_hesaplar.txt)</div>
                    <div style="display: flex; gap: 0.5rem;">
                        <button class="btn-copy" onclick="copyAllAccounts()">Tümünü Kopyala</button>
                        <a href="/api/download" class="btn-copy" style="text-decoration:none;">TXT İndir</a>
                    </div>
                </div>
                <table>
                    <thead>
                        <tr>
                            <th style="width: 50px;">#</th>
                            <th>E-Posta</th>
                            <th>Şifre</th>
                            <th>Biçim</th>
                            <th style="width: 80px;">İşlem</th>
                        </tr>
                    </thead>
                    <tbody id="accountsTableBody">
                        <tr><td colspan="5" style="text-align: center; color: var(--text-sub);">Yükleniyor...</td></tr>
                    </tbody>
                </table>
            </div>
        </div>
    </main>

    <!-- KREDİ TÜKENDİ MODAL PENCERESİ -->
    <div class="modal-overlay" id="creditExhaustedModal">
        <div class="modal-card">
            <div class="modal-title">
                <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
                Hesap Kredisi Tükendi!
            </div>
            <div class="modal-body">
                Mevcut Genspark hesabının 100 ücretsiz kullanım kredisi tamamlandı. Sohbete kesintisiz devam etmek için <strong>yeni bir hesap açılsın mı?</strong>
            </div>
            <div class="modal-actions">
                <button class="btn-secondary" onclick="closeModal()">İptal</button>
                <button class="btn-primary" style="width:auto; padding: 0.6rem 1.2rem;" onclick="confirmSwitchNewAccount()">Evet, Yeni Hesap Aç ve Devam Et</button>
            </div>
        </div>
    </div>

    <script>
        let isGenerating = false;
        let lastLogCount = 0;

        function switchTab(tabId) {
            document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.nav-tab').forEach(b => b.classList.remove('active'));
            document.getElementById(tabId).classList.add('active');
            event.currentTarget.classList.add('active');
        }

        function handleInputKey(e) {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendMessage();
            }
        }

        function clearChat() {
            document.getElementById('chatMessages').innerHTML = `
                <div class="message-row assistant">
                    <div class="avatar assistant">G</div>
                    <div class="msg-bubble">Sohbet geçmişi temizlendi. Size nasıl yardımcı olabilirim?</div>
                </div>
            `;
            fetch('/api/clear_chat', { method: 'POST' });
        }

        function closeModal() {
            document.getElementById('creditExhaustedModal').classList.remove('active');
        }

        function showCreditModal() {
            const autoSwitch = document.getElementById('autoSwitchToggle').checked;
            if (autoSwitch) {
                confirmSwitchNewAccount();
            } else {
                document.getElementById('creditExhaustedModal').classList.add('active');
            }
        }

        async function confirmSwitchNewAccount() {
            closeModal();
            appendAssistantMessage("⏳ Kredi tükendi. Yeni bir Genspark hesabı açılıyor ve 100 kredi aktifleştiriliyor...");
            try {
                await fetch('/api/start', { method: 'POST' });
                // Hesap açılana kadar bekle
                pollUntilAccountReady();
            } catch (err) {
                appendAssistantMessage("❌ Yeni hesap açma başlatılamadı: " + err);
            }
        }

        async function pollUntilAccountReady() {
            const interval = setInterval(async () => {
                const res = await fetch('/api/status');
                const st = await res.json();
                if (!st.is_running && st.step === 6) {
                    clearInterval(interval);
                    appendAssistantMessage("✅ Yeni hesap başarıyla bağlandı! 100 kredi hazır. Kaldığımız yerden devam edebilirsiniz.");
                    updateBalanceHeader();
                } else if (!st.is_running && st.error) {
                    clearInterval(interval);
                    appendAssistantMessage("❌ Yeni hesap açılırken hata: " + st.error);
                }
            }, 1500);
        }

        function appendUserMessage(text) {
            const container = document.getElementById('chatMessages');
            const row = document.createElement('div');
            row.className = 'message-row user';
            row.innerHTML = `<div class="avatar user">S</div><div class="msg-bubble">${escapeHtml(text)}</div>`;
            container.appendChild(row);
            container.scrollTop = container.scrollHeight;
        }

        function appendAssistantMessage(text) {
            const container = document.getElementById('chatMessages');
            const row = document.createElement('div');
            row.className = 'message-row assistant';
            row.innerHTML = `<div class="avatar assistant">G</div><div class="msg-bubble">${escapeHtml(text)}</div>`;
            container.appendChild(row);
            container.scrollTop = container.scrollHeight;
            return row.querySelector('.msg-bubble');
        }

        function escapeHtml(str) {
            return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
        }

        async function sendMessage() {
            const input = document.getElementById('chatInput');
            const prompt = input.value.trim();
            if (!prompt || isGenerating) return;

            input.value = '';
            appendUserMessage(prompt);

            isGenerating = true;
            document.getElementById('btnSend').disabled = true;

            const bubble = appendAssistantMessage("");
            let assistantText = "";

            try {
                const response = await fetch('/api/chat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ message: prompt })
                });

                if (response.status === 402) {
                    // Credit exhausted
                    bubble.innerText = "[UYARI] Bu hesabın 100 kredisi tükendi!";
                    showCreditModal();
                    return;
                }

                if (!response.ok) {
                    const err = await response.json();
                    bubble.innerText = "Hata: " + (err.message || response.statusText);
                    return;
                }

                const reader = response.body.getReader();
                const decoder = new TextDecoder('utf-8');

                while (true) {
                    const { done, value } = await reader.read();
                    if (done) break;
                    const chunk = decoder.decode(value, { stream: true });
                    
                    const lines = chunk.split('\\n');
                    for (const line of lines) {
                        if (line.startsWith('data: ')) {
                            const dataStr = line.slice(6);
                            if (dataStr === '[DONE]') break;
                            if (dataStr === '[CREDIT_EXHAUSTED]') {
                                bubble.innerText += "\\n\\n[!] Hesap kredisi tükendi!";
                                showCreditModal();
                                return;
                            }
                            try {
                                const parsed = JSON.parse(dataStr);
                                if (parsed.delta) {
                                    assistantText += parsed.delta;
                                    bubble.innerText = assistantText;
                                    document.getElementById('chatMessages').scrollTop = document.getElementById('chatMessages').scrollHeight;
                                }
                            } catch (e) {
                                assistantText += dataStr;
                                bubble.innerText = assistantText;
                            }
                        }
                    }
                }
            } catch (err) {
                bubble.innerText = "Bağlantı hatası: " + err;
            } finally {
                isGenerating = false;
                document.getElementById('btnSend').disabled = false;
                updateBalanceHeader();
            }
        }

        async function updateBalanceHeader() {
            try {
                const res = await fetch('/api/balance');
                const data = await res.json();
                if (data.email) {
                    document.getElementById('badgeAccountText').innerText = "Hesap: " + data.email;
                    document.getElementById('badgeCreditsText').innerText = "Kredi: " + data.balance + " / 100";
                } else {
                    document.getElementById('badgeAccountText').innerText = "Hesap: Yok (Yeni Açın)";
                    document.getElementById('badgeCreditsText').innerText = "Kredi: 0";
                }
            } catch (e) {}
        }

        async function startNewAccount() {
            document.getElementById('btnStartRegister').disabled = true;
            try {
                await fetch('/api/start', { method: 'POST' });
            } catch (e) {}
        }

        async function clearLogs() {
            await fetch('/api/clear_logs', { method: 'POST' });
            document.getElementById('terminalBody').innerText = '';
        }

        async function fetchStatus() {
            try {
                const res = await fetch('/api/status');
                const data = await res.json();

                const isRunning = data.is_running;
                document.getElementById('btnStartRegister').disabled = isRunning;
                document.getElementById('workerIndicator').innerText = isRunning ? "Çalışıyor..." : "Boşta";

                // Captcha
                if (data.captcha_b64) {
                    document.getElementById('captchaPlaceholder').style.display = 'none';
                    const img = document.getElementById('captchaImage');
                    img.src = data.captcha_b64;
                    img.style.display = 'block';
                }
                if (data.captcha_text) {
                    document.getElementById('captchaPrediction').innerText = "Opus 4.7: " + data.captcha_text;
                }

                document.getElementById('valEmail').innerText = data.email || '-';
                document.getElementById('valCode').innerText = data.code || '-';
                document.getElementById('valStatus').innerText = data.status || 'Boşta';

                // Loglar
                const terminal = document.getElementById('terminalBody');
                if (data.logs && data.logs.length !== lastLogCount) {
                    terminal.innerText = data.logs.join('\\n');
                    terminal.scrollTop = terminal.scrollHeight;
                    lastLogCount = data.logs.length;
                }

            } catch (err) {}
        }

        async function fetchAccounts() {
            try {
                const res = await fetch('/api/accounts');
                const list = await res.json();
                const tbody = document.getElementById('accountsTableBody');

                if (!list || list.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="5" style="text-align: center; color: var(--text-sub);">Henüz hesap yok.</td></tr>';
                    return;
                }

                tbody.innerHTML = list.map((acc, idx) => `
                    <tr>
                        <td style="color: var(--text-sub);">${idx + 1}</td>
                        <td class="font-mono"><strong>${acc.email}</strong></td>
                        <td class="font-mono">${acc.password}</td>
                        <td class="font-mono" style="color: #a5b4fc;">${acc.format}</td>
                        <td><button class="btn-copy" onclick="copyText('${acc.format}')">Kopyala</button></td>
                    </tr>
                `).reverse().join('');
            } catch (err) {}
        }

        function copyText(txt) {
            navigator.clipboard.writeText(txt);
            alert("Kopyalandı: " + txt);
        }

        async function copyAllAccounts() {
            const res = await fetch('/api/accounts');
            const list = await res.json();
            if (list && list.length > 0) {
                copyText(list.map(a => a.format).join('\\n'));
            }
        }

        // Periyodik polling
        setInterval(fetchStatus, 1000);
        setInterval(fetchAccounts, 4000);
        setInterval(updateBalanceHeader, 5000);

        fetchStatus();
        fetchAccounts();
        updateBalanceHeader();
    </script>
</body>
</html>
"""


# ==============================================================================
#  BÖLÜM 9: FLASK API ENDPOINTLERİ
# ==============================================================================
@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)


@app.route("/api/balance", methods=["GET"])
def api_balance():
    with state_lock:
        session = active_chat.get("session")
        email = active_chat.get("email")
        balance = active_chat.get("balance", 0)

    # Canlı güncel bakiyeyi sorgulamayı dene
    if session:
        try:
            r = session.get(f"{GENSPARK_HOST}/api/payment/get_credit_balance?surface=super_agent", timeout=5)
            if r.status_code == 200:
                balance = r.json().get("data", {}).get("balance", balance)
                with state_lock:
                    active_chat["balance"] = balance
        except Exception:
            pass

    return jsonify({
        "email": email,
        "balance": balance,
        "is_exhausted": active_chat.get("is_credit_exhausted", False),
    })


@app.route("/api/start", methods=["POST"])
def api_start():
    with state_lock:
        if job_state["is_running"]:
            return jsonify({"status": "already_running"}), 400

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
        email = active_chat.get("email")
        history = list(active_chat.get("history", []))

    if not session:
        return jsonify({
            "status": "no_account",
            "message": "Henüz aktif bir hesap yok. Lütfen önce 'Yeni Hesap Aç' butonuna basın.",
        }), 400

    def generate_events():
        full_reply = ""
        try:
            for delta in stream_genspark_chat(session, user_prompt=user_prompt, history=history):
                full_reply += delta
                yield f"data: {json.dumps({'delta': delta})}\n\n"

            # Başarıyla tamamlandı, geçmişi kaydet
            with state_lock:
                active_chat["history"].append({"role": "user", "content": user_prompt})
                active_chat["history"].append({"role": "assistant", "content": full_reply})

            yield "data: [DONE]\n\n"

        except CreditExhaustedError:
            with state_lock:
                active_chat["is_credit_exhausted"] = True
            yield "data: [CREDIT_EXHAUSTED]\n\n"

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


# ==============================================================================
#  BÖLÜM 10: GİRİŞ NOKTASI
# ==============================================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print("=" * 60)
    print("  GENSPARK AI - GEMINI 4 ARGON & HESAP YÖNETİCİSİ")
    print(f"  Adres: http://localhost:{port} (Tüm arayüzler: 0.0.0.0:{port})")
    print("=" * 60)
    app.run(host="0.0.0.0", port=port, debug=False)
