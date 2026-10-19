#!/usr/bin/env python3
"""
Genspark AI - Web Otomasyon Paneli (Flask + Claude Opus 4.7 + TempMailNinja)
=============================================================================
Render.com ve Bulut Dağıtımları İçin Hazır Tek Dosya (Standalone) Web Uygulaması.
- Dahili Flask Sunucusu + HTML/CSS/JS Arayüzü (Ekstra şablon klasörü gerektirmez).
- Canlı CAPTCHA Önizlemesi, Opus 4.7 Tahmini ve 10 Deneme Desteği.
- Anlık Akıcı Terminal Log Ekranı (Polling ile gerçek zamanlı izleme).
- Açılan Hesaplar Tablosu, Tek Tıkla Kopyalama ve TXT İndirme.
"""

import os
import re
import sys
import time
import json
import base64
import random
import string
import ssl
import threading
import datetime
from typing import Optional, Tuple

import requests
import websocket
from flask import Flask, jsonify, request, Response, render_template_string

# Windows konsol desteği
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# ==============================================================================
#  BÖLÜM 1: GENSPARK & ENTEGRASYON SABİTLERİ
# ==============================================================================
GENSPARK_B2C_BASE = "https://login.genspark.ai/gensparkad.onmicrosoft.com/B2C_1_new_login"
CLIENT_ID = "536a4e98-fd24-4cbc-a67b-417e209e0080"
REDIRECT_URI = "https://www.genspark.ai/api/auth/gensparkaio"
DEFAULT_PASSWORD = "Password123!"

PB_API_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImVzbmRocGFzb3hyd3p4cHpqbGZnIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzIzNDgxNjgsImV4cCI6MjA4NzkyNDE2OH0.cStXgyUmRDoaIctjoH4aNL2DUjjcnZLn_7VFNyEbdzE"
PB_BASE_URL = "https://esndhpasoxrwzxpzjlfg.supabase.co"
PB_DEFAULT_PASSWORD = "SifreniYaz123!"

ACCOUNTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "genspark_hesaplar.txt")


# ==============================================================================
#  BÖLÜM 2: GLOBAL İŞ DURUMU (THREAD-SAFE STATE)
# ==============================================================================
state_lock = threading.Lock()
job_state = {
    "is_running": False,
    "status": "Hazır",
    "step": 0,           # 0: Hazır, 1: Oturum, 2: Mail, 3: Captcha, 4: Kod, 5: Tamamlandı
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


def add_log(msg: str):
    """Konsola ve web arayüzü hafızasına zaman damgalı log ekler."""
    t_str = datetime.datetime.now().strftime("%H:%M:%S")
    line = f"[{t_str}] {msg}"
    print(line, flush=True)
    with state_lock:
        job_state["logs"].append(line)
        if len(job_state["logs"]) > 300:
            job_state["logs"].pop(0)


def update_state(**kwargs):
    with state_lock:
        for k, v in kwargs.items():
            job_state[k] = v


# ==============================================================================
#  BÖLÜM 3: PIXELBUNNY AI / CLAUDE OPUS 4.7 MOTORU
# ==============================================================================
class SpamokTempMail:
    def random_email(self, length: int = 15) -> str:
        rand_str = "".join(random.SystemRandom().choice(string.ascii_lowercase + string.digits) for _ in range(length))
        return f"{rand_str}@spamok.com"

    def get_confirm_link(self, mail: str, timeout: int = 30) -> Optional[str]:
        address = mail.replace("@spamok.com", "")
        for _ in range(timeout):
            try:
                r = requests.get(f"https://api.spamok.com/v2/EmailBox/{address}", timeout=10)
                for m in r.json().get("mails", []):
                    if "Confirm" in m.get("subject", "") or "Pixel Bunny" in m.get("fromDisplay", ""):
                        email_r = requests.get(f"https://api.spamok.com/v2/Email/{address}/{m['id']}", timeout=10)
                        html = email_r.json().get("messageHtml", "")
                        match = re.search(
                            r'href="(https://mt-link\.pixelbunny\.ai/cl/[^\"]+)"[^>]*background-color:#7c3aed',
                            html,
                        )
                        if match:
                            return match.group(1)
                        links = re.findall(r'href="(https://mt-link\.pixelbunny\.ai/cl/[^\"]+)"', html)
                        return links[1] if len(links) >= 2 else (links[0] if links else None)
            except Exception:
                pass
            time.sleep(1)
        return None


def register_pixelbunny() -> Tuple[Optional[str], Optional[str]]:
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
    payload = {
        "email": email,
        "password": PB_DEFAULT_PASSWORD,
        "data": {},
        "gotrue_meta_security": {},
        "code_challenge": None,
        "code_challenge_method": None,
    }
    add_log(f"[*] PixelBunny Kayıt: {email}")
    r = requests.post(
        f"{PB_BASE_URL}/auth/v1/signup?redirect_to=https://pixelbunny.ai",
        headers=headers,
        json=payload,
        timeout=15,
    )
    if r.status_code not in [200, 201]:
        return None, None

    link = temp.get_confirm_link(email)
    if not link:
        return None, None

    requests.get(link, allow_redirects=True, timeout=15)
    return email, PB_DEFAULT_PASSWORD


def login_pixelbunny(email: str, password: str) -> Tuple[Optional[str], Optional[str]]:
    r = requests.post(
        f"{PB_BASE_URL}/auth/v1/token?grant_type=password",
        headers={"apikey": PB_API_KEY, "content-type": "application/json;charset=UTF-8"},
        json={"email": email, "password": password},
        timeout=15,
    )
    if r.status_code != 200:
        return None, None
    data = r.json()
    return data.get("access_token"), data.get("user", {}).get("id")


def upload_pixelbunny_image(token: str, image_bytes: bytes) -> Optional[dict]:
    headers = {
        "authorization": f"Bearer {token}",
        "origin": "https://pixelbunny.ai",
        "referer": "https://pixelbunny.ai/",
        "accept": "*/*",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    files = {"file": ("captcha.jpg", image_bytes, "image/jpeg")}
    r = requests.post(f"{PB_BASE_URL}/functions/v1/upload-input", headers=headers, files=files, timeout=30)
    if r.status_code == 200:
        return {"url": r.json().get("url"), "type": "image/png"}
    return None


def create_pixelbunny_conversation(token: str, user_id: str) -> Optional[str]:
    r = requests.post(
        f"{PB_BASE_URL}/rest/v1/chat_conversations?select=id",
        headers={
            "apikey": PB_API_KEY,
            "authorization": f"Bearer {token}",
            "content-type": "application/json",
            "content-profile": "public",
            "prefer": "return=representation",
            "origin": "https://pixelbunny.ai",
            "referer": "https://pixelbunny.ai/",
            "x-client-info": "supabase-js-web/2.98.0",
            "accept": "application/vnd.pgrst.object+json",
        },
        json={"user_id": user_id, "default_model_id": "claude-opus-4.7"},
        timeout=15,
    )
    if r.status_code == 201:
        return r.json().get("id")
    return None


def ask_pixelbunny_opus(token: str, conv_id: str, attachment: dict) -> Optional[str]:
    headers = {
        "authorization": f"Bearer {token}",
        "content-type": "application/json",
        "accept": "*/*",
        "origin": "https://pixelbunny.ai",
        "referer": "https://pixelbunny.ai/",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    prompt = (
        "Bu görseldeki CAPTCHA (güvenlik kodu) karakterlerini oku. "
        "Karakterler üstte ve altta veya yamuk olabilir; mantıksal sırasına göre tüm harf ve rakamları yan yana birleştirerek yaz. "
        "SADECE ve SADECE tespit ettiğin harf ve rakamları düz yazı olarak ver. "
        "Hiçbir açıklama, selamlama, markdown, boşluk veya noktalama işareti ekleme."
    )
    payload = {
        "conversation_id": conv_id,
        "model_id": "claude-opus-4.7",
        "message": prompt,
        "attachments": [attachment],
        "incognito": False,
        "history": [],
    }
    full_response = ""
    with requests.post(
        f"{PB_BASE_URL}/functions/v1/chat-completion",
        headers=headers,
        json=payload,
        stream=True,
        timeout=45,
    ) as res:
        res.encoding = "utf-8"
        if res.status_code != 200:
            return None
        for raw_line in res.iter_lines(decode_unicode=True, chunk_size=1):
            if not raw_line:
                continue
            line = raw_line.strip()
            if not line.startswith("data:"):
                continue
            data_str = line[5:].strip()
            if data_str == "[DONE]":
                break
            try:
                data = json.loads(data_str)
                choices = data.get("choices", [])
                if choices:
                    c = choices[0].get("delta", {}).get("content", "")
                    if c:
                        full_response += c
            except Exception:
                pass
    return full_response


class OpusCaptchaSolver:
    def __init__(self):
        self.token = None
        self.user_id = None

    def _ensure_authenticated(self):
        if self.token and self.user_id:
            return
        add_log("[*] Opus 4.7 için PixelBunny oturumu başlatılıyor...")
        email, password = register_pixelbunny()
        if not email:
            raise RuntimeError("PixelBunny hesabı açılamadı!")
        self.token, self.user_id = login_pixelbunny(email, password)
        if not self.token:
            raise RuntimeError("PixelBunny oturumu açılamadı!")
        add_log("[+] PixelBunny oturumu hazır!")

    def solve(self, image_bytes: bytes) -> str:
        self._ensure_authenticated()

        add_log("[*] Captcha görseli yükleniyor...")
        attachment = upload_pixelbunny_image(self.token, image_bytes)
        if not attachment:
            raise RuntimeError("Görsel sunucuya yüklenemedi!")

        conv_id = create_pixelbunny_conversation(self.token, self.user_id)
        if not conv_id:
            raise RuntimeError("Opus 4.7 sohbet oturumu oluşturulamadı!")

        add_log("[*] Claude Opus 4.7'ye gönderiliyor...")
        raw_response = ask_pixelbunny_opus(self.token, conv_id, attachment)
        if not raw_response:
            raise RuntimeError("Opus 4.7'den yanıt alınamadı!")

        clean_text = re.sub(r"[^A-Za-z0-9]", "", raw_response).upper()
        add_log(f"[+] Opus 4.7 Captcha Okuması: {clean_text}")
        return clean_text


# ==============================================================================
#  BÖLÜM 4: TEMPMALNINJA (WEBSOCKET CLIENT)
# ==============================================================================
def clean_html(html_text: str) -> str:
    if not html_text:
        return ""
    text = re.sub(r"<style[^>]*>.*?</style>", "", html_text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"</?(?:p|div|tr|br|h[1-6])[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines)


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

    def connect(self):
        if self.is_connected and self.ws:
            return

        self.ws = websocket.create_connection(
            self.ws_url,
            header=self.headers,
            sslopt={"cert_reqs": ssl.CERT_NONE},
            timeout=2.0,
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

        self._init_session()
        self._subscribe_inbox()

    def _listen_loop(self):
        while self.is_running and self.ws:
            try:
                try:
                    msg = self.ws.recv()
                except (websocket.WebSocketTimeoutException, TimeoutError):
                    continue
                except (websocket.WebSocketConnectionClosedException, ConnectionResetError, BrokenPipeError):
                    break
                except Exception:
                    continue

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
                        if isinstance(data, list) and len(data) >= 2:
                            event_name = data[0]
                            event_payload = data[1]
                            if event_name == "new_message_notify":
                                threading.Thread(target=self._handle_new_message, args=(event_payload,), daemon=True).start()
                    except Exception:
                        pass
            except Exception:
                continue
        self.is_connected = False

    def _handle_new_message(self, data):
        mail = data.get("email_message", data)
        try:
            self._subscribe_inbox()
        except Exception:
            pass

        subject = mail.get("subject", "")
        sender_obj = mail.get("sender", {})
        sender = sender_obj.get("text", "") if isinstance(sender_obj, dict) else str(sender_obj)
        add_log(f"[!] YENİ E-POSTA! Kimden: {sender} | Konu: {subject}")

        m_code = re.search(r"\b(\d{6})\b", subject)
        if m_code:
            self.received_codes.append(m_code.group(1))

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

    def _init_session(self):
        res = self._send_event("get_guest_user_data", {"token": self.token})
        if res and isinstance(res, dict):
            if res.get("activeEmail"):
                ae = res["activeEmail"]
                self.active_email_addr = ae.get("address") or ae.get("emailAddress")

    def generate_quick_ninja(self) -> str:
        res = self._send_event("generate_temp_mail")
        if res and "emailData" in res:
            ed = res["emailData"]
            self.active_email_addr = ed.get("address") or ed.get("emailAddress")
            self.token = res.get("newToken", self.token)
            self._subscribe_inbox()
            return self.active_email_addr
        raise RuntimeError(f"Temp e-posta üretilemedi: {res}")

    def delete_email(self, email_address: str) -> bool:
        res = self._send_event("delete_email", {"emailAddress": email_address})
        return bool(res and ("emailDeleted" in res or res.get("success")))

    def get_messages(self):
        res = self._send_event("get_email_messages", {"messageType": "inbound"})
        return res.get("email_messages", []) if (res and isinstance(res, dict)) else []

    def get_message_detail(self, message_id):
        res = self._send_event("get_email_message", {"messageId": message_id}, timeout=20)
        if res and isinstance(res, dict) and "emailMessage" in res:
            msg_obj = res["emailMessage"]
            data = msg_obj.get("data", {})
            return {
                "id": msg_obj.get("id"),
                "subject": data.get("subject", ""),
                "clean_text": clean_html(data.get("content", "")),
            }
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


# ==============================================================================
#  BÖLÜM 5: GENSPARK B2C ADIMLARI
# ==============================================================================
def init_fresh_session() -> Tuple[requests.Session, str, str]:
    session = requests.Session()
    session.headers.update({
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "accept-language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
    })

    add_log("[*] 1/2 Canlı Genspark B2C oturumu başlatılıyor...")
    r1 = session.get(
        f"{GENSPARK_B2C_BASE}/oauth2/v2.0/authorize",
        params={
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "scope": "openid profile email",
            "p": "B2C_1_new_login",
        },
        timeout=15,
    )
    r1.raise_for_status()
    m1 = re.search(r"var SETTINGS = ({.*?});", r1.text)
    if not m1:
        raise RuntimeError("Genspark B2C SETTINGS objesi bulunamadı!")
    s1 = json.loads(m1.group(1))
    csrf1, tx1 = s1.get("csrf"), s1.get("transId")

    add_log("[*] 2/2 B2C Kayıt (SignUp) adımına geçiliyor...")
    r2 = session.get(
        f"{GENSPARK_B2C_BASE}/api/CombinedSigninAndSignup/unified?local=signup&csrf_token={csrf1}&tx={tx1}&p=B2C_1_new_login",
        timeout=15,
    )
    r2.raise_for_status()
    m2 = re.search(r"var SETTINGS = ({.*?});", r2.text)
    if not m2:
        raise RuntimeError("SignUp SETTINGS objesi bulunamadı!")
    s2 = json.loads(m2.group(1))
    csrf_token, tx = s2.get("csrf"), s2.get("transId")
    add_log(f"[+] Güncel canlı oturum hazır! (TX: {tx[:30]}...)")
    return session, tx, csrf_token


def get_default_headers(csrf_token: str, tx: str = "") -> dict:
    return {
        "accept": "application/json, text/javascript, */*; q=0.01",
        "accept-language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
        "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
        "origin": "https://login.genspark.ai",
        "priority": "u=1, i",
        "referer": (
            f"{GENSPARK_B2C_BASE}/api/CombinedSigninAndSignup/unified"
            f"?local=signup&csrf_token={csrf_token}&tx={tx}&p=B2C_1_new_login"
        ),
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "x-csrf-token": csrf_token,
        "x-requested-with": "XMLHttpRequest",
    }


def safe_json(r: requests.Response) -> dict:
    try:
        return r.json()
    except Exception:
        return {"status": str(r.status_code), "raw": r.text}


def req_1_get_challenge(session: requests.Session, tx: str, csrf_token: str) -> Tuple[str, bytes, str]:
    url = f"{GENSPARK_B2C_BASE}/SelfAsserted/DisplayControlAction/vbeta/captchaControlChallengeCode/GetChallenge?tx={tx}&p=B2C_1_new_login&challengeType=Visual&_={int(time.time() * 1000)}"
    headers = get_default_headers(csrf_token, tx)
    add_log("[*] 1/5 GetChallenge çağrılıyor...")
    r = session.get(url, headers=headers, timeout=15)
    r.raise_for_status()

    data = r.json()
    challenge_id = data.get("challengeId")
    challenge_str = data.get("challengeString", "")
    azure_region = data.get("azureregion", "")
    b64_data = challenge_str.split(",", 1)[1] if "," in challenge_str else challenge_str
    image_bytes = base64.b64decode(b64_data)
    add_log(f"[+] Challenge ID alındı: {challenge_id} ({len(image_bytes)} bayt)")
    return challenge_id, image_bytes, azure_region


def req_2_verify_challenge(session: requests.Session, tx: str, csrf_token: str, challenge_id: str, captcha_entered: str, azure_region: str = "") -> bool:
    url = f"{GENSPARK_B2C_BASE}/SelfAsserted/DisplayControlAction/vbeta/captchaControlChallengeCode/VerifyChallenge?tx={tx}&p=B2C_1_new_login"
    headers = get_default_headers(csrf_token, tx)
    payload = {
        "challengeId": challenge_id,
        "captchaEntered": captcha_entered,
        "challengeType": "Visual",
        "azureRegion": azure_region,
    }
    add_log(f"[*] 2/5 VerifyChallenge gönderiliyor (Captcha: {captcha_entered})...")
    r = session.post(url, headers=headers, data=payload, timeout=15)
    res = safe_json(r)
    return str(res.get("isCaptchaSolved")).lower() == "true"


def req_3_send_code(session: requests.Session, tx: str, csrf_token: str, email: str) -> bool:
    url = f"{GENSPARK_B2C_BASE}/SelfAsserted/DisplayControlAction/vbeta/emailVerificationControl/SendCode?tx={tx}&p=B2C_1_new_login"
    headers = get_default_headers(csrf_token, tx)
    add_log(f"[*] 3/5 SendCode gönderiliyor (E-posta: {email})...")
    r = session.post(url, headers=headers, data={"email": email}, timeout=15)
    res = safe_json(r)
    return res.get("status") == "200"


def req_4_verify_code(session: requests.Session, tx: str, csrf_token: str, email: str, code: str) -> bool:
    url = f"{GENSPARK_B2C_BASE}/SelfAsserted/DisplayControlAction/vbeta/emailVerificationControl/VerifyCode?tx={tx}&p=B2C_1_new_login"
    headers = get_default_headers(csrf_token, tx)
    add_log(f"[*] 4/5 VerifyCode gönderiliyor (Kod: {code})...")
    r = session.post(url, headers=headers, data={"email": email, "emailVerificationCode": code}, timeout=15)
    res = safe_json(r)
    return res.get("status") == "200"


def req_5_self_asserted(session: requests.Session, tx: str, csrf_token: str, email: str, code: str, captcha_code: str, password: str = DEFAULT_PASSWORD) -> bool:
    url = f"{GENSPARK_B2C_BASE}/SelfAsserted?tx={tx}&p=B2C_1_new_login"
    headers = get_default_headers(csrf_token, tx)
    payload = {
        "email": email,
        "emailVerificationCode": code,
        "captchaControlChallengeCode": captcha_code,
        "newPassword": password,
        "reenterPassword": password,
        "request_type": "RESPONSE",
    }
    add_log("[*] 5/5 SelfAsserted (Kayıt Tamamlama) gönderiliyor...")
    r = session.post(url, headers=headers, data=payload, timeout=15)
    res = safe_json(r)
    return res.get("status") == "200"


def wait_for_genspark_code(mail_client: TempMailNinja, timeout: int = 180) -> Optional[str]:
    add_log(f"[*] Doğrulama kodu bekleniyor (Zaman aşımı: {timeout}s)...")
    start = time.time()
    while time.time() - start < timeout:
        if mail_client.received_codes:
            code = mail_client.received_codes.pop(0)
            add_log(f"[+] Doğrulama Kodu Yakalandı (Push): {code}")
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
#  BÖLÜM 6: ARKA PLAN ÇALIŞTIRICI (THREAD WORKER)
# ==============================================================================
def background_account_worker(password: str = DEFAULT_PASSWORD):
    """Hesap açma sürecini arka planda adım adım çalıştırır."""
    update_state(
        is_running=True,
        status="Canlı Oturum Başlatılıyor...",
        step=1,
        attempt=0,
        captcha_b64="",
        captcha_text="",
        email="",
        code="",
        error=None,
        started_at=datetime.datetime.now().strftime("%H:%M:%S"),
    )
    add_log("==================================================")
    add_log("  GENSPARK YENİ HESAP AÇMA İŞLEMİ BAŞLATILDI")
    add_log("==================================================")

    session = None
    mail_client = None
    email = None

    try:
        # 1. Canlı B2C Oturumu
        session, tx, csrf_token = init_fresh_session()

        # 2. Temp Mail
        update_state(step=2, status="Geçici E-Posta Alınıyor...")
        add_log("[*] TempMailNinja başlatılıyor...")
        mail_client = TempMailNinja(locale="en")
        mail_client.connect()
        email = mail_client.generate_quick_ninja()
        update_state(email=email)
        add_log(f"[+] Geçici E-Posta Hazır: {email}")

        # 3. Captcha Döngüsü (10 deneme)
        update_state(step=3, status="CAPTCHA Çözülüyor (Opus 4.7)...")
        solver = OpusCaptchaSolver()
        captcha_solved = False
        captcha_text = ""

        for attempt in range(1, 11):
            update_state(attempt=attempt, status=f"CAPTCHA Çözülüyor ({attempt}/10)...")
            add_log(f"\n[*] CAPTCHA Denemesi ({attempt}/10)...")
            challenge_id, image_bytes, azure_region = req_1_get_challenge(session, tx, csrf_token)

            # Görseli arayüz için Base64'e dönüştür ve diske kaydet
            b64_img = "data:image/jpeg;base64," + base64.b64encode(image_bytes).decode("ascii")
            update_state(captcha_b64=b64_img)

            captcha_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "captcha_challenge.jpg")
            try:
                with open(captcha_file, "wb") as f:
                    f.write(image_bytes)
            except Exception:
                pass

            captcha_text = solver.solve(image_bytes)
            update_state(captcha_text=captcha_text)

            if req_2_verify_challenge(session, tx, csrf_token, challenge_id, captcha_text, azure_region):
                captcha_solved = True
                add_log(f"[+] CAPTCHA başarıyla doğrulandı! ({captcha_text})")
                break
            else:
                add_log("[-] CAPTCHA eşleşmedi! Oturum bozulmadan yeni görsel çekiliyor...")
                time.sleep(1)

        if not captcha_solved:
            raise RuntimeError("CAPTCHA 10 denemede çözülemedi.")

        # 4. Kod Gönderme
        update_state(step=4, status="Doğrulama Kodu Bekleniyor...")
        if not req_3_send_code(session, tx, csrf_token, email):
            raise RuntimeError("Doğrulama kodu gönderilemedi.")

        code = wait_for_genspark_code(mail_client, timeout=180)
        if not code:
            raise RuntimeError("3 dakika içinde e-posta onay kodu gelmedi.")
        update_state(code=code)

        if not req_4_verify_code(session, tx, csrf_token, email, code):
            raise RuntimeError("E-posta onay kodu hatalı!")

        # 5. Kayıt Tamamlama
        update_state(status="Hesap Oluşturuluyor...")
        if not req_5_self_asserted(session, tx, csrf_token, email, code, captcha_text, password):
            raise RuntimeError("SelfAsserted kayıt isteği tamamlanamadı.")

        # Başarılı!
        account_entry = {
            "email": email,
            "password": password,
            "date": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        update_state(step=5, status="Hesap Başarıyla Açıldı!", last_account=account_entry)

        try:
            with open(ACCOUNTS_FILE, "a", encoding="utf-8") as f:
                f.write(f"{email}:{password}\n")
            add_log("[+] Hesap 'genspark_hesaplar.txt' dosyasına eklendi.")
        except Exception as e:
            add_log(f"[-] Dosya yazma hatası: {e}")

        add_log("\n" + "=" * 50)
        add_log(f"  [SUCCESS] HESAP HAZIR: {email}:{password}")
        add_log("=" * 50 + "\n")

    except Exception as e:
        err_msg = str(e)
        add_log(f"[HATA] İşlem başarısız: {err_msg}")
        update_state(error=err_msg, status=f"Hata: {err_msg}")

    finally:
        if mail_client:
            add_log("[*] Geçici e-posta sunucudan temizleniyor...")
            if email:
                try:
                    mail_client.delete_email(email)
                except Exception:
                    pass
            mail_client.close()
            add_log("[+] Mail istemcisi kapatıldı.")
        update_state(is_running=False)


# ==============================================================================
#  BÖLÜM 7: FLASK UYGULAMASI & HTML ŞABLONU (TEK DOSYA)
# ==============================================================================
app = Flask(__name__)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Genspark AI - Otomatik Hesap Açıcı</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg: #090d16;
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

        /* Arka plan gradyan efekti */
        .ambient-bg {
            position: fixed;
            top: -20%;
            left: 50%;
            transform: translateX(-50%);
            width: 800px;
            height: 400px;
            background: radial-gradient(circle, rgba(99, 102, 241, 0.15) 0%, rgba(9, 13, 22, 0) 70%);
            z-index: -1;
            pointer-events: none;
        }

        .container {
            max-width: 1200px;
            width: 100%;
            margin: 0 auto;
            padding: 24px 20px;
        }

        /* Üst Başlık */
        header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 24px;
            padding-bottom: 16px;
            border-bottom: 1px solid var(--border);
        }

        .brand {
            display: flex;
            align-items: center;
            gap: 12px;
        }

        .brand-badge {
            width: 36px;
            height: 36px;
            background: linear-gradient(135deg, #6366f1, #8b5cf6);
            border-radius: 10px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 700;
            font-size: 18px;
            color: #fff;
            box-shadow: 0 4px 12px var(--accent-glow);
        }

        .brand-title {
            font-size: 20px;
            font-weight: 700;
            letter-spacing: -0.5px;
        }

        .brand-sub {
            font-size: 12px;
            color: var(--text-sub);
        }

        .status-badge {
            display: flex;
            align-items: center;
            gap: 8px;
            background: var(--surface);
            border: 1px solid var(--border);
            padding: 6px 14px;
            border-radius: 999px;
            font-size: 13px;
            font-weight: 500;
        }

        .status-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: var(--success);
            box-shadow: 0 0 8px var(--success);
        }

        .status-dot.busy {
            background: var(--warning);
            box-shadow: 0 0 8px var(--warning);
            animation: pulse 1.5s infinite;
        }

        @keyframes pulse {
            0% { opacity: 1; transform: scale(1); }
            50% { opacity: 0.4; transform: scale(1.3); }
            100% { opacity: 1; transform: scale(1); }
        }

        /* Kontrol Paneli Kartı */
        .control-card {
            background: var(--surface-card);
            border: 1px solid var(--border);
            border-radius: 16px;
            padding: 20px 24px;
            margin-bottom: 24px;
            display: flex;
            flex-wrap: wrap;
            align-items: center;
            justify-content: space-between;
            gap: 16px;
            box-shadow: 0 8px 24px rgba(0, 0, 0, 0.2);
        }

        .control-inputs {
            display: flex;
            flex-wrap: wrap;
            align-items: center;
            gap: 12px;
            flex: 1;
        }

        .input-group {
            display: flex;
            flex-direction: column;
            gap: 6px;
        }

        .input-group label {
            font-size: 12px;
            color: var(--text-sub);
            font-weight: 500;
        }

        .input-group input {
            background: var(--surface);
            border: 1px solid var(--border);
            color: var(--text-main);
            padding: 10px 14px;
            border-radius: 10px;
            font-size: 14px;
            font-family: inherit;
            outline: none;
            transition: border-color 0.2s;
        }

        .input-group input:focus {
            border-color: var(--accent);
        }

        .btn-primary {
            background: linear-gradient(135deg, var(--accent), var(--accent-hover));
            color: #fff;
            border: none;
            padding: 12px 24px;
            border-radius: 12px;
            font-size: 14px;
            font-weight: 600;
            font-family: inherit;
            cursor: pointer;
            display: flex;
            align-items: center;
            gap: 10px;
            transition: transform 0.15s, box-shadow 0.15s, opacity 0.2s;
            box-shadow: 0 4px 16px var(--accent-glow);
        }

        .btn-primary:hover:not(:disabled) {
            transform: translateY(-1px);
            box-shadow: 0 6px 20px var(--accent-glow);
        }

        .btn-primary:active:not(:disabled) {
            transform: translateY(0);
        }

        .btn-primary:disabled {
            opacity: 0.6;
            cursor: not-allowed;
            transform: none;
        }

        /* İlerleme Aşamaları (Stepper) */
        .stepper {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 24px;
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 14px;
            padding: 14px 20px;
            overflow-x: auto;
            gap: 8px;
        }

        .step-item {
            display: flex;
            align-items: center;
            gap: 10px;
            font-size: 13px;
            color: var(--text-sub);
            white-space: nowrap;
        }

        .step-num {
            width: 26px;
            height: 26px;
            border-radius: 50%;
            background: var(--surface-card);
            border: 1px solid var(--border);
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 12px;
            font-weight: 600;
        }

        .step-item.active {
            color: var(--text-main);
            font-weight: 600;
        }

        .step-item.active .step-num {
            background: var(--accent);
            border-color: var(--accent);
            color: #fff;
            box-shadow: 0 0 10px var(--accent-glow);
        }

        .step-item.done {
            color: var(--success);
        }

        .step-item.done .step-num {
            background: rgba(16, 185, 129, 0.2);
            border-color: var(--success);
            color: var(--success);
        }

        .step-divider {
            flex: 1;
            height: 2px;
            background: var(--border);
            min-width: 20px;
        }

        /* İki Kolonlu Grid */
        .grid-main {
            display: grid;
            grid-template-columns: 380px 1fr;
            gap: 20px;
            margin-bottom: 24px;
        }

        @media (max-width: 900px) {
            .grid-main {
                grid-template-columns: 1fr;
            }
        }

        .card {
            background: var(--surface-card);
            border: 1px solid var(--border);
            border-radius: 16px;
            padding: 20px;
            display: flex;
            flex-direction: column;
            gap: 16px;
        }

        .card-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            font-size: 15px;
            font-weight: 600;
            color: var(--text-main);
        }

        /* Captcha Önizleme Alanı */
        .captcha-box {
            background: var(--surface);
            border: 1px dashed var(--border);
            border-radius: 12px;
            min-height: 120px;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            padding: 14px;
            text-align: center;
            gap: 10px;
        }

        .captcha-box img {
            max-width: 100%;
            height: auto;
            border-radius: 8px;
            border: 1px solid var(--border);
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
        }

        .captcha-info-row {
            display: flex;
            gap: 8px;
            flex-wrap: wrap;
            justify-content: center;
            width: 100%;
        }

        .pill {
            background: var(--surface);
            border: 1px solid var(--border);
            padding: 4px 10px;
            border-radius: 8px;
            font-size: 12px;
            color: var(--text-sub);
        }

        .pill strong {
            color: var(--text-main);
        }

        .pill.accent {
            background: rgba(99, 102, 241, 0.15);
            border-color: var(--accent);
            color: #c7d2fe;
            font-family: 'JetBrains Mono', monospace;
            font-size: 13px;
            font-weight: 600;
        }

        /* Bilgi Kutusu */
        .info-card {
            display: flex;
            flex-direction: column;
            gap: 8px;
        }

        .info-row {
            display: flex;
            justify-content: space-between;
            align-items: center;
            background: var(--surface);
            padding: 8px 12px;
            border-radius: 8px;
            border: 1px solid var(--border);
            font-size: 12px;
        }

        .info-row .label {
            color: var(--text-sub);
        }

        .info-row .val {
            font-family: 'JetBrains Mono', monospace;
            font-size: 12px;
            font-weight: 500;
            color: #e2e8f0;
            word-break: break-all;
        }

        /* Canlı Terminal Log Konsolu */
        .terminal-container {
            flex: 1;
            display: flex;
            flex-direction: column;
            background: #090d16;
            border: 1px solid var(--border);
            border-radius: 12px;
            overflow: hidden;
            min-height: 380px;
            box-shadow: inset 0 2px 8px rgba(0, 0, 0, 0.4);
        }

        .terminal-bar {
            background: #111827;
            border-bottom: 1px solid var(--border);
            padding: 8px 14px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            font-size: 12px;
            color: var(--text-sub);
        }

        .terminal-dots {
            display: flex;
            gap: 6px;
        }

        .tdot {
            width: 10px;
            height: 10px;
            border-radius: 50%;
        }

        .tdot.r { background: #ef4444; }
        .tdot.y { background: #f59e0b; }
        .tdot.g { background: #10b981; }

        .terminal-body {
            flex: 1;
            padding: 14px;
            font-family: 'JetBrains Mono', monospace;
            font-size: 12px;
            line-height: 1.6;
            color: #cbd5e1;
            overflow-y: auto;
            max-height: 420px;
            white-space: pre-wrap;
            word-break: break-all;
        }

        .terminal-body::-webkit-scrollbar {
            width: 6px;
        }

        .terminal-body::-webkit-scrollbar-track {
            background: transparent;
        }

        .terminal-body::-webkit-scrollbar-thumb {
            background: #243247;
            border-radius: 3px;
        }

        /* Sonuç & Hesaplar Kartı */
        .accounts-card {
            background: var(--surface-card);
            border: 1px solid var(--border);
            border-radius: 16px;
            padding: 20px;
        }

        .accounts-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 16px;
            flex-wrap: wrap;
            gap: 12px;
        }

        .btn-secondary {
            background: var(--surface);
            border: 1px solid var(--border);
            color: var(--text-main);
            padding: 8px 14px;
            border-radius: 8px;
            font-size: 13px;
            font-family: inherit;
            cursor: pointer;
            display: flex;
            align-items: center;
            gap: 6px;
            transition: background 0.15s;
        }

        .btn-secondary:hover {
            background: #1e293b;
        }

        .table-responsive {
            overflow-x: auto;
        }

        table {
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
            text-align: left;
        }

        th {
            background: var(--surface);
            padding: 10px 14px;
            color: var(--text-sub);
            font-weight: 600;
            border-bottom: 1px solid var(--border);
        }

        td {
            padding: 12px 14px;
            border-bottom: 1px solid var(--border);
            color: var(--text-main);
        }

        tr:hover td {
            background: rgba(255, 255, 255, 0.02);
        }

        .font-mono {
            font-family: 'JetBrains Mono', monospace;
        }

        .btn-copy {
            background: transparent;
            border: 1px solid var(--border);
            color: var(--text-sub);
            padding: 4px 10px;
            border-radius: 6px;
            font-size: 11px;
            cursor: pointer;
            transition: all 0.15s;
        }

        .btn-copy:hover {
            border-color: var(--accent);
            color: #fff;
        }

        /* Toast Bildirimi */
        #toast {
            position: fixed;
            bottom: 24px;
            right: 24px;
            background: var(--surface);
            border: 1px solid var(--accent);
            color: #fff;
            padding: 10px 18px;
            border-radius: 10px;
            font-size: 13px;
            box-shadow: 0 4px 16px rgba(0,0,0,0.4);
            opacity: 0;
            transform: translateY(10px);
            transition: all 0.25s ease;
            pointer-events: none;
            z-index: 1000;
        }

        #toast.show {
            opacity: 1;
            transform: translateY(0);
        }
    </style>
</head>
<body>
    <div class="ambient-bg"></div>

    <div class="container">
        <!-- Header -->
        <header>
            <div class="brand">
                <div class="brand-badge">G</div>
                <div>
                    <h1 class="brand-title">Genspark AI Creator</h1>
                    <p class="brand-sub">Claude Opus 4.7 & TempMailNinja Canlı Otomasyon</p>
                </div>
            </div>
            <div class="status-badge">
                <span class="status-dot" id="statusDot"></span>
                <span id="statusText">Sistem Hazır</span>
            </div>
        </header>

        <!-- Kontrol Kartı -->
        <div class="control-card">
            <div class="control-inputs">
                <div class="input-group">
                    <label for="pwdInput">Oluşturulacak Hesap Şifresi</label>
                    <input type="text" id="pwdInput" value="Password123!" placeholder="Şifre belirleyin">
                </div>
            </div>
            <button class="btn-primary" id="btnStart" onclick="startCreation()">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <polygon points="5 3 19 12 5 21 5 3"></polygon>
                </svg>
                <span id="btnText">Yeni Hesap Aç</span>
            </button>
        </div>

        <!-- İlerleme Adımları (Stepper) -->
        <div class="stepper">
            <div class="step-item" id="step1">
                <div class="step-num">1</div>
                <span>Canlı Oturum</span>
            </div>
            <div class="step-divider"></div>
            <div class="step-item" id="step2">
                <div class="step-num">2</div>
                <span>Temp Mail</span>
            </div>
            <div class="step-divider"></div>
            <div class="step-item" id="step3">
                <div class="step-num">3</div>
                <span>Opus 4.7 Captcha</span>
            </div>
            <div class="step-divider"></div>
            <div class="step-item" id="step4">
                <div class="step-num">4</div>
                <span>Kod Doğrulama</span>
            </div>
            <div class="step-divider"></div>
            <div class="step-item" id="step5">
                <div class="step-num">5</div>
                <span>Kayıt Tamam</span>
            </div>
        </div>

        <!-- Grid: Sol (Önizleme) / Sağ (Terminal) -->
        <div class="grid-main">
            <!-- Sol: Önizleme ve Bilgi Kartları -->
            <div style="display: flex; flex-direction: column; gap: 20px;">
                <!-- Captcha Kartı -->
                <div class="card">
                    <div class="card-header">
                        <span>Canlı CAPTCHA İzleme</span>
                        <span class="pill" id="attemptPill">Deneme: 0/10</span>
                    </div>

                    <div class="captcha-box" id="captchaBox">
                        <span style="color: var(--text-sub); font-size: 13px;" id="captchaPlaceholder">Henüz görsel çekilmedi</span>
                        <img id="captchaImg" style="display: none;" alt="Captcha Önizleme">
                    </div>

                    <div class="captcha-info-row">
                        <span class="pill">Model: <strong>Claude Opus 4.7</strong></span>
                        <span class="pill accent" id="opusPredictionPill">Çözüm: Bekleniyor...</span>
                    </div>
                </div>

                <!-- Detay Kartı -->
                <div class="card">
                    <div class="card-header">
                        <span>Anlık Bilgiler</span>
                    </div>
                    <div class="info-card">
                        <div class="info-row">
                            <span class="label">Geçici E-Posta:</span>
                            <span class="val" id="valEmail">-</span>
                        </div>
                        <div class="info-row">
                            <span class="label">Doğrulama Kodu:</span>
                            <span class="val" id="valCode">-</span>
                        </div>
                        <div class="info-row">
                            <span class="label">İşlem Durumu:</span>
                            <span class="val" id="valStatus">Boşta</span>
                        </div>
                    </div>
                </div>
            </div>

            <!-- Sağ: Terminal Log Konsolu -->
            <div class="card" style="padding: 0; overflow: hidden;">
                <div class="terminal-bar">
                    <div class="terminal-dots">
                        <div class="tdot r"></div>
                        <div class="tdot y"></div>
                        <div class="tdot g"></div>
                    </div>
                    <span>CANLI SUNUCU LOGLARI</span>
                    <button class="btn-copy" onclick="clearLogs()">Temizle</button>
                </div>
                <div class="terminal-body" id="terminalBody">[*] Sistem başlatıldı. 'Yeni Hesap Aç' butonuna basarak süreci başlatabilirsiniz.</div>
            </div>
        </div>

        <!-- Açılan Hesaplar Tablosu -->
        <div class="accounts-card">
            <div class="accounts-header">
                <h3 style="font-size: 16px; font-weight: 600;">Oluşturulan Hesaplar (genspark_hesaplar.txt)</h3>
                <div style="display: flex; gap: 8px;">
                    <button class="btn-secondary" onclick="copyAllAccounts()">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
                            <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
                        </svg>
                        Tümünü Kopyala
                    </button>
                    <a href="/api/download" class="btn-secondary" style="text-decoration: none;">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
                            <polyline points="7 10 12 15 17 10"></polyline>
                            <line x1="12" y1="15" x2="12" y2="3"></line>
                        </svg>
                        TXT İndir
                    </a>
                </div>
            </div>

            <div class="table-responsive">
                <table>
                    <thead>
                        <tr>
                            <th>#</th>
                            <th>E-Posta Adresi</th>
                            <th>Şifre</th>
                            <th>Biçim (email:password)</th>
                            <th>İşlem</th>
                        </tr>
                    </thead>
                    <tbody id="accountsTableBody">
                        <tr>
                            <td colspan="5" style="text-align: center; color: var(--text-sub);">Hesap listesi yükleniyor...</td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </div>
    </div>

    <!-- Toast Bildirimi -->
    <div id="toast">Kopyalandı!</div>

    <script>
        let isPolling = true;
        let lastLogCount = 0;

        function showToast(msg) {
            const toast = document.getElementById('toast');
            toast.innerText = msg;
            toast.classList.add('show');
            setTimeout(() => toast.classList.remove('show'), 2000);
        }

        function copyText(txt) {
            navigator.clipboard.writeText(txt).then(() => {
                showToast("Panoya Kopyalandı!");
            });
        }

        async function startCreation() {
            const pwd = document.getElementById('pwdInput').value.trim() || 'Password123!';
            const btn = document.getElementById('btnStart');
            btn.disabled = true;

            try {
                const res = await fetch('/api/start', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ password: pwd })
                });
                const data = await res.json();
                if (data.status === 'already_running') {
                    showToast("Zaten devam eden bir işlem var!");
                } else {
                    showToast("Hesap açma süreci başlatıldı!");
                }
            } catch (err) {
                showToast("Başlatma hatası: " + err);
                btn.disabled = false;
            }
        }

        async function clearLogs() {
            try {
                await fetch('/api/clear_logs', { method: 'POST' });
                document.getElementById('terminalBody').innerText = "";
            } catch (e) {}
        }

        async function fetchStatus() {
            try {
                const res = await fetch('/api/status');
                const data = await res.json();

                // 1. Buton ve Durum Rozeti
                const btn = document.getElementById('btnStart');
                const btnText = document.getElementById('btnText');
                const statusDot = document.getElementById('statusDot');
                const statusText = document.getElementById('statusText');

                if (data.is_running) {
                    btn.disabled = true;
                    btnText.innerText = "İşlem Sürüyor...";
                    statusDot.className = "status-dot busy";
                    statusText.innerText = data.status || "Çalışıyor";
                } else {
                    btn.disabled = false;
                    btnText.innerText = "Yeni Hesap Aç";
                    statusDot.className = "status-dot";
                    statusText.innerText = data.status || "Sistem Hazır";
                }

                // 2. Stepper
                for (let i = 1; i <= 5; i++) {
                    const stepEl = document.getElementById('step' + i);
                    if (data.step > i) {
                        stepEl.className = "step-item done";
                    } else if (data.step === i) {
                        stepEl.className = "step-item active";
                    } else {
                        stepEl.className = "step-item";
                    }
                }

                // 3. Captcha Önizleme
                const captchaImg = document.getElementById('captchaImg');
                const captchaPlaceholder = document.getElementById('captchaPlaceholder');
                const attemptPill = document.getElementById('attemptPill');
                const opusPill = document.getElementById('opusPredictionPill');

                if (data.captcha_b64) {
                    captchaImg.src = data.captcha_b64;
                    captchaImg.style.display = 'block';
                    captchaPlaceholder.style.display = 'none';
                } else {
                    captchaImg.style.display = 'none';
                    captchaPlaceholder.style.display = 'block';
                }

                attemptPill.innerText = `Deneme: ${data.attempt || 0}/10`;
                opusPill.innerText = `Çözüm: ${data.captcha_text || 'Bekleniyor...'}`;

                // 4. Detay Bilgiler
                document.getElementById('valEmail').innerText = data.email || '-';
                document.getElementById('valCode').innerText = data.code || '-';
                document.getElementById('valStatus').innerText = data.status || 'Boşta';

                // 5. Terminal Logları
                const terminal = document.getElementById('terminalBody');
                if (data.logs && data.logs.length > 0) {
                    if (data.logs.length !== lastLogCount) {
                        terminal.innerText = data.logs.join('\\n');
                        terminal.scrollTop = terminal.scrollHeight;
                        lastLogCount = data.logs.length;
                    }
                }

            } catch (err) {
                console.error("Status polling error:", err);
            }
        }

        async function fetchAccounts() {
            try {
                const res = await fetch('/api/accounts');
                const list = await res.json();
                const tbody = document.getElementById('accountsTableBody');

                if (!list || list.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="5" style="text-align: center; color: var(--text-sub);">Henüz kaydedilmiş hesap yok.</td></tr>';
                    return;
                }

                tbody.innerHTML = list.map((acc, idx) => `
                    <tr>
                        <td style="color: var(--text-sub);">${idx + 1}</td>
                        <td class="font-mono"><strong>${acc.email}</strong></td>
                        <td class="font-mono">${acc.password}</td>
                        <td class="font-mono" style="color: #a5b4fc;">${acc.format}</td>
                        <td>
                            <button class="btn-copy" onclick="copyText('${acc.format}')">Kopyala</button>
                        </td>
                    </tr>
                `).reverse().join('');
            } catch (err) {
                console.error("Accounts error:", err);
            }
        }

        async function copyAllAccounts() {
            try {
                const res = await fetch('/api/accounts');
                const list = await res.json();
                if (list && list.length > 0) {
                    const text = list.map(a => a.format).join('\\n');
                    copyText(text);
                } else {
                    showToast("Kopyalanacak hesap bulunamadı!");
                }
            } catch (err) {
                showToast("Hata oluştu!");
            }
        }

        // Döngüsel sorgulama (her 1 saniyede status, her 5 saniyede hesaplar)
        setInterval(fetchStatus, 1000);
        setInterval(fetchAccounts, 4000);

        // İlk yükleme
        fetchStatus();
        fetchAccounts();
    </script>
</body>
</html>
"""


# ==============================================================================
#  BÖLÜM 8: FLASK API ENDPOINTLERİ
# ==============================================================================
@app.route("/")
def index():
    """Doğrudan gömülü HTML arayüzünü döndürür."""
    return render_template_string(HTML_TEMPLATE)


@app.route("/api/start", methods=["POST"])
def api_start():
    """Yeni hesap açma sürecini başlatır."""
    with state_lock:
        if job_state["is_running"]:
            return jsonify({"status": "already_running"}), 400

    data = request.get_json(silent=True) or {}
    password = data.get("password") or DEFAULT_PASSWORD

    # Arka plan thread başlat
    t = threading.Thread(target=background_account_worker, args=(password,), daemon=True)
    t.start()
    return jsonify({"status": "started", "password": password})


@app.route("/api/status", methods=["GET"])
def api_status():
    """Mevcut işlem durumunu, captcha görselini ve logları döndürür."""
    with state_lock:
        return jsonify(job_state)


@app.route("/api/accounts", methods=["GET"])
def api_accounts():
    """genspark_hesaplar.txt dosyasındaki kayıtlı hesapları döndürür."""
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
    """genspark_hesaplar.txt dosyasını düz metin olarak indirir."""
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


@app.route("/api/clear_logs", methods=["POST"])
def api_clear_logs():
    """Ekrandaki log geçmişini temizler."""
    with state_lock:
        job_state["logs"] = []
    return jsonify({"status": "cleared"})


# ==============================================================================
#  GİRİŞ NOKTASI (RENDER.COM & YEREL SUNUCU)
# ==============================================================================
if __name__ == "__main__":
    # Render.com tarafından atanan PORT ortam değişkenini okur, yoksa 5000'de başlar
    port = int(os.environ.get("PORT", 5000))
    print("=" * 60)
    print("  GENSPARK AI - WEB ARAYÜZÜ BAŞLATILIYOR")
    print(f"  Adres: http://localhost:{port} (Tüm arayüzler: 0.0.0.0:{port})")
    print("=" * 60)
    app.run(host="0.0.0.0", port=port, debug=False)
