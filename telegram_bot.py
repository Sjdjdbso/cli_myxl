import logging
import os
import time
from contextlib import redirect_stdout
from datetime import datetime
from io import StringIO

import requests
from dotenv import load_dotenv

load_dotenv()

from app.client.ciam import get_otp, submit_otp
from app.client.engsel import get_balance, send_api_request
from app.menus.util import format_quota_byte
from app.service.auth import AuthInstance


logger = logging.getLogger(__name__)
MAX_MESSAGE_LENGTH = 2000


class TelegramBot:
    def __init__(self, token: str, allowed_user_id: int):
        self.api_url = f"https://api.telegram.org/bot{token}"
        self.allowed_user_id = allowed_user_id
        self.offset = None
        self.login_state = None

    def call_api(self, method: str, **data):
        data = {key: value for key, value in data.items() if value is not None}
        response = requests.post(
            f"{self.api_url}/{method}",
            json=data,
            timeout=35,
        )
        response.raise_for_status()
        result = response.json()
        if not result.get("ok"):
            raise RuntimeError("Telegram API request failed")
        return result["result"]

    def send_message(self, chat_id: int, text: str):
        text = text or "No data available."
        for start in range(0, len(text), MAX_MESSAGE_LENGTH):
            self.call_api(
                "sendMessage",
                chat_id=chat_id,
                text=text[start:start + MAX_MESSAGE_LENGTH],
            )

    def delete_message(self, chat_id: int, message_id: int):
        try:
            self.call_api(
                "deleteMessage",
                chat_id=chat_id,
                message_id=message_id,
            )
        except (requests.RequestException, RuntimeError, ValueError):
            pass

    def poll(self):
        try:
            self.call_api("getMe")
        except (requests.RequestException, RuntimeError, ValueError):
            raise SystemExit("Unable to connect to Telegram. Check the bot token and network.") from None
        logger.info("Telegram bot started")
        while True:
            try:
                updates = self.call_api(
                    "getUpdates",
                    offset=self.offset,
                    timeout=30,
                    allowed_updates=["message"],
                )
            except (requests.RequestException, RuntimeError, ValueError):
                logger.warning("Telegram polling failed; retrying")
                time.sleep(3)
                continue

            for update in updates:
                self.offset = update["update_id"] + 1
                try:
                    self.handle_message(update.get("message", {}))
                except Exception:
                    logger.error("Failed to process a Telegram message")

    def handle_message(self, message: dict):
        sender = message.get("from", {})
        chat = message.get("chat", {})
        user_id = sender.get("id")
        chat_id = chat.get("id")

        if (
            user_id != self.allowed_user_id
            or chat_id != self.allowed_user_id
            or chat.get("type") != "private"
        ):
            return

        text = message.get("text", "").strip()
        if not text:
            return

        command, _, _ = text.partition(" ")
        command = command.split("@", 1)[0].lower()
        if command in ("/start", "/help"):
            self.login_state = None
            self.send_message(chat_id, self.help_text())
        elif command == "/cancel":
            self.login_state = None
            self.send_message(chat_id, "Login dibatalkan.")
        elif command == "/login":
            self.login_state = {"step": "phone", "attempts": 0}
            self.send_message(chat_id, "Masukkan nomor XL (contoh: 6281234567890).")
        elif command == "/balance":
            self.login_state = None
            self.send_balance(chat_id)
        elif command == "/packages":
            self.login_state = None
            self.send_packages(chat_id)
        elif self.login_state:
            self.handle_login(chat_id, message, text)
        else:
            self.send_message(chat_id, "Perintah tidak dikenal. Gunakan /help.")

    @staticmethod
    def help_text():
        return (
            "MyXL Telegram Bot\n"
            "/login — login dengan nomor XL dan OTP\n"
            "/balance — lihat pulsa dan masa aktif\n"
            "/packages — lihat paket aktif\n"
            "/cancel — batalkan proses login\n"
            "/help — tampilkan bantuan"
        )

    def handle_login(self, chat_id: int, message: dict, text: str):
        if self.login_state["step"] == "phone":
            if not text.startswith("628") or not text.isdigit() or not 10 <= len(text) <= 14:
                self.send_message(
                    chat_id,
                    "Nomor tidak valid. Masukkan nomor yang diawali 628, atau /cancel.",
                )
                return

            try:
                with redirect_stdout(StringIO()):
                    subscriber_id = get_otp(text)
                if not subscriber_id:
                    raise RuntimeError("OTP request failed")
            except Exception:
                self.login_state = None
                self.send_message(chat_id, "Gagal meminta OTP. Silakan coba lagi nanti.")
                return

            self.login_state = {
                "step": "otp",
                "phone": text,
                "attempts": 0,
            }
            self.send_message(chat_id, "OTP telah diminta. Kirim 6 digit OTP, atau /cancel.")
            return

        if not text.isdigit() or len(text) != 6:
            self.send_message(chat_id, "OTP harus terdiri dari 6 digit angka, atau /cancel.")
            return

        self.login_state["attempts"] += 1
        self.delete_message(chat_id, message["message_id"])
        try:
            with redirect_stdout(StringIO()):
                tokens = submit_otp(
                    AuthInstance.api_key,
                    "SMS",
                    self.login_state["phone"],
                    text,
                )
                if not tokens or not tokens.get("refresh_token"):
                    raise RuntimeError("OTP verification failed")
                AuthInstance.add_refresh_token(
                    int(self.login_state["phone"]),
                    tokens["refresh_token"],
                )
        except Exception:
            if self.login_state["attempts"] >= 5:
                self.login_state = None
                self.send_message(chat_id, "Login gagal setelah 5 percobaan. Silakan mulai lagi dengan /login.")
            else:
                self.send_message(
                    chat_id,
                    f"OTP salah atau login gagal. Sisa percobaan: {5 - self.login_state['attempts']}.",
                )
            return

        self.login_state = None
        self.send_message(chat_id, "Login berhasil. Gunakan /balance atau /packages.")

    @staticmethod
    def active_user():
        try:
            return AuthInstance.get_active_user()
        except Exception:
            return None

    def send_balance(self, chat_id: int):
        user = self.active_user()
        if not user:
            self.send_message(chat_id, "Belum ada akun aktif. Gunakan /login terlebih dahulu.")
            return

        try:
            balance = get_balance(AuthInstance.api_key, user["tokens"]["id_token"])
        except Exception:
            balance = None
        if not isinstance(balance, dict):
            self.send_message(chat_id, "Gagal mengambil informasi pulsa.")
            return

        expired_at = balance.get("expired_at")
        try:
            expired_text = datetime.fromtimestamp(float(expired_at)).strftime("%Y-%m-%d")
        except (TypeError, ValueError, OSError, OverflowError):
            expired_text = "Tidak diketahui"
        self.send_message(
            chat_id,
            f"Nomor: {user['number']}\n"
            f"Pulsa: Rp {balance.get('remaining', 'Tidak diketahui')}\n"
            f"Aktif sampai: {expired_text}",
        )

    def send_packages(self, chat_id: int):
        user = self.active_user()
        if not user:
            self.send_message(chat_id, "Belum ada akun aktif. Gunakan /login terlebih dahulu.")
            return

        try:
            response = send_api_request(
                AuthInstance.api_key,
                "api/v8/packages/quota-details",
                {
                    "is_enterprise": False,
                    "lang": "en",
                    "family_member_id": "",
                },
                user["tokens"]["id_token"],
                "POST",
            )
        except Exception:
            response = None

        if not isinstance(response, dict) or response.get("status") != "SUCCESS":
            self.send_message(chat_id, "Gagal mengambil daftar paket.")
            return

        data = response.get("data")
        quotas = data.get("quotas", []) if isinstance(data, dict) else []
        if not quotas:
            self.send_message(chat_id, "Tidak ada paket aktif.")
            return

        lines = ["Paket aktif:"]
        for index, quota in enumerate(quotas, start=1):
            if not isinstance(quota, dict):
                continue
            lines.append(f"\n{index}. {quota.get('name', 'Paket tanpa nama')}")
            lines.append(f"   Grup: {quota.get('group_name', 'Tidak diketahui')}")
            for benefit in quota.get("benefits") or []:
                if not isinstance(benefit, dict):
                    continue
                name = benefit.get("name", benefit.get("data_type", "Kuota"))
                data_type = benefit.get("data_type", "")
                remaining = benefit.get("remaining", 0)
                total = benefit.get("total", 0)
                if data_type == "DATA":
                    remaining = format_quota_byte(int(remaining or 0))
                    total = format_quota_byte(int(total or 0))
                elif data_type == "VOICE":
                    remaining = f"{float(remaining or 0) / 60:.2f} menit"
                    total = f"{float(total or 0) / 60:.2f} menit"
                elif data_type == "TEXT":
                    remaining = f"{remaining} SMS"
                    total = f"{total} SMS"
                lines.append(f"   {name}: {remaining} / {total}")
        self.send_message(chat_id, "\n".join(lines))


def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    allowed_user_id = os.getenv("TELEGRAM_ALLOWED_USER_ID", "").strip()
    if not token or not allowed_user_id:
        raise SystemExit(
            "Set TELEGRAM_BOT_TOKEN and TELEGRAM_ALLOWED_USER_ID in .env before starting the bot."
        )
    try:
        allowed_user_id = int(allowed_user_id)
    except ValueError as error:
        raise SystemExit("TELEGRAM_ALLOWED_USER_ID must be a Telegram numeric user ID.") from error

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    TelegramBot(token, allowed_user_id).poll()


if __name__ == "__main__":
    main()
