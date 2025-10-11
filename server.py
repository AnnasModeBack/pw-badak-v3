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

# --- KONFIGURASI BOT (WAJIB GANTI) ---
# Dapatkan dari BotFather dan Channel Anda
TELEGRAM_BOT_TOKEN = "6589420280:AAEPgvt6DdvZdtZ0NM-olXz9XNySr6PDNYM"  # Ganti dengan Token Bot Anda
TELEGRAM_CHAT_ID = "-1003102738220" # Ganti dengan ID Channel/Group Anda

ACCOUNTS_FILE = "accounts.txt"
RIWAYAT_FILE = "riwayat_kirim.json"
IMAP_CHECK_INTERVAL_SECONDS = 60 # Cek balasan IMAP setiap 60 detik
RECEIVER_EMAIL = "support@support.whatsapp.com"

# Akun default untuk pengujian/starter
DEFAULT_ACCOUNTS = [
    {'email': "annasrullah916@gmail.com", 'password': "vsgs ndxi tsev aqwv"},
    {'email': "sgjutaf@gmail.com", 'password': "ckgx tnga otiq ufer"},
]

# Variabel Global
SENDER_ACCOUNTS = []
RIWAYAT_PENGIRIMAN_GLOBAL = deque()
LOG_QUEUE = deque(maxlen=100)
IS_BOT_RUNNING = False

# --- UTILITY LOGGING & DATA ---

def add_log(level, message):
    """Adds a log entry to the global queue and prints to console."""
    timestamp = datetime.now().strftime("%H:%M:%S")
    log_entry = {'timestamp': timestamp, 'level': level, 'message': message}
    print(f"[{level}] {timestamp} - {message}")
    LOG_QUEUE.appendleft(log_entry)

def load_accounts():
    """Loads default and additional accounts from file."""
    global SENDER_ACCOUNTS
    accounts = list(DEFAULT_ACCOUNTS)
    
    if os.path.exists(ACCOUNTS_FILE):
        with open(ACCOUNTS_FILE, 'r') as f:
            for line in f:
                try:
                    email, password = line.strip().split(':', 1)
                    if email and password and '@' in email:
                        accounts.append({'email': email.strip(), 'password': password.strip()})
                except ValueError:
                    continue
    SENDER_ACCOUNTS = accounts
    add_log("INIT", f"Memuat {len(accounts)} akun pengirim.")

def load_riwayat():
    """Loads history from file."""
    global RIWAYAT_PENGIRIMAN_GLOBAL
    if os.path.exists(RIWAYAT_FILE):
        try:
            with open(RIWAYAT_FILE, 'r') as f:
                RIWAYAT_PENGIRIMAN_GLOBAL = deque(json.load(f))
        except (IOError, json.JSONDecodeError):
            RIWAYAT_PENGIRIMAN_GLOBAL = deque()

def save_riwayat():
    """Saves history to file."""
    try:
        with open(RIWAYAT_FILE, 'w') as f:
            json.dump(list(RIWAYAT_PENGIRIMAN_GLOBAL), f)
    except Exception as e:
        add_log("ERROR", f"Gagal menyimpan riwayat: {e}")

def save_accounts():
    """Saves additional accounts to accounts.txt (skipping default accounts)."""
    try:
        with open(ACCOUNTS_FILE, 'w') as f:
            start_index = len(DEFAULT_ACCOUNTS)
            for account in SENDER_ACCOUNTS[start_index:]:
                f.write(f"{account['email']}:{account['password']}\n")
    except Exception as e:
        add_log("ERROR", f"Gagal menyimpan akun: {e}")

def sensor_email(email):
    """Censors email for privacy in logs."""
    if not email or '@' not in email: return "[Tidak Valid]"
    try:
        parts = email.split('@')
        username = parts[0]
        domain = parts[1]
        
        if len(username) > 2:
            username_censored = username[0] + '***' + username[-1]
        else:
            username_censored = '****'
            
        return f"{username_censored}@{domain}"
    except:
        return "[Format Salah]"

def normalize_phone_number(nomor):
    """Normalizes phone number to international format (+XX...)."""
    nomor_bersih = re.sub(r'[^\d+]', '', nomor)
    nomor_bersih = nomor_bersih.lstrip('0')
    if nomor_bersih.startswith('+'):
        nomor_bersih = '+' + nomor_bersih.lstrip('+')

    if not nomor_bersih.startswith('+') and len(nomor_bersih) >= 5:
        return '+' + nomor_bersih
        
    return nomor_bersih

def kirim_notifikasi_telegram(pesan):
    """Sends notification message to Telegram."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        add_log("WARN", "Token/ID Telegram kosong. Notifikasi dilewati.")
        return False
        
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        'chat_id': TELEGRAM_CHAT_ID,
        'text': pesan,
        'parse_mode': 'HTML'
    }
    
    try:
        requests.post(url, json=payload, timeout=5)
        return True
    except requests.exceptions.RequestException:
        add_log("WARN", "Gagal koneksi Telegram.")
        return False

# --- LOGIKA INTI BOT (SMTP/IMAP) ---

def kirim_email_banding(nomor_telepon):
    """Core logic to send appeal email."""
    if not SENDER_ACCOUNTS:
        add_log("FATAL", "Tidak ada akun pengirim yang ditemukan.")
        return False, "Tidak ada akun pengirim yang tersedia."
        
    sender_account = random.choice(SENDER_ACCOUNTS)
    sender_email = sender_account['email']
    sender_password = sender_account['password']
    
    nomor_normalized = normalize_phone_number(nomor_telepon)

    if not re.match(r'^\+\d{5,}$', nomor_normalized):
        return False, f"Nomor {nomor_telepon} tidak valid."

    subjek = f"Permintaan Peninjauan Akun Ditangguhkan: {nomor_normalized}"
    isi_email = f"""
Halo Tim Dukungan WhatsApp,

Saya ingin melaporkan masalah terkait nomor WhatsApp saya. Saat mencoba melakukan pendaftaran, selalu muncul pesan "Login Tidak Tersedia Untuk Saat Ini".

Nomor WhatsApp saya adalah: {nomor_normalized}.

Saya mohon agar pihak WhatsApp dapat membantu agar saya bisa menggunakan kembali nomor saya tanpa muncul kendala tersebut. Terima kasih.
    """

    try:
        msg = MIMEMultipart()
        msg['From'] = sender_email
        msg['To'] = RECEIVER_EMAIL
        msg['Subject'] = subjek
        msg.attach(MIMEText(isi_email, 'plain'))

        server = smtplib.SMTP_SSL('smtp.gmail.com', 465)
        server.ehlo()
        server.login(sender_email, sender_password)
        server.sendmail(sender_email, RECEIVER_EMAIL, msg.as_string())
        server.close()

        email_censored = sensor_email(sender_email)
        
        add_log("SUCCESS", f"Banding {nomor_normalized} terkirim dari {email_censored}.")
        
        telegram_kirim_msg = f"""
➡️ BANDING TERKIRIM
Nomor: <code>{nomor_normalized}</code>
Dikirim Dari: <code>{email_censored}</code>
"""
        kirim_notifikasi_telegram(telegram_kirim_msg)
        
        # Add to history
        RIWAYAT_PENGIRIMAN_GLOBAL.appendleft({'nomor': nomor_normalized, 'pengirim': sender_email, 'timestamp': datetime.now().isoformat()})
        save_riwayat()
        
        return True, f"Banding berhasil dikirim untuk {nomor_normalized} dari {email_censored}."

    except smtplib.SMTPAuthenticationError:
        add_log("ERROR", f"Autentikasi GAGAL untuk {sensor_email(sender_email)}. App Password salah atau IMAP/SMTP OFF.")
        return False, "Autentikasi GAGAL. Cek App Password/Pengaturan Google."
    except Exception as e:
        add_log("ERROR", f"Gagal mengirim email untuk {nomor_normalized}. Error: {e}")
        return False, f"Gagal mengirim email. Error umum: {e}"


def check_and_notify_replies(item, account_data):
    """Checks for WhatsApp replies via IMAP."""
    nomor_banding = item['nomor']
    imap_user = account_data['email']
    imap_pass = account_data['password']

    try:
        mail = imaplib.IMAP4_SSL('imap.gmail.com')
        mail.login(imap_user, imap_pass) 
        mail.select('inbox')

        # Search for unseen emails from WhatsApp containing the appeal number
        search_criteria = f'(UNSEEN FROM "support@support.whatsapp.com" TEXT "{nomor_banding}")'
        status, email_ids = mail.search(None, search_criteria)
        
        email_id_list = email_ids[0].split()
        
        if not email_id_list:
            mail.logout()
            return 
            
        latest_email_id = email_id_list[-1]
        status, msg_data = mail.fetch(latest_email_id, '(RFC822)')
        
        # Parse email
        raw_email = msg_data[0][1]
        msg = email_parser.message_from_bytes(raw_email)
        subject = msg['subject']
        
        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                ctype = part.get_content_type()
                cdispo = str(part.get('Content-Disposition'))
                if ctype == 'text/plain' and 'attachment' not in cdispo:
                    body = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                    break
        else:
            body = msg.get_payload(decode=True).decode('utf-8', errors='ignore')
                
        email_pengirim_sensor = sensor_email(imap_user)
        
        add_log("ALERT", f"Balasan DITERIMA untuk {nomor_banding} di {email_pengirim_sensor}.")
                
        telegram_message = f"""
🚨 BALASAN WHATSAPP MASUK! 🚨
Nomor Banding: <code>{nomor_banding}</code>
Pengirim Banding (Akun Anda): <code>{email_pengirim_sensor}</code>
Subjek: {subject}

--- ISI BALASAN ---
<code>{body[:350].strip()}...</code>
"""
        kirim_notifikasi_telegram(telegram_message)
        
        # Mark email as read
        mail.store(latest_email_id, '+FLAGS', '\\Seen')
        mail.logout()
        
    except imaplib.IMAP4.error as e:
        add_log("ERROR", f"IMAP GAGAL koneksi/login untuk {sensor_email(imap_user)}. Cek App Password/IMAP ON.")
    except Exception as e:
        add_log("ERROR", f"Kesalahan saat cek IMAP: {e}")

# --- THREAD LATAR BELAKANG ---

class BackgroundWorker(threading.Thread):
    def __init__(self):
        threading.Thread.__init__(self)
        self._stop_event = threading.Event()
        self.last_imap_check_time = 0
        self.name = "Background-Worker"

    def run(self):
        """Main loop for IMAP check."""
        global IS_BOT_RUNNING, SENDER_ACCOUNTS
        IS_BOT_RUNNING = True
        
        load_accounts()
        load_riwayat()
        
        add_log("INIT", "Bot Telegram & Cek IMAP dimulai di latar belakang...")
        self.last_imap_check_time = int(time.time())

        while not self._stop_event.is_set():
            time.sleep(1)
            current_time = int(time.time())
            
            # --- Automatic IMAP Check for Replies ---
            if SENDER_ACCOUNTS and len(RIWAYAT_PENGIRIMAN_GLOBAL) > 0 and (current_time - self.last_imap_check_time) >= IMAP_CHECK_INTERVAL_SECONDS:
                add_log("INFO", f"Memeriksa {len(RIWAYAT_PENGIRIMAN_GLOBAL)} riwayat pengiriman...")
                
                for item in list(RIWAYAT_PENGIRIMAN_GLOBAL): 
                    try:
                        account_data = next(acc for acc in SENDER_ACCOUNTS if acc['email'] == item['pengirim'])
                        check_and_notify_replies(item, account_data)
                    except StopIteration:
                        continue # Skip if credential is removed
                    except Exception as e:
                        add_log("ERROR", f"Gagal memproses cek IMAP: {e}")
                            
                self.last_imap_check_time = current_time
                add_log("INFO", f"Selesai. Cek IMAP berikutnya dalam {IMAP_CHECK_INTERVAL_SECONDS} detik.")

    def stop(self):
        self._stop_event.set()
        global IS_BOT_RUNNING
        IS_BOT_RUNNING = False

# --- FLASK APP DAN RUTES API ---

app = Flask(__name__)
# Enable CORS for Netlify frontend to connect
CORS(app) 

@app.route('/')
def serve_index():
    """Serves index.html from the current directory."""
    try:
        return send_from_directory(os.getcwd(), 'index.html')
    except FileNotFoundError:
        return "File index.html tidak ditemukan.", 404

@app.route('/api/status', methods=['GET'])
def get_status():
    """Returns bot status, logs, and statistics for the web dashboard."""
    logs_to_send = list(LOG_QUEUE)
    
    return jsonify({
        'status': 'AKTIF' if IS_BOT_RUNNING else 'OFFLINE',
        'accounts_count': len(SENDER_ACCOUNTS),
        'riwayat_count': len(RIWAYAT_PENGIRIMAN_GLOBAL),
        'imap_interval': IMAP_CHECK_INTERVAL_SECONDS,
        'logs': logs_to_send
    })

@app.route('/api/send_appeal', methods=['POST'])
def handle_send_appeal():
    """Handles appeal sending request from web."""
    data = request.json
    number = data.get('number', '').strip()

    if not number:
        return jsonify({'message': 'Nomor WhatsApp tidak boleh kosong.'}), 400
    
    status, message = kirim_email_banding(number)
    
    return jsonify({'message': message, 'status': 'success' if status else 'failed'}), 200 if status else 400

@app.route('/api/add_account', methods=['POST'])
def handle_add_account():
    """Handles new account addition request from web."""
    global SENDER_ACCOUNTS
    data = request.json
    email = data.get('email', '').strip()
    password = data.get('password', '').strip()

    if not email or not password or '@' not in email or len(password) < 16:
        return jsonify({'message': 'Format Email atau App Password salah.'}), 400
    
    new_account = {'email': email, 'password': password}

    try:
        # Test SMTP login before adding
        server = smtplib.SMTP_SSL('smtp.gmail.com', 465)
        server.ehlo()
        server.login(email, password)
        server.close()
        
        if any(acc['email'] == email for acc in SENDER_ACCOUNTS):
            return jsonify({'message': f"Akun {sensor_email(email)} sudah terdaftar."}), 409

        SENDER_ACCOUNTS.append(new_account)
        save_accounts()
        add_log("SUCCESS", f"Akun {sensor_email(email)} berhasil ditambahkan.")
        
        return jsonify({
            'message': f"Akun {sensor_email(email)} berhasil ditambahkan dan teruji login!",
            'total_accounts': len(SENDER_ACCOUNTS)
        }), 200

    except smtplib.SMTPAuthenticationError:
        add_log("ERROR", f"Autentikasi GAGAL untuk {sensor_email(email)}. App Password salah.")
        return jsonify({'message': 'Autentikasi GAGAL. Cek App Password/Pengaturan Google Anda.'}), 401
    except Exception as e:
        add_log("ERROR", f"Terjadi kesalahan saat verifikasi/penambahan: {e}")
        return jsonify({'message': f'Terjadi kesalahan saat verifikasi/penambahan: {e}'}), 500


if __name__ == '__main__':
    worker_thread = BackgroundWorker()
    worker_thread.daemon = True 
    worker_thread.start()
    
    add_log("INIT", f"Bot Aktif! {len(SENDER_ACCOUNTS)} Akun siap. IMAP cek setiap {IMAP_CHECK_INTERVAL_SECONDS}s.")
    
    try:
        app.run(host='0.0.0.0', port=5000)
    except KeyboardInterrupt:
        worker_thread.stop()
        worker_thread.join()
        add_log("FATAL", "Server dihentikan oleh pengguna.")
    except Exception as e:
        worker_thread.stop()
        worker_thread.join()
        add_log("FATAL", f"Kesalahan fatal server: {e}")