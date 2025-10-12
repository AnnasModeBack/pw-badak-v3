import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os
import time
import re
import random
import requests
import imaplib
import email as email_parser
import threading 
import json 
from collections import deque
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

# ==============================================================================
#                 *** PENTING: GANTI KONFIGURASI INI! ***
# ==============================================================================

# GANTI INI DENGAN TOKEN BOT TELEGRAM ANDA
TELEGRAM_BOT_TOKEN = "6589420280:AAEPgvt6DdvZdtZ0NM-olXz9NySr6PDNYM"
# GANTI INI DENGAN CHAT ID GRUP ANDA (Biasanya -100xxxxxxxxxx)
TELEGRAM_GROUP_CHAT_ID = "-1003102738220" 
RECEIVER_EMAIL = "support@support.whatsapp.com"

# Akun default (Wajib App Password 16 digit). GANTI INI DENGAN AKUN ANDA!
DEFAULT_ACCOUNTS = [
    {'email': "annasrullah916@gmail.com", 'password': "vsgsndxittsevaqw"},
    # {'email': "contoh2@gmail.com", 'password': "ckgxtngaotiqferx"}, # Tambahkan akun default lain di sini
]

# ==============================================================================
#                 *** KONFIGURASI & VARIABEL GLOBAL ***
# ==============================================================================

ACCOUNTS_FILE = "accounts.txt"
RIWAYAT_FILE = "riwayat_kirim.json"
TELEGRAM_USERS_FILE = "telegram_users.json"
TELEGRAM_LAST_UPDATE_ID = 0
IMAP_CHECK_INTERVAL_SECONDS = 60 # Cek balasan setiap 60 detik
TELEGRAM_POLLING_INTERVAL = 3  # Polling command setiap 3 detik

# Variabel Global
SENDER_ACCOUNTS = []
RIWAYAT_PENGIRIMAN_GLOBAL = deque(maxlen=500) # Batasi riwayat maksimum
TELEGRAM_USER_MAP = {} # {telegram_id: username, ...}
LOG_QUEUE = deque(maxlen=100)
IS_BOT_RUNNING = False

# ==============================================================================
#                         *** UTILITY & DATA HANDLING ***
# ==============================================================================

def add_log(level, message):
    """Adds a log entry to the global queue and prints to console."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # Menggunakan HTML tag code untuk pesan yang sensitif/teknis
    log_message = message.replace('<', '&lt;').replace('>', '&gt;')
    log_entry = {'timestamp': timestamp, 'level': level, 'message': log_message}
    print(f"[{level}] {timestamp} - {message}")
    LOG_QUEUE.appendleft(log_entry)

def load_data():
    """Loads all persistent data from files."""
    global SENDER_ACCOUNTS, RIWAYAT_PENGIRIMAN_GLOBAL, TELEGRAM_USER_MAP
    
    # 1. Accounts
    accounts = list(DEFAULT_ACCOUNTS)
    if os.path.exists(ACCOUNTS_FILE):
        try:
            with open(ACCOUNTS_FILE, 'r') as f:
                for line in f:
                    try:
                        email, password = line.strip().split(':', 1)
                        if email and password and '@' in email and len(password.replace(" ", "")) >= 16:
                            accounts.append({'email': email.strip(), 'password': password.strip().replace(" ", "")})
                        else:
                            add_log("WARN", f"Akun di file tidak valid: {line.strip()}")
                    except ValueError:
                        add_log("WARN", f"Format baris salah di {ACCOUNTS_FILE}: {line.strip()}")
                        continue
        except IOError:
            pass
    SENDER_ACCOUNTS = accounts
    
    # 2. Riwayat
    if os.path.exists(RIWAYAT_FILE):
        try:
            with open(RIWAYAT_FILE, 'r') as f:
                loaded_riwayat = json.load(f)
                RIWAYAT_PENGIRIMAN_GLOBAL = deque(loaded_riwayat[-500:], maxlen=500) 
        except (IOError, json.JSONDecodeError):
            add_log("WARN", f"Gagal memuat atau mem-parse {RIWAYAT_FILE}. Membuat baru.")
            RIWAYAT_PENGIRIMAN_GLOBAL = deque(maxlen=500)
            
    # 3. Telegram Users
    if os.path.exists(TELEGRAM_USERS_FILE):
        try:
            with open(TELEGRAM_USERS_FILE, 'r') as f:
                TELEGRAM_USER_MAP = json.load(f)
        except (IOError, json.JSONDecodeError):
            add_log("WARN", f"Gagal memuat atau mem-parse {TELEGRAM_USERS_FILE}. Membuat baru.")
            TELEGRAM_USER_MAP = {}

    add_log("INIT", f"Memuat {len(SENDER_ACCOUNTS)} akun & {len(RIWAYAT_PENGIRIMAN_GLOBAL)} riwayat.")

def save_data():
    """Saves all persistent data to files."""
    # 1. Riwayat
    try:
        with open(RIWAYAT_FILE, 'w') as f:
            json.dump(list(RIWAYAT_PENGIRIMAN_GLOBAL), f, indent=4)
    except Exception as e:
        add_log("ERROR", f"Gagal menyimpan riwayat: {e}")

    # 2. Accounts (hanya akun yang ditambahkan via web/API, bukan DEFAULT_ACCOUNTS)
    try:
        with open(ACCOUNTS_FILE, 'w') as f:
            start_index = len(DEFAULT_ACCOUNTS)
            for account in SENDER_ACCOUNTS[start_index:]:
                # Pastikan password tidak mengandung spasi saat disimpan
                f.write(f"{account['email']}:{account['password']}\n") 
    except Exception as e:
        add_log("ERROR", f"Gagal menyimpan akun: {e}")
        
    # 3. Telegram Users
    try:
        with open(TELEGRAM_USERS_FILE, 'w') as f:
            json.dump(TELEGRAM_USER_MAP, f, indent=4)
    except Exception as e:
        add_log("ERROR", f"Gagal menyimpan pengguna Telegram: {e}")

def sensor_email(email):
    """Censors email for privacy in logs."""
    if not email or '@' not in email: return "[Tidak Valid]"
    try:
        parts = email.split('@')
        username = parts[0]
        domain = parts[1]
        
        if len(username) > 4:
            username_censored = username[:2] + '***' + username[-2:]
        else:
            username_censored = '****'
            
        return f"{username_censored}@{domain}"
    except:
        return "[Format Salah]"

def normalize_phone_number(nomor):
    """Normalizes phone number to international format (+XX...)."""
    nomor_bersih = re.sub(r'[^\d+]', '', nomor) 
    
    # Jika sudah dimulai dengan '+' dan memiliki panjang yang masuk akal
    if re.match(r'^\+\d{5,}$', nomor_bersih):
        return nomor_bersih
        
    # Menghilangkan awalan '0'
    nomor_bersih = nomor_bersih.lstrip('0')

    # Jika nomor dimulai dengan kode negara (misal 62) dan tidak ada '+'
    if nomor_bersih.startswith('62') or nomor_bersih.startswith('1'): # Asumsi Indonesia/US
        if not nomor_bersih.startswith('+'):
            nomor_bersih = '+' + nomor_bersih
            
    # Jika tidak dimulai dengan kode negara yang jelas, anggap kode negara default (misal 62)
    elif len(nomor_bersih) >= 8:
        nomor_bersih = '+62' + nomor_bersih
    
    if re.match(r'^\+\d{5,}$', nomor_bersih):
        return nomor_bersih
        
    return None

def kirim_notifikasi_telegram(pesan, target_chat_id=None):
    """Sends notification message to Telegram (group or specific user)."""
    chat_id = target_chat_id if target_chat_id else TELEGRAM_GROUP_CHAT_ID

    if not TELEGRAM_BOT_TOKEN or not chat_id:
        add_log("WARN", "Token/ID Telegram kosong. Notifikasi dilewati.")
        return False
        
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        'chat_id': chat_id,
        'text': pesan,
        'parse_mode': 'HTML'
    }
    
    try:
        response = requests.post(url, json=payload, timeout=5)
        response.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        add_log("WARN", f"Gagal koneksi Telegram ke {chat_id}. Error: {e}")
        return False

def get_bot_status_text():
    """Generates a formatted status message for /status command."""
    status_text = f"""
🤖 STATUS BOT ANNAS FIX MERAH
Status Server: {'✅ AKTIF' if IS_BOT_RUNNING else '❌ OFFLINE'}
Total Akun Pengirim: <code>{len(SENDER_ACCOUNTS)}</code>
Total Riwayat Banding: <code>{len(RIWAYAT_PENGIRIMAN_GLOBAL)}</code>
Interval Cek Balasan: <code>{IMAP_CHECK_INTERVAL_SECONDS} detik</code>
Jumlah Pengguna Web Terdaftar: <code>{len(TELEGRAM_USER_MAP)}</code>

---
Akun Aktif:
"""
    if SENDER_ACCOUNTS:
        for i, acc in enumerate(SENDER_ACCOUNTS, 1):
            source = " (DEFAULT)" if i <= len(DEFAULT_ACCOUNTS) else " (CUSTOM)"
            status_text += f" • {i}. <code>{sensor_email(acc['email'])}</code>{source}\n"
    else:
        status_text += "Tidak ada akun pengirim aktif.\n"
        
    status_text += "\nKetik /help untuk daftar perintah."
    return status_text

# ==============================================================================
#                      *** LOGIKA INTI BOT (SMTP/IMAP) ***
# ==============================================================================

def kirim_email_banding(nomor_telepon, sender_telegram_id):
    """Core logic to send appeal email."""
    if not SENDER_ACCOUNTS:
        add_log("FATAL", "Tidak ada akun pengirim yang ditemukan.")
        return False, "Tidak ada akun pengirim yang tersedia."
        
    sender_account = random.choice(SENDER_ACCOUNTS)
    sender_email = sender_account['email']
    sender_password = sender_account['password']
    
    nomor_normalized = normalize_phone_number(nomor_telepon)

    if not nomor_normalized:
        return False, f"Nomor WhatsApp (<code>{nomor_telepon}</code>) tidak dapat divalidasi ke format internasional (+XX...)."

    subjek = f"Permintaan Peninjauan Akun Ditangguhkan: {nomor_normalized}"
    
    isi_email = f"""
Halo Tim Dukungan WhatsApp,

Saya ingin melaporkan masalah terkait nomor WhatsApp saya. Saat mencoba melakukan pendaftaran, selalu muncul pesan "Login Tidak Tersedia Untuk Saat Ini" atau "Akun Saya Ditangguhkan" tanpa alasan yang jelas.

Saya yakin akun saya telah ditangguhkan karena kesalahan. Saya memohon agar pihak WhatsApp dapat meninjau kembali dan memulihkan akses saya ke nomor tersebut.

Nomor WhatsApp saya adalah: {nomor_normalized}.

Terima kasih atas waktu dan perhatiannya.
"""

    try:
        msg = MIMEMultipart()
        msg['From'] = sender_email
        msg['To'] = RECEIVER_EMAIL
        msg['Subject'] = subjek
        msg.attach(MIMEText(isi_email, 'plain', 'utf-8'))

        server = smtplib.SMTP_SSL('smtp.gmail.com', 465)
        server.ehlo()
        server.login(sender_email, sender_password)
        server.sendmail(sender_email, RECEIVER_EMAIL, msg.as_string())
        server.close()

        email_censored = sensor_email(sender_email)
        
        add_log("SUCCESS", f"Banding {nomor_normalized} terkirim dari {email_censored}. Oleh ID {sender_telegram_id}.")
        
        telegram_kirim_msg = f"""
➡️ BANDING TERKIRIM
Nomor: <code>{nomor_normalized}</code>
Dikirim Dari: <code>{email_censored}</code>
Pengirim Web ID: <code>{sender_telegram_id}</code>
"""
        kirim_notifikasi_telegram(telegram_kirim_msg)
        
        if sender_telegram_id and sender_telegram_id in TELEGRAM_USER_MAP:
            kirim_notifikasi_telegram(f"✅ Banding untuk <code>{nomor_normalized}</code> berhasil dikirim. Akun pengirim: <code>{email_censored}</code>.", sender_telegram_id)
        
        RIWAYAT_PENGIRIMAN_GLOBAL.appendleft({
            'nomor': nomor_normalized, 
            'pengirim': sender_email, 
            'timestamp': datetime.now().isoformat(),
            'telegram_id': sender_telegram_id 
        })
        save_data()
        
        return True, f"Banding berhasil dikirim untuk <code>{nomor_normalized}</code> dari <code>{email_censored}</code>. Balasan akan dicek otomatis."

    except smtplib.SMTPAuthenticationError:
        add_log("ERROR", f"Autentikasi GAGAL untuk {sensor_email(sender_email)}. App Password salah atau IMAP/SMTP OFF.")
        return False, f"Autentikasi GAGAL untuk <code>{sensor_email(sender_email)}</code>. Cek App Password/Pengaturan Google."
    except Exception as e:
        add_log("ERROR", f"Gagal mengirim email untuk {nomor_normalized}. Error: {e}")
        return False, f"Gagal mengirim email untuk <code>{nomor_normalized}</code>. Error: {type(e).__name__}"


def check_and_notify_replies(item, account_data):
    """Checks for WhatsApp replies via IMAP."""
    nomor_banding = item['nomor']
    imap_user = account_data['email']
    imap_pass = account_data['password']
    sender_telegram_id = item.get('telegram_id')

    try:
        mail = imaplib.IMAP4_SSL('imap.gmail.com')
        mail.login(imap_user, imap_pass) 
        mail.select('inbox')

        search_criteria = f'(UNSEEN FROM "{RECEIVER_EMAIL}" TEXT "{nomor_banding}")'
        status, email_ids = mail.search(None, search_criteria)
        
        email_id_list = email_ids[0].split()
        
        if not email_id_list:
            mail.logout()
            return 
            
        latest_email_id = email_id_list[-1]
        status, msg_data = mail.fetch(latest_email_id, '(RFC822)')
        
        raw_email = msg_data[0][1]
        msg = email_parser.message_from_bytes(raw_email)
        subject = msg['subject']
        
        body = ""
        for part in msg.walk():
            ctype = part.get_content_type()
            cdispo = str(part.get('Content-Disposition'))
            if ctype == 'text/plain' and 'attachment' not in cdispo:
                body = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                break
                
        email_pengirim_sensor = sensor_email(imap_user)
        
        add_log("ALERT", f"Balasan DITERIMA untuk {nomor_banding} di {email_pengirim_sensor}.")
                
        telegram_message_group = f"""
🚨 BALASAN WHATSAPP MASUK! 🚨
Nomor Banding: <code>{nomor_banding}</code>
Pengirim Banding (Akun Anda): <code>{email_pengirim_sensor}</code>
Subjek: {subject}

--- ISI BALASAN (350 karakter pertama) ---
<code>{body[:350].strip()}...</code>

Notifikasi dikirim ke user ID: <code>{sender_telegram_id}</code>
"""
        kirim_notifikasi_telegram(telegram_message_group)
        
        if sender_telegram_id and sender_telegram_id in TELEGRAM_USER_MAP:
             kirim_notifikasi_telegram(f"🔥 BALASAN WHATSAPP MASUK untuk nomor <code>{nomor_banding}</code>! Subjek: {subject}", sender_telegram_id)
        
        mail.store(latest_email_id, '+FLAGS', '\\Seen')
        mail.logout()
        
    except imaplib.IMAP4.error as e:
        # Ini akan sering terjadi jika App Password salah, penting untuk log
        add_log("ERROR", f"IMAP GAGAL koneksi/login untuk {sensor_email(imap_user)}. Cek App Password/IMAP.")
    except Exception as e:
        add_log("ERROR", f"Kesalahan umum saat cek IMAP untuk {nomor_banding}: {e}")

# ==============================================================================
#                    *** TELEGRAM COMMAND HANDLERS ***
# ==============================================================================

def handle_telegram_addbot(chat_id, args_list):
    """Handles the /addbot command from Telegram."""
    if len(args_list) < 3:
        kirim_notifikasi_telegram("Format salah. Gunakan: `/addbot email password` (Pastikan password 16 digit App Password tanpa spasi).", chat_id)
        return

    email = args_list[1].strip()
    password = args_list[2].strip().replace(" ", "")

    if '@' not in email or len(password) != 16:
        kirim_notifikasi_telegram("Email atau App Password tidak valid. Pastikan App Password 16 digit.", chat_id)
        return
        
    # Check if account already exists
    if any(acc['email'] == email for acc in SENDER_ACCOUNTS):
        kirim_notifikasi_telegram(f"Akun <code>{sensor_email(email)}</code> sudah terdaftar.", chat_id)
        return

    try:
        # Test SMTP login
        server = smtplib.SMTP_SSL('smtp.gmail.com', 465)
        server.ehlo()
        server.login(email, password)
        server.close()
        
        # If successful, add
        new_account = {'email': email, 'password': password}
        SENDER_ACCOUNTS.append(new_account)
        save_data()
        add_log("SUCCESS", f"Akun {sensor_email(email)} berhasil ditambahkan via Telegram.")
        
        kirim_notifikasi_telegram(f"✅ Akun <code>{sensor_email(email)}</code> berhasil ditambahkan! Total akun: {len(SENDER_ACCOUNTS)}", chat_id)

    except smtplib.SMTPAuthenticationError:
        add_log("ERROR", f"Autentikasi GAGAL untuk {sensor_email(email)} dari Telegram.")
        kirim_notifikasi_telegram(f"❌ Autentikasi GAGAL untuk <code>{sensor_email(email)}</code>. Cek App Password/Pengaturan Google.", chat_id)
    except Exception as e:
        add_log("ERROR", f"Kesalahan saat penambahan akun: {e}")
        kirim_notifikasi_telegram(f"❌ Terjadi kesalahan saat penambahan akun. Error: {type(e).__name__}", chat_id)


def handle_telegram_fix(chat_id, args_list):
    """Handles the /fix command from Telegram."""
    if len(args_list) < 2:
        kirim_notifikasi_telegram("Format salah. Gunakan: `/fix +628xxxxxxxx` (Sertakan kode negara).", chat_id)
        return

    number = args_list[1].strip()
    
    # Gunakan chat_id Telegram sebagai sender_telegram_id
    status, message = kirim_email_banding(number, str(chat_id))
    
    # Kirim balasan ke chat pribadi pengguna
    kirim_notifikasi_telegram(message, chat_id)


def process_telegram_updates(updates):
    """Processes incoming Telegram messages for commands."""
    global TELEGRAM_LAST_UPDATE_ID
    
    for update in updates:
        # Pastikan ini adalah pesan baru dan bukan update lain
        if 'message' not in update: continue 
            
        message = update['message']
        chat_id = message['chat']['id']
        text = message.get('text', '')
        
        # Update last processed ID
        TELEGRAM_LAST_UPDATE_ID = update['update_id'] + 1
        
        if not text.startswith('/'): continue

        parts = text.split()
        command = parts[0].lower()
        
        # Log command
        user_info = message['from'].get('username', message['from'].get('first_name', 'Unknown'))
        add_log("INFO", f"Perintah Telegram dari {user_info} ({chat_id}): {text}")

        if command == '/start':
            welcome_msg = f"""
👋 Halo, {user_info}!
Saya Bot Annas Fix Merah untuk banding WhatsApp.
Saya dapat mengirim banding dan melacak balasan email.

Perintah:
/status - Lihat status bot dan akun pengirim.
/addbot email app_password - Tambahkan akun pengirim baru.
/fix nomor_wa - Kirim banding untuk nomor WA (Contoh: /fix +62812xxxx)
"""
            kirim_notifikasi_telegram(welcome_msg, chat_id)
            
        elif command == '/status':
            kirim_notifikasi_telegram(get_bot_status_text(), chat_id)

        elif command == '/addbot':
            handle_telegram_addbot(chat_id, parts)
            
        elif command == '/fix':
            handle_telegram_fix(chat_id, parts)
            
        else:
            kirim_notifikasi_telegram("Perintah tidak dikenal. Ketik /start untuk melihat daftar perintah.", chat_id)


def telegram_polling_loop():
    """Polls Telegram API for new messages/commands."""
    global TELEGRAM_LAST_UPDATE_ID
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    
    while IS_BOT_RUNNING:
        try:
            # Menggunakan update_id untuk hanya mendapatkan pesan baru
            response = requests.get(url, params={'offset': TELEGRAM_LAST_UPDATE_ID, 'timeout': 5}, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            if data['ok'] and data['result']:
                process_telegram_updates(data['result'])
                
        except requests.exceptions.RequestException as e:
            add_log("WARN", f"Gagal Polling Telegram: {e}. Cek koneksi internet/token bot.")
            
        time.sleep(TELEGRAM_POLLING_INTERVAL)

# ==============================================================================
#                          *** THREAD LATAR BELAKANG ***
# ==============================================================================

class BackgroundWorker(threading.Thread):
    def __init__(self):
        threading.Thread.__init__(self)
        self._stop_event = threading.Event()
        self.name = "Background-Worker"
        self.imap_last_check = time.time() # Inisialisasi waktu cek IMAP terakhir

    def run(self):
        """Main loop for IMAP check and Telegram Polling."""
        global IS_BOT_RUNNING
        IS_BOT_RUNNING = True
        
        load_data()
        
        add_log("INIT", f"Bot Telegram & Cek IMAP dimulai. Cek balasan setiap {IMAP_CHECK_INTERVAL_SECONDS} detik.")
        
        # Start Telegram polling in the same thread, but use small delays
        
        # Waktu IMAP check
        self.imap_last_check = time.time() 

        # Polling Telegram loop
        while not self._stop_event.is_set():
            
            # --- TELEGRAM COMMANDS CHECK ---
            telegram_polling_loop()
            
            # --- AUTOMATIC IMAP CHECK FOR REPLIES ---
            if time.time() - self.imap_last_check >= IMAP_CHECK_INTERVAL_SECONDS:
                if SENDER_ACCOUNTS and len(RIWAYAT_PENGIRIMAN_GLOBAL) > 0:
                    add_log("INFO", f"Memeriksa {len(RIWAYAT_PENGIRIMAN_GLOBAL)} riwayat pengiriman untuk balasan...")
                    
                    riwayat_check = list(RIWAYAT_PENGIRIMAN_GLOBAL)[:50] # Check 50 newest
                    
                    for item in riwayat_check: 
                        try:
                            account_data = next(acc for acc in SENDER_ACCOUNTS if acc['email'] == item['pengirim'])
                            check_and_notify_replies(item, account_data)
                        except StopIteration:
                            continue
                        except Exception as e:
                            add_log("ERROR", f"Gagal memproses cek IMAP: {e}")
                                
                    add_log("INFO", f"Selesai cek IMAP.")
                self.imap_last_check = time.time() # Reset timer
                
            time.sleep(1) # Delay kecil untuk menjaga CPU

    def stop(self):
        self._stop_event.set()
        global IS_BOT_RUNNING
        IS_BOT_RUNNING = False

# ==============================================================================
#                        *** FLASK APP DAN RUTES API ***
# ==============================================================================

app = Flask(__name__)
CORS(app) 

@app.route('/api/status', methods=['GET'])
def get_status():
    """Returns bot status, logs, and statistics for the web dashboard."""
    logs_to_send = list(LOG_QUEUE)
    # Log terakhir adalah yang paling baru (seperti yang disimpan di queue)
    logs_to_send.reverse() 
    
    return jsonify({
        'status': 'AKTIF' if IS_BOT_RUNNING else 'OFFLINE',
        'accounts_count': len(SENDER_ACCOUNTS),
        'riwayat_count': len(RIWAYAT_PENGIRIMAN_GLOBAL),
        'imap_interval': IMAP_CHECK_INTERVAL_SECONDS,
        'logs': logs_to_send
    })

@app.route('/api/login_telegram', methods=['POST'])
def handle_telegram_login_api():
    """Handles Telegram ID submission from the web for notification mapping."""
    data = request.json
    telegram_id = data.get('telegram_id', '').strip()
    telegram_username = data.get('telegram_username', '').strip()
    
    if not telegram_id or not telegram_username:
        return jsonify({'message': 'ID dan Username Telegram tidak boleh kosong.'}), 400
        
    try:
        telegram_id = str(int(telegram_id)) 
        
        TELEGRAM_USER_MAP[telegram_id] = telegram_username
        save_data()
        
        add_log("INFO", f"Pengguna web baru terdaftar: {telegram_username} ({telegram_id})")
        
        # Kirim notifikasi pribadi
        kirim_notifikasi_telegram(f"🎉 Login Web Berhasil! \n\nSelamat datang, <code>{telegram_username}</code>! ID Anda: <code>{telegram_id}</code>.", telegram_id)
        
        return jsonify({'message': 'Berhasil terhubung. Cek chat bot Anda.', 'status': 'success'}), 200

    except ValueError:
        return jsonify({'message': 'ID Telegram harus berupa angka valid.'}), 400
    except Exception as e:
        add_log("ERROR", f"Gagal mendaftarkan Telegram ID: {e}")
        return jsonify({'message': 'Terjadi kesalahan saat pendaftaran ID Telegram.'}), 500


@app.route('/api/send_appeal', methods=['POST'])
def handle_send_appeal_api():
    """Handles appeal sending request from web (equivalent to /fix)."""
    data = request.json
    number = data.get('number', '').strip()
    telegram_id = data.get('telegram_id', '').strip()

    if not IS_BOT_RUNNING:
        return jsonify({'message': 'Server Bot sedang OFFLINE. Mohon tunggu.'}), 503
        
    if not number:
        return jsonify({'message': 'Nomor WhatsApp tidak boleh kosong.'}), 400
    
    status, message = kirim_email_banding(number, telegram_id)
    
    return jsonify({'message': message, 'status': 'success' if status else 'failed'}), 200

@app.route('/api/add_account', methods=['POST'])
def handle_add_account_api():
    """Handles new account addition request from web (equivalent to /addbot)."""
    global SENDER_ACCOUNTS
    data = request.json
    email = data.get('email', '').strip()
    password = data.get('password', '').strip().replace(" ", "")

    if not email or not password or '@' not in email or len(password) != 16:
        return jsonify({'message': 'Format Email atau App Password salah. App Password Wajib 16 karakter.'}), 400
    
    if any(acc['email'] == email for acc in SENDER_ACCOUNTS):
        return jsonify({'message': f"Akun {sensor_email(email)} sudah terdaftar."}), 409

    try:
        server = smtplib.SMTP_SSL('smtp.gmail.com', 465)
        server.ehlo()
        server.login(email, password)
        server.close()
        
        new_account = {'email': email, 'password': password}
        SENDER_ACCOUNTS.append(new_account)
        save_data()
        add_log("SUCCESS", f"Akun {sensor_email(email)} berhasil ditambahkan dan teruji login.")
        
        return jsonify({
            'message': f"Akun {sensor_email(email)} berhasil ditambahkan dan teruji login! Total akun: {len(SENDER_ACCOUNTS)}",
            'total_accounts': len(SENDER_ACCOUNTS)
        }), 200

    except smtplib.SMTPAuthenticationError:
        add_log("ERROR", f"Autentikasi GAGAL untuk {sensor_email(email)}.")
        return jsonify({'message': 'Autentikasi GAGAL. Cek App Password/Pengaturan Google Anda.'}), 401
    except Exception as e:
        add_log("ERROR", f"Terjadi kesalahan saat verifikasi/penambahan: {type(e).__name__}")
        return jsonify({'message': f'Terjadi kesalahan saat verifikasi/penambahan. Error: {type(e).__name__}'}), 500


if __name__ == '__main__':
    # Pastikan file data ada
    if not os.path.exists(ACCOUNTS_FILE):
        with open(ACCOUNTS_FILE, 'w') as f: pass
    if not os.path.exists(RIWAYAT_FILE):
        with open(RIWAYAT_FILE, 'w') as f: json.dump([], f)
    if not os.path.exists(TELEGRAM_USERS_FILE):
        with open(TELEGRAM_USERS_FILE, 'w') as f: json.dump({}, f)

    # Inisialisasi dan jalankan thread background
    worker_thread = BackgroundWorker()
    worker_thread.daemon = True 
    worker_thread.start()
    
    add_log("INIT", "Memulai Flask Server di port 5000...")
    
    try:
        # Jalankan di port 5000 agar mudah diekspos oleh Cloudflared di Termux
        app.run(host='0.0.0.0', port=5000) 
    except KeyboardInterrupt:
        worker_thread.stop()
        worker_thread.join()
        add_log("FATAL", "Server dihentikan oleh pengguna.")
    except Exception as e:
        worker_thread.stop()
        worker_thread.join()
        add_log("FATAL", f"Kesalahan fatal server: {e}")