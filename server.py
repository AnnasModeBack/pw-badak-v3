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
from flask import Flask, request, jsonify
from flask_cors import CORS
import telebot # Library for Telegram Bot (PyTelegramBotAPI)

# --- KONFIGURASI UMUM & TOKEN API ---
# PENTING: GANTI DENGAN TOKEN ASLI ANDA
TELEGRAM_BOT_TOKEN = "6589420280:AAEPgvt6DdvZdtZ0NM-olXz9XNySr6PDNYM"
TELEGRAM_CHAT_ID = "-1003102738220"
RECEIVER_EMAIL = "support@support.whatsapp.com"
ACCOUNTS_FILE = "accounts.txt"
RIWAYAT_FILE = "riwayat_kirim.json"
IMAP_CHECK_INTERVAL_SECONDS = 60
WEB_URL = "https://webfixmerahbyanas.netlify.app/" # URL web yang diminta untuk command /start

# Akun default (Wajib ada)
DEFAULT_ACCOUNTS = [
    {'email': "annasrullah916@gmail.com", 'password': "vsgs ndxi tsev aqwv"},
    {'email': "sgjutaf@gmail.com", 'password': "ckgx tnga otiq ufer"},
]

# Variabel Global
SENDER_ACCOUNTS = []
RIWAYAT_PENGIRIMAN_GLOBAL = deque()
LOG_QUEUE = deque(maxlen=100)
IS_BOT_RUNNING = False
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

# --- UTILITY LOGGING & DATA PERSISTENCE ---

def add_log(level, message, console_only=False):
    """Menambahkan entri log ke antrian global dan mencetak ke konsol."""
    timestamp = datetime.now().strftime("%H:%M:%S")
    log_entry = {'timestamp': timestamp, 'level': level, 'message': message}
    print(f"[{level}] {timestamp} - {message}")
    if not console_only:
        LOG_QUEUE.appendleft(log_entry)

def load_accounts():
    """Memuat akun default dan tambahan dari file."""
    global SENDER_ACCOUNTS
    accounts = list(DEFAULT_ACCOUNTS)
    
    if os.path.exists(ACCOUNTS_FILE):
        try:
            with open(ACCOUNTS_FILE, 'r') as f:
                for line in f:
                    try:
                        email, password = line.strip().split(':', 1)
                        if email and password and '@' in email:
                            # Hanya tambahkan jika belum ada di default
                            if not any(acc['email'] == email for acc in DEFAULT_ACCOUNTS):
                                accounts.append({'email': email.strip(), 'password': password.strip()})
                    except ValueError:
                        continue
        except Exception as e:
            add_log("ERROR", f"Gagal memuat {ACCOUNTS_FILE}: {e}", console_only=True)

    SENDER_ACCOUNTS = accounts
    add_log("INIT", f"Memuat {len(accounts)} akun pengirim.")

def load_riwayat():
    """Memuat riwayat dari file."""
    global RIWAYAT_PENGIRIMAN_GLOBAL
    if os.path.exists(RIWAYAT_FILE):
        try:
            with open(RIWAYAT_FILE, 'r') as f:
                RIWAYAT_PENGIRIMAN_GLOBAL = deque(json.load(f), maxlen=500) # Batasi riwayat
        except (IOError, json.JSONDecodeError):
            RIWAYAT_PENGIRIMAN_GLOBAL = deque(maxlen=500)

def save_riwayat():
    """Menyimpan riwayat ke file."""
    try:
        with open(RIWAYAT_FILE, 'w') as f:
            json.dump(list(RIWAYAT_PENGIRIMAN_GLOBAL), f, indent=4)
    except Exception as e:
        add_log("ERROR", f"Gagal menyimpan riwayat: {e}")

def save_accounts():
    """Menyimpan akun tambahan ke accounts.txt (melewati akun default)."""
    try:
        with open(ACCOUNTS_FILE, 'w') as f:
            # Lewati akun default (yang tidak perlu disimpan ulang)
            start_index = len(DEFAULT_ACCOUNTS)
            for account in SENDER_ACCOUNTS[start_index:]:
                # Pastikan akun yang disimpan adalah yang ditambahkan oleh user, bukan default
                if account not in DEFAULT_ACCOUNTS:
                    f.write(f"{account['email']}:{account['password']}\n")
    except Exception as e:
        add_log("ERROR", f"Gagal menyimpan akun: {e}")

def sensor_email(email):
    """Menyensor email untuk privasi di log."""
    if not email or '@' not in email: return "[Tidak Valid]"
    try:
        parts = email.split('@')
        username = parts[0]
        domain = parts[1]
        
        if len(username) > 4:
            username_censored = username[0:2] + '***' + username[-2:]
        elif len(username) > 2:
            username_censored = username[0] + '***'
        else:
            username_censored = '****'
            
        return f"{username_censored}@{domain}"
    except:
        return "[Format Salah]"

def normalize_phone_number(nomor):
    """Menormalkan nomor telepon ke format internasional (+XX...)."""
    # Menghapus semua karakter selain angka dan '+'
    nomor_bersih = re.sub(r'[^\d+]', '', nomor)
    
    # Jika diawali '0', hapus dan coba tambahkan kode negara default jika perlu (misal Indonesia +62)
    if nomor_bersih.startswith('0'):
        nomor_bersih = nomor_bersih.lstrip('0')
        # Jika panjang setelah '0' cukup dan tidak ada '+' (asumsi nomor lokal)
        if len(nomor_bersih) >= 8 and not nomor_bersih.startswith('+'):
            # Ini hanya contoh untuk nomor Indonesia, ganti jika perlu
            return '+62' + nomor_bersih

    # Jika sudah memiliki '+' di awal
    if nomor_bersih.startswith('+'):
        return '+' + nomor_bersih.lstrip('+')

    # Jika belum ada '+' dan panjangnya seperti nomor internasional
    if len(nomor_bersih) >= 8: # Minimal panjang nomor internasional (contoh +12345678)
        # Tambahkan '+' secara default jika tidak ada. Asumsi user memasukkan kode negara.
        return '+' + nomor_bersih
        
    return nomor_bersih

# --- LOGIKA INTI BOT (SMTP/IMAP) ---

def kirim_email_banding(nomor_telepon):
    """Logika inti untuk mengirim email banding."""
    if not SENDER_ACCOUNTS:
        add_log("FATAL", "Tidak ada akun pengirim yang ditemukan.")
        return False, "Tidak ada akun pengirim yang tersedia."
        
    sender_account = random.choice(SENDER_ACCOUNTS)
    sender_email = sender_account['email']
    sender_password = sender_account['password']
    
    nomor_normalized = normalize_phone_number(nomor_telepon)

    # Validasi minimal 5 digit setelah '+'
    if not re.match(r'^\+\d{5,}$', nomor_normalized):
        return False, f"Nomor {nomor_telepon} setelah normalisasi ({nomor_normalized}) tidak valid."

    # Pembuatan Subjek dan Isi Email
    subjek = f"Permintaan Peninjauan Akun Ditangguhkan: {nomor_normalized}"
    isi_email = f"""
Kepada Tim Dukungan WhatsApp,

Saya menulis surat ini untuk meminta peninjauan ulang segera atas akun WhatsApp saya yang ditangguhkan.

Saya telah menerima pemberitahuan bahwa akun saya diblokir/ditangguhkan, atau saya mengalami masalah saat mendaftar dengan pesan "Login Tidak Tersedia Untuk Saat Ini".

Nomor WhatsApp saya yang terkena dampak adalah: {nomor_normalized}.

Saya yakin penangguhan ini adalah sebuah kekeliruan, dan saya memohon agar akun saya segera diaktifkan kembali.

Saya telah membaca dan akan mematuhi semua Ketentuan Layanan WhatsApp. Saya mohon perhatian dan tindakan cepat Anda.

Hormat saya,
Pengguna Setia WhatsApp ({sender_email})
    """

    try:
        # Kirim Email via SMTP
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
➡️ *BANDING TERKIRIM (Web/Bot)*
Nomor: `{nomor_normalized}`
Pengirim: `{email_censored}`
Status: ✅ Berhasil terkirim ke WhatsApp Support.
"""
        kirim_notifikasi_telegram(telegram_kirim_msg, parse_mode='Markdown')
        
        # Tambahkan ke riwayat
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
    """Memeriksa balasan WhatsApp via IMAP dan mengirim notifikasi Telegram."""
    nomor_banding = item['nomor']
    imap_user = account_data['email']
    imap_pass = account_data['password']

    try:
        mail = imaplib.IMAP4_SSL('imap.gmail.com')
        mail.login(imap_user, imap_pass) 
        mail.select('inbox')

        # Mencari email yang belum dibaca dari WhatsApp support dan mengandung nomor banding
        search_criteria = f'(UNSEEN FROM "support@support.whatsapp.com" TEXT "{nomor_banding}")'
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
Nomor Banding: `{nomor_banding}`
Pengirim Banding (Akun Anda): `{email_pengirim_sensor}`
Subjek: {subject}

--- ISI BALASAN (Ringkasan) ---
`{body[:400].strip()}...`

[Klik untuk membuka web dashboard]({WEB_URL})
"""
        kirim_notifikasi_telegram(telegram_message, parse_mode='Markdown')
        
        # Tandai email sebagai sudah dibaca
        mail.store(latest_email_id, '+FLAGS', '\\Seen')
        mail.logout()
        
    except imaplib.IMAP4.error as e:
        add_log("ERROR", f"IMAP GAGAL koneksi/login untuk {sensor_email(imap_user)}. Cek App Password/IMAP ON. Error: {e}", console_only=True)
    except Exception as e:
        add_log("ERROR", f"Kesalahan saat cek IMAP: {e}")

def kirim_notifikasi_telegram(pesan, parse_mode='HTML'):
    """Mengirim pesan notifikasi ke Telegram."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        add_log("WARN", "Token/ID Telegram kosong. Notifikasi dilewati.", console_only=True)
        return False
        
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        'chat_id': TELEGRAM_CHAT_ID,
        'text': pesan,
        'parse_mode': parse_mode
    }
    
    try:
        requests.post(url, json=payload, timeout=5)
        return True
    except requests.exceptions.RequestException as e:
        add_log("WARN", f"Gagal koneksi Telegram: {e}", console_only=True)
        return False

# --- THREAD LATAR BELAKANG UNTUK IMAP CHECK ---

class BackgroundWorker(threading.Thread):
    def __init__(self):
        threading.Thread.__init__(self)
        self._stop_event = threading.Event()
        self.last_imap_check_time = 0
        self.name = "Background-Worker"

    def run(self):
        """Loop utama untuk cek IMAP otomatis."""
        global IS_BOT_RUNNING
        IS_BOT_RUNNING = True
        
        load_accounts()
        load_riwayat()
        
        add_log("INIT", "Cek IMAP & server API dimulai di latar belakang...")
        self.last_imap_check_time = int(time.time())

        while not self._stop_event.is_set():
            time.sleep(1)
            current_time = int(time.time())
            
            # --- Cek IMAP Otomatis untuk Balasan ---
            if SENDER_ACCOUNTS and len(RIWAYAT_PENGIRIMAN_GLOBAL) > 0 and (current_time - self.last_imap_check_time) >= IMAP_CHECK_INTERVAL_SECONDS:
                add_log("INFO", f"Memeriksa {len(RIWAYAT_PENGIRIMAN_GLOBAL)} riwayat pengiriman...")
                
                # Cek balasan hanya untuk 50 riwayat terbaru untuk efisiensi
                for item in list(RIWAYAT_PENGIRIMAN_GLOBAL)[:50]: 
                    try:
                        # Cari data akun yang digunakan untuk mengirim riwayat ini
                        account_data = next(acc for acc in SENDER_ACCOUNTS if acc['email'] == item['pengirim'])
                        check_and_notify_replies(item, account_data)
                    except StopIteration:
                        # Akun pengirim sudah dihapus, lewati
                        continue
                    except Exception as e:
                        add_log("ERROR", f"Gagal memproses cek IMAP: {e}", console_only=True)
                            
                self.last_imap_check_time = current_time
                add_log("INFO", f"Selesai. Cek IMAP berikutnya dalam {IMAP_CHECK_INTERVAL_SECONDS} detik.")

    def stop(self):
        self._stop_event.set()
        global IS_BOT_RUNNING
        IS_BOT_RUNNING = False

# --- LOGIKA BOT TELEGRAM ---

# Handler /start
@bot.message_handler(commands=['start'])
def send_welcome(message):
    add_log("BOT", f"Perintah /start dari {message.from_user.username}")
    keyboard = telebot.types.InlineKeyboardMarkup()
    keyboard.add(telebot.types.InlineKeyboardButton(text="Akses Web Dashboard 🌐", url=WEB_URL))
    
    bot.reply_to(message, 
                 f"Halo! Saya Bot Appeal WhatsApp Annas Fix Merah.\n\n"
                 f"Gunakan perintah berikut:\n"
                 f"• `/fix <nomor_wa>`: Kirim banding WA.\n"
                 f"• `/addbot <email> <password>`: Tambah akun pengirim (App Password).\n"
                 f"• `/status`: Cek status server.\n\n"
                 f"Atau, klik tombol di bawah untuk membuka web dashboard:", 
                 reply_markup=keyboard, parse_mode='Markdown')

# Handler /status
@bot.message_handler(commands=['status'])
def send_status(message):
    add_log("BOT", f"Perintah /status dari {message.from_user.username}")
    status_text = (
        f"🤖 STATUS SERVER & WEB 🌐\n"
        f"Server Python: {'✅ AKTIF' if IS_BOT_RUNNING else '❌ OFFLINE'}\n"
        f"Akun Pengirim: {len(SENDER_ACCOUNTS)} Akun\n"
        f"Banding Terkirim: {len(RIWAYAT_PENGIRIMAN_GLOBAL)} Riwayat\n"
        f"IMAP Check: Setiap {IMAP_CHECK_INTERVAL_SECONDS} detik\n"
        f"Web Dashboard: {WEB_URL}"
    )
    bot.reply_to(message, status_text, parse_mode='Markdown')

# Handler /fix <nomor>
@bot.message_handler(commands=['fix'])
def handle_fix_appeal(message):
    try:
        command_parts = message.text.split(maxsplit=1)
        if len(command_parts) < 2:
            bot.reply_to(message, "⚠️ Format salah. Gunakan: `/fix <nomor_wa>` (Contoh: `/fix +62812345678` atau `/fix 0812 345 678`)", parse_mode='Markdown')
            return

        nomor = command_parts[1].strip()
        add_log("BOT", f"Perintah /fix untuk nomor: {nomor} dari {message.from_user.username}")
        
        bot.reply_to(message, f"⏳ Memproses banding untuk nomor `{nomor}`...", parse_mode='Markdown')
        
        status, feedback = kirim_email_banding(nomor)
        
        if status:
            final_message = f"✅ BERHASIL! Banding untuk `{normalize_phone_number(nomor)}` telah dikirim.\n\n_{feedback}_"
        else:
            final_message = f"❌ GAGAL! Gagal mengirim banding.\n\n_{feedback}_"

        bot.send_message(message.chat.id, final_message, parse_mode='Markdown')

    except Exception as e:
        bot.reply_to(message, f"❌ Terjadi kesalahan saat memproses: {e}")
        add_log("ERROR", f"Kesalahan di /fix: {e}")

# Handler /addbot <email> <password>
@bot.message_handler(commands=['addbot'])
def handle_add_bot(message):
    try:
        command_parts = message.text.split()
        if len(command_parts) != 3:
            bot.reply_to(message, "⚠️ Format salah. Gunakan: `/addbot <email> <app_password>` (App Password 16 digit)", parse_mode='Markdown')
            return

        email = command_parts[1].strip()
        password = command_parts[2].strip()

        if '@' not in email or len(password) < 16:
            bot.reply_to(message, "⚠️ Format Email atau App Password (min 16 digit) salah. Cek kembali.", parse_mode='Markdown')
            return
            
        add_log("BOT", f"Perintah /addbot untuk email: {sensor_email(email)} dari {message.from_user.username}")
        bot.reply_to(message, f"⏳ Mencoba verifikasi akun `{sensor_email(email)}`...", parse_mode='Markdown')

        # Logika Verifikasi Akun (Sama seperti API)
        server = smtplib.SMTP_SSL('smtp.gmail.com', 465)
        server.ehlo()
        server.login(email, password)
        server.close()
        
        if any(acc['email'] == email for acc in SENDER_ACCOUNTS):
            bot.send_message(message.chat.id, f"⚠️ Akun `{sensor_email(email)}` sudah terdaftar sebelumnya.", parse_mode='Markdown')
            return

        SENDER_ACCOUNTS.append({'email': email, 'password': password})
        save_accounts()
        add_log("SUCCESS", f"Akun {sensor_email(email)} berhasil ditambahkan via Bot.")
        
        bot.send_message(message.chat.id, 
                         f"✅ BERHASIL! Akun `{sensor_email(email)}` berhasil ditambahkan dan teruji login.\n"
                         f"Total Akun Aktif: {len(SENDER_ACCOUNTS)}", 
                         parse_mode='Markdown')

    except smtplib.SMTPAuthenticationError:
        bot.reply_to(message, "❌ OTENTIKASI GAGAL. App Password salah atau IMAP/SMTP OFF di pengaturan Google Anda.", parse_mode='Markdown')
        add_log("ERROR", f"Autentikasi GAGAL via Bot untuk {sensor_email(email)}.")
    except Exception as e:
        bot.reply_to(message, f"❌ Terjadi kesalahan saat penambahan akun: {e}", parse_mode='Markdown')
        add_log("ERROR", f"Kesalahan di /addbot: {e}")


def telegram_bot_polling():
    """Fungsi untuk menjalankan polling Bot Telegram."""
    add_log("BOT_INIT", "Memulai polling Bot Telegram...")
    # Menggunakan loop tak terbatas untuk mencoba kembali jika terjadi kesalahan
    while IS_BOT_RUNNING:
        try:
            bot.polling(none_stop=True, interval=3) 
        except Exception as e:
            add_log("ERROR", f"Kesalahan Polling Bot Telegram: {e}", console_only=True)
            time.sleep(15) # Tunggu sebelum mencoba lagi

# --- FLASK APP DAN RUTES API (Web Dashboard) ---

app = Flask(__name__)
CORS(app) 

@app.route('/api/status', methods=['GET'])
def get_status():
    """Mengembalikan status bot, log, dan statistik untuk dashboard web."""
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
    """Menangani permintaan pengiriman banding dari web."""
    data = request.json
    number = data.get('number', '').strip()

    if not number:
        return jsonify({'message': 'Nomor WhatsApp tidak boleh kosong.'}), 400
    
    status, message = kirim_email_banding(number)
    
    # Kirim balasan ke konsol/bot tele
    add_log("WEB_REQ", f"Permintaan Banding Web untuk {normalize_phone_number(number)}: {'Berhasil' if status else 'Gagal'}")
    
    return jsonify({'message': message, 'status': 'success' if status else 'failed'}), 200

@app.route('/api/add_account', methods=['POST'])
def handle_add_account():
    """Menangani permintaan penambahan akun baru dari web."""
    global SENDER_ACCOUNTS
    data = request.json
    email = data.get('email', '').strip()
    password = data.get('password', '').strip()

    if not email or not password or '@' not in email or len(password) < 16:
        return jsonify({'message': 'Format Email atau App Password (minimal 16 digit) salah.'}), 400
    
    new_account = {'email': email, 'password': password}

    try:
        # Test SMTP login sebelum menambahkan
        server = smtplib.SMTP_SSL('smtp.gmail.com', 465)
        server.ehlo()
        server.login(email, password)
        server.close()
        
        if any(acc['email'] == email for acc in SENDER_ACCOUNTS):
            return jsonify({'message': f"Akun {sensor_email(email)} sudah terdaftar."}), 409

        SENDER_ACCOUNTS.append(new_account)
        save_accounts()
        add_log("WEB_REQ", f"Akun {sensor_email(email)} berhasil ditambahkan via Web.")
        
        return jsonify({
            'message': f"Akun {sensor_email(email)} berhasil ditambahkan dan teruji login!",
            'total_accounts': len(SENDER_ACCOUNTS)
        }), 200

    except smtplib.SMTPAuthenticationError:
        add_log("ERROR", f"Autentikasi GAGAL via Web untuk {sensor_email(email)}. Cek App Password/Pengaturan Google.")
        return jsonify({'message': 'Autentikasi GAGAL. Cek App Password/Pengaturan Google Anda.'}), 401
    except Exception as e:
        add_log("ERROR", f"Terjadi kesalahan saat verifikasi/penambahan via Web: {e}")
        return jsonify({'message': f'Terjadi kesalahan saat verifikasi/penambahan: {e}'}), 500

# --- MAIN EXECUTION ---
if __name__ == '__main__':
    # Memuat data awal
    load_accounts()
    load_riwayat()

    # Memulai Thread Background Worker (IMAP Check)
    worker_thread = BackgroundWorker()
    worker_thread.daemon = True 
    worker_thread.start()
    
    # Memulai Thread Telegram Bot Polling
    telegram_thread = threading.Thread(target=telegram_bot_polling)
    telegram_thread.daemon = True
    telegram_thread.start()
    
    add_log("INIT", f"Bot & Server Aktif! {len(SENDER_ACCOUNTS)} Akun siap. API Web di http://127.0.0.1:5000")
    
    try:
        # Menjalankan Flask App
        app.run(host='0.0.0.0', port=5000, debug=False) 
    except KeyboardInterrupt:
        worker_thread.stop()
        telegram_thread.join(timeout=1) # Tunggu sebentar
        worker_thread.join(timeout=1)
        add_log("FATAL", "Server dihentikan oleh pengguna.")
    except Exception as e:
        worker_thread.stop()
        telegram_thread.join(timeout=1)
        worker_thread.join(timeout=1)
        add_log("FATAL", f"Kesalahan fatal server: {e}")