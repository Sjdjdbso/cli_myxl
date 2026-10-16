import json
import logging
import os
import time
from contextlib import redirect_stdout
from datetime import datetime
from io import BytesIO, StringIO

import qrcode
import requests
from dotenv import load_dotenv

load_dotenv()

from app.client.ciam import get_otp, submit_otp
from app.client.engsel import get_balance, get_family, get_package_details, send_api_request
from app.client.purchase.balance import settlement_balance
from app.client.purchase.ewallet import settlement_multipayment
from app.client.purchase.qris import get_qris_code, settlement_qris
from app.menus.util import format_quota_byte
from app.service.auth import AuthInstance
from app.service.bookmark import BookmarkInstance
from app.type_dict import PaymentItem


logger = logging.getLogger(__name__)
MAX_MESSAGE_LENGTH = 2000


class TelegramBot:
    def __init__(self, token: str, allowed_user_id: int):
        self.api_url = f"https://api.telegram.org/bot{token}"
        self.allowed_user_id = allowed_user_id
        self.offset = None
        self.login_state = None
        self.pending_purchase = None
        self.edit_target = None

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

    def send_message(self, chat_id: int, text: str, reply_markup=None):
        text = text or "No data available."
        for start in range(0, len(text), MAX_MESSAGE_LENGTH):
            chunk = text[start:start + MAX_MESSAGE_LENGTH]
            if self.edit_target and start == 0:
                self.call_api(
                    "editMessageText",
                    chat_id=chat_id,
                    message_id=self.edit_target["message_id"],
                    text=chunk,
                    reply_markup=self.back_menu(),
                )
            else:
                self.call_api(
                    "sendMessage",
                    chat_id=chat_id,
                    text=chunk,
                    reply_markup=reply_markup if start == 0 else None,
                )

    @staticmethod
    def feature_menu(show: bool = True):
        if not show:
            return {
                "inline_keyboard": [[
                    {"text": "Tampilkan menu fitur", "callback_data": "menu:show"}
                ]]
            }
        commands = [
            ("/login", "/login"),
            ("/balance", "/balance"),
            ("/packages", "/packages"),
            ("/family", "/family"),
            ("/bookmarks", "/bookmarks"),
            ("/bookmark", "/bookmark"),
            ("/unbookmark", "/unbookmark"),
            ("/buy", "/buy"),
            ("/buybookmark", "/buybookmark"),
            ("/confirm", "/confirm"),
            ("/accounts", "/accounts"),
            ("/switch", "/switch"),
            ("/help", "/help"),
            ("/cancel", "/cancel"),
        ]
        keyboard = [
            [
                {"text": label, "callback_data": f"feature:{command}"}
                for label, command in commands[index:index + 2]
            ]
            for index in range(0, len(commands), 2)
        ]
        keyboard.append([
            {"text": "Sembunyikan menu fitur", "callback_data": "menu:hide"}
        ])
        return {"inline_keyboard": keyboard}

    @staticmethod
    def back_menu():
        return {
            "inline_keyboard": [[
                {"text": "Kembali", "callback_data": "menu:home"}
            ]]
        }

    @staticmethod
    def home_text():
        user = None
        try:
            user = AuthInstance.get_active_user()
        except Exception:
            user = None
        if user:
            try:
                balance = get_balance(AuthInstance.api_key, user["tokens"]["id_token"])
            except Exception:
                balance = None
            if isinstance(balance, dict):
                try:
                    expired_text = datetime.fromtimestamp(float(balance.get("expired_at"))).strftime("%d %b %Y")
                except (TypeError, ValueError, OSError, OverflowError):
                    expired_text = "?"
                return (
                    f"MyXL Bot — {user['number']}\n"
                    f"Pulsa Rp{balance.get('remaining', '?')} · aktif s.d. {expired_text}"
                )
            return f"MyXL Bot — {user['number']}"
        return "MyXL Bot. Login dulu ya."

    @classmethod
    def home_keyboard(cls):
        return {
            "inline_keyboard": [
                [
                    {"text": "Pulsa", "callback_data": "view:balance"},
                    {"text": "Paket", "callback_data": "view:packages"},
                ],
                [
                    {"text": "Akun", "callback_data": "view:accounts"},
                    {"text": "Bookmark", "callback_data": "view:bookmarks"},
                ],
                [
                    {"text": "Riwayat", "callback_data": "view:history"},
                    {"text": "Masa aktif", "callback_data": "view:masaaktif"},
                ],
                [
                    {"text": "Beli paket", "callback_data": "help:buy"},
                    {"text": "Bantuan", "callback_data": "view:help"},
                ],
            ]
        }

    def show_feature_menu(self, chat_id: int):
        self.send_message(
            chat_id,
            "Pilih fitur. Untuk fitur yang memerlukan argumen, ikuti format pada petunjuk.",
            reply_markup=self.feature_menu(),
        )

    def handle_callback_query(self, query: dict):
        sender = query.get("from", {})
        message = query.get("message", {})
        chat = message.get("chat", {})
        if (
            sender.get("id") != self.allowed_user_id
            or chat.get("id") != self.allowed_user_id
            or chat.get("type") != "private"
        ):
            return

        callback_id = query.get("id")
        data = query.get("data", "")
        if data == "menu:show":
            self.call_api("answerCallbackQuery", callback_query_id=callback_id)
            self.call_api(
                "editMessageReplyMarkup",
                chat_id=chat["id"],
                message_id=message["message_id"],
                reply_markup=self.feature_menu(),
            )
            return
        if data == "menu:hide":
            self.call_api("answerCallbackQuery", callback_query_id=callback_id)
            self.call_api(
                "editMessageReplyMarkup",
                chat_id=chat["id"],
                message_id=message["message_id"],
                reply_markup=self.feature_menu(show=False),
            )
            return

        if data == "menu:return":
            self.call_api("answerCallbackQuery", callback_query_id=callback_id)
            self.call_api(
                "editMessageText",
                chat_id=chat["id"],
                message_id=message["message_id"],
                text="Pilih fitur. Untuk fitur yang memerlukan argumen, ikuti format pada petunjuk.",
                reply_markup=self.feature_menu(),
            )
            return

        if data == "menu:home":
            self.call_api("answerCallbackQuery", callback_query_id=callback_id)
            self.call_api(
                "editMessageText",
                chat_id=chat["id"],
                message_id=message["message_id"],
                text=self.home_text(),
                reply_markup=self.home_keyboard(),
            )
            return

        if data.startswith("view:"):
            self.call_api("answerCallbackQuery", callback_query_id=callback_id)
            view = data.split(":", 1)[1]
            text, markup = {
                "balance": lambda: (self.balance_text(), self.home_keyboard()),
                "packages": lambda: (self.packages_text(), self.back_menu()),
                "bookmarks": lambda: (self.bookmarks_text(), self.back_menu()),
                "help": lambda: (self.help_text(), self.back_menu()),
                "masaaktif": lambda: (self.masa_aktif_text(), self.back_menu()),
            }.get(view, lambda: (None, None))()
            if view == "history":
                self.call_api("editMessageText", chat_id=chat["id"], message_id=message["message_id"], text=self.history_text(), reply_markup=self.back_menu())
                return
            if view == "accounts":
                text, markup = self.accounts_screen()
            if text is None:
                return
            if text is None:
                return
            self.call_api(
                "editMessageText",
                chat_id=chat["id"],
                message_id=message["message_id"],
                text=text,
                reply_markup=markup,
            )
            return

        if data.startswith("help:"):
            self.call_api("answerCallbackQuery", callback_query_id=callback_id)
            topic = data.split(":", 1)[1]
            tips = {"buy": "Ketik /family <family_code> untuk lihat opsi, lalu /buy <family_code> <no>."}
            self.call_api(
                "editMessageText",
                chat_id=chat["id"],
                message_id=message["message_id"],
                text=tips.get(topic, self.help_text()),
                reply_markup=self.back_menu(),
            )
            return

        if data.startswith("acc:"):
            self.call_api("answerCallbackQuery", callback_query_id=callback_id)
            number = int(data.split(":", 1)[1])
            try:
                with redirect_stdout(StringIO()):
                    ok = AuthInstance.set_active_user(number)
            except EOFError:
                ok = False
            text = f"Aktif sekarang: {number}" if ok is not False else f"Gagal pindah ke {number}. Coba /login ulang."
            self.call_api(
                "editMessageText",
                chat_id=chat["id"],
                message_id=message["message_id"],
                text=text,
                reply_markup=self.back_menu(),
            )
            return

        command = data.removeprefix("feature:")
        if command not in {
            "/login", "/balance", "/packages", "/family", "/bookmarks",
            "/bookmark", "/unbookmark", "/buy", "/buybookmark", "/confirm",
            "/accounts", "/switch", "/help", "/cancel",
        }:
            self.call_api("answerCallbackQuery", callback_query_id=callback_id)
            return

        self.call_api("answerCallbackQuery", callback_query_id=callback_id)
        self.edit_target = {"message_id": message["message_id"]}
        try:
            self.handle_message({
                "from": sender,
                "chat": chat,
                "text": command,
            })
        finally:
            self.edit_target = None

    def send_photo(self, chat_id: int, image: BytesIO, caption: str):
        image.seek(0)
        response = requests.post(
            f"{self.api_url}/sendPhoto",
            data={"chat_id": chat_id, "caption": caption},
            files={"photo": ("qris.png", image, "image/png")},
            timeout=35,
        )
        response.raise_for_status()
        result = response.json()
        if not result.get("ok"):
            raise RuntimeError("Telegram photo request failed")

    def delete_message(self, chat_id: int, message_id: int):
        try:
            self.call_api(
                "deleteMessage",
                chat_id=chat_id,
                message_id=message_id,
            )
        except (requests.RequestException, RuntimeError, ValueError):
            pass

    def discard_pending_updates(self):
        """Skip messages sent while the bot was offline so they are never answered."""
        try:
            while True:
                updates = self.call_api(
                    "getUpdates",
                    offset=self.offset,
                    timeout=0,
                    allowed_updates=["message", "callback_query"],
                )
                if not updates:
                    return
                self.offset = updates[-1]["update_id"] + 1
        except (requests.RequestException, RuntimeError, ValueError):
            logger.warning("Failed to discard pending Telegram updates")

    def poll(self):
        try:
            self.call_api("getMe")
        except (requests.RequestException, RuntimeError, ValueError):
            raise SystemExit("Unable to connect to Telegram. Check the bot token and network.") from None
        self.discard_pending_updates()
        logger.info("Telegram bot started")
        while True:
            try:
                updates = self.call_api(
                    "getUpdates",
                    offset=self.offset,
                    timeout=30,
                    allowed_updates=["message", "callback_query"],
                )
            except (requests.RequestException, RuntimeError, ValueError):
                logger.warning("Telegram polling failed; retrying")
                time.sleep(3)
                continue

            for update in updates:
                self.offset = update["update_id"] + 1
                try:
                    if "callback_query" in update:
                        self.handle_callback_query(update["callback_query"])
                    else:
                        self.handle_message(update.get("message", {}))
                except Exception:
                    logger.exception("Failed to process a Telegram message")

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

        command, _, args = text.partition(" ")
        command = command.split("@", 1)[0].lower()
        args = args.split()
        if command not in ("/buy", "/buybookmark", "/confirm", "/menu"):
            self.pending_purchase = None
        if command == "/menu":
            self.login_state = None
            self.send_message(chat_id, self.home_text(), reply_markup=self.home_keyboard())
        elif command == "/family":
            self.login_state = None
            self.send_family(chat_id, args)
        elif command == "/bookmark":
            self.login_state = None
            self.add_bookmark(chat_id, args)
        elif command == "/bookmarks":
            self.login_state = None
            self.send_bookmarks(chat_id)
        elif command == "/unbookmark":
            self.login_state = None
            self.remove_bookmark(chat_id, args)
        elif command == "/buy":
            self.login_state = None
            self.request_buy(chat_id, args)
        elif command == "/buybookmark":
            self.login_state = None
            self.request_buy_bookmark(chat_id, args)
        elif command == "/confirm":
            self.login_state = None
            self.confirm_purchase(chat_id, args)
        elif command == "/start":
            self.login_state = None
            self.send_message(chat_id, self.home_text(), reply_markup=self.home_keyboard())
        elif command == "/help":
            self.login_state = None
            self.send_message(chat_id, self.help_text())
        elif command == "/cancel":
            self.login_state = None
            self.pending_purchase = None
            self.send_message(chat_id, "Proses dibatalkan.")
        elif command == "/login":
            self.login_state = {"step": "phone", "attempts": 0}
            self.send_message(chat_id, "Masukkan nomor XL (contoh: 6281234567890).")
        elif command == "/balance":
            self.login_state = None
            self.send_balance(chat_id)
        elif command == "/packages":
            self.login_state = None
            self.send_packages(chat_id)
        elif command == "/history":
            self.login_state = None
            self.send_history(chat_id)
        elif command == "/masaaktif":
            self.login_state = None
            self.send_masa_aktif(chat_id, args)
        elif command == "/accounts":
            self.login_state = None
            self.send_accounts(chat_id)
        elif command == "/switch":
            self.login_state = None
            self.switch_account(chat_id, args)
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
            "/family <family_code> — lihat opsi paket dalam family code\n"
            "/bookmark <family_code> <no> — simpan paket ke bookmark\n"
            "/bookmarks — lihat bookmark\n"
            "/unbookmark <no> — hapus bookmark\n"
            "/buy <family_code> <no> — pilih paket untuk dibeli\n"
            "/buybookmark <no> — pilih paket bookmark untuk dibeli\n"
            "/confirm [balance|qris|ewallet <dana|shopeepay|gopay|ovo> [nomor]] — pilih pembayaran\n"
            "/history — riwayat transaksi\n"
            "/masaaktif <no> — beli perpanjangan masa aktif (lihat opsi tanpa <no>)\n"
            "/accounts — lihat daftar nomor tersimpan\n"
            "/switch <nomor> — ganti akun aktif\n"
            "/menu — tampilkan menu fitur dengan tombol\n"
            "/cancel — batalkan proses login\n"
            "/help — tampilkan bantuan"
        )

    def balance_text(self):
        user = self.active_user()
        if not user:
            return "Belum ada akun aktif. Login dulu: /login"
        try:
            balance = get_balance(AuthInstance.api_key, user["tokens"]["id_token"])
        except Exception:
            balance = None
        if not isinstance(balance, dict):
            return "Gagal ambil data pulsa, coba lagi ntar."
        try:
            expired_text = datetime.fromtimestamp(float(balance.get("expired_at"))).strftime("%d %b %Y")
        except (TypeError, ValueError, OSError, OverflowError):
            expired_text = "?"
        return f"Pulsa lo Rp{balance.get('remaining', '?')} · aktif sampai {expired_text}"

    def packages_text(self):
        user = self.active_user()
        if not user:
            return "Belum ada akun aktif. Login dulu: /login"
        try:
            response = send_api_request(
                AuthInstance.api_key,
                "api/v8/packages/quota-details",
                {"is_enterprise": False, "lang": "en", "family_member_id": ""},
                user["tokens"]["id_token"],
                "POST",
            )
        except Exception:
            response = None
        if not isinstance(response, dict) or response.get("status") != "SUCCESS":
            return "Gagal ambil daftar paket."
        data = response.get("data")
        quotas = data.get("quotas", []) if isinstance(data, dict) else []
        if not quotas:
            return "Nggak ada paket aktif."
        lines = ["Paket aktif:"]
        for quota in quotas:
            if not isinstance(quota, dict):
                continue
            lines.append(f"\n{quota.get('name', 'Paket tanpa nama')}")
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
                    remaining = f"{float(remaining or 0) / 60:.0f} mnt"
                    total = f"{float(total or 0) / 60:.0f} mnt"
                elif data_type == "TEXT":
                    remaining = f"{remaining} SMS"
                    total = f"{total} SMS"
                lines.append(f"{name}: {remaining} / {total}")
        return "\n".join(lines)

    def accounts_text(self):
        tokens = AuthInstance.refresh_tokens
        if not tokens:
            return "Belum ada nomor tersimpan. Login dulu: /login"
        return tokens

    def accounts_screen(self):
        tokens = AuthInstance.refresh_tokens
        if not tokens:
            return "Belum ada nomor tersimpan. Login dulu: /login", self.back_menu()
        active = AuthInstance.active_user
        active_number = active["number"] if active else None
        rows = []
        for rt in tokens:
            label = f"{rt['number']}" + (" (aktif)" if rt["number"] == active_number else "")
            rows.append([{"text": label, "callback_data": f"acc:{rt['number']}"}])
        rows.append([{"text": "Kembali", "callback_data": "menu:home"}])
        return "Pilih nomor buat ganti akun:", {"inline_keyboard": rows}

    def bookmarks_text(self):
        try:
            bookmarks = BookmarkInstance.get_bookmarks()
        except Exception:
            bookmarks = []
        if not bookmarks:
            return "Belum ada bookmark."
        lines = ["Bookmark lo:"]
        for index, bm in enumerate(bookmarks, start=1):
            lines.append(f"{index}. {bm.get('family_name', '?')} — {bm.get('option_name', '?')}")
        return "\n".join(lines)

    def history_text(self):
        user = self.active_user()
        if not user:
            return "Belum ada akun aktif. Login dulu: /login"
        from app.client.engsel import get_transaction_history
        try:
            with redirect_stdout(StringIO()):
                data = get_transaction_history(AuthInstance.api_key, user["tokens"])
        except Exception:
            data = None
        history = data.get("list", []) if isinstance(data, dict) else []
        if not history:
            return "Belum ada transaksi."
        lines = ["Riwayat transaksi:"]
        for t in history[:15]:
            ts = t.get("timestamp", 0)
            try:
                when = datetime.fromtimestamp(int(ts)).strftime("%d %b %Y %H:%M")
            except (TypeError, ValueError, OSError, OverflowError):
                when = "?"
            lines.append(
                f"\n{t.get('title', '?')} — {t.get('price', '?')}\n"
                f"{when} · {t.get('payment_method_label', '?')} · {t.get('status', '?')}"
            )
        return "\n".join(lines)

    def masa_aktif_text(self):
        try:
            with open("hot_data/hot2.json", "r", encoding="utf-8") as f:
                hot = json.load(f)
        except Exception:
            hot = []
        if not hot:
            return "Opsi masa aktif tidak tersedia."
        lines = ["Opsi masa aktif:"]
        for index, h in enumerate(hot, start=1):
            lines.append(f"{index}. {h.get('name', '?')} — {h.get('price', '?')}")
        lines.append("\nBeli: /masaaktif <no>")
        return "\n".join(lines)

    def send_history(self, chat_id: int):
        self.send_message(chat_id, self.history_text())

    def send_masa_aktif(self, chat_id: int, args: list):
        user = self.active_user()
        if not user:
            self.send_message(chat_id, "Belum ada akun aktif. Login dulu: /login")
        else:
            try:
                with open("hot_data/hot2.json", "r", encoding="utf-8") as f:
                    hot = json.load(f)
            except Exception:
                hot = []
            if not (0 < len(args or []) <= 1) or not hot:
                lines = ["Opsi masa aktif:"]
                for index, h in enumerate(hot, start=1):
                    lines.append(f"{index}. {h.get('name', '?')} — {h.get('price', '?')}")
                lines.append("\nBeli: /masaaktif <no>")
                self.send_message(chat_id, "\n".join(lines))
                return
            idx = self.parse_index(args[0], len(hot))
            if idx is None:
                self.send_message(chat_id, f"Nomor harus 1-{len(hot)}.")
                return
            selected = hot[idx]
            packages = selected.get("packages", [])
            if not packages:
                self.send_message(chat_id, "Paket tidak tersedia.")
                return
            tokens = user["tokens"]
            items = []
            main_detail = None
            try:
                with redirect_stdout(StringIO()):
                    for package in packages:
                        detail = get_package_details(
                            AuthInstance.api_key,
                            tokens,
                            package["family_code"],
                            package["variant_code"],
                            package["order"],
                            package["is_enterprise"],
                            package["migration_type"],
                        )
                        if not detail:
                            raise RuntimeError(f"Gagal ambil detail {package['family_code']}")
                        if package is packages[0]:
                            main_detail = detail
                        opt = detail["package_option"]
                        items.append(
                            PaymentItem(
                                item_code=opt["package_option_code"],
                                product_type="",
                                item_price=opt["price"],
                                item_name=opt["name"],
                                tax=0,
                                token_confirmation=detail["token_confirmation"],
                            )
                        )
            except Exception as e:
                self.send_message(chat_id, f"Gagal menyiapkan pembelian: {e}")
                return
            self.pending_purchase = {
                "family_code": packages[0]["family_code"],
                "option": {
                    "name": selected.get("name", "Masa Aktif"),
                    "price": main_detail["package_option"]["price"],
                    "variant_code": packages[0]["variant_code"],
                    "order": packages[0]["order"],
                },
                "hot_items": items,
                "hot_meta": {
                    "payment_for": selected.get("payment_for", "BUY_PACKAGE"),
                    "ask_overwrite": selected.get("ask_overwrite", False),
                    "overwrite_amount": selected.get("overwrite_amount", -1),
                    "token_confirmation_idx": selected.get("token_confirmation_idx", 0),
                    "amount_idx": selected.get("amount_idx", -1),
                },
            }
            self.send_message(
                chat_id,
                f"Beli {selected.get('name')} ({selected.get('price')}). "
                "Pilih: /confirm balance atau /confirm qris. /cancel buat batal.",
            )

    def send_accounts(self, chat_id: int):
        text, markup = self.accounts_screen()
        self.send_message(chat_id, text, reply_markup=markup)

    def switch_account(self, chat_id: int, args: list):
        if not args or not args[0].isdigit():
            self.send_message(chat_id, "Format: /switch <nomor> (contoh: /switch 6281234567890)")
            return
        number = int(args[0])
        if not any(rt["number"] == number for rt in AuthInstance.refresh_tokens):
            self.send_message(chat_id, f"Nomor {number} tidak ada di daftar. Lihat /accounts.")
            return
        try:
            with redirect_stdout(StringIO()):
                ok = AuthInstance.set_active_user(number)
        except EOFError:
            ok = False
        if ok is False:
            self.send_message(chat_id, f"Gagal ganti ke {number}. Refresh token mungkin kedaluwarsa — login ulang dengan /login.")
            return
        self.send_message(chat_id, f"Akun aktif sekarang: {number}. Gunakan /balance atau /packages.")

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
        self.send_message(chat_id, self.balance_text())

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

    def load_family_options(self, user: dict, family_code: str):
        """Return (family_name, flat option list) or (None, None)."""
        try:
            with redirect_stdout(StringIO()):
                data = get_family(AuthInstance.api_key, user["tokens"], family_code)
        except Exception:
            data = None
        if not isinstance(data, dict):
            return None, None
        options = []
        for variant in data.get("package_variants", []):
            for option in variant.get("package_options", []):
                options.append(
                    {
                        "variant_name": variant.get("name", ""),
                        "variant_code": variant.get("package_variant_code", ""),
                        "order": option.get("order"),
                        "name": option.get("name", ""),
                        "price": option.get("price", 0),
                        "is_enterprise": bool(data["package_family"].get("is_enterprise", False)),
                    }
                )
        return data["package_family"].get("name", ""), options

    @staticmethod
    def parse_index(value: str, size: int):
        if not value.isdigit() or not 1 <= int(value) <= size:
            return None
        return int(value) - 1

    def send_family(self, chat_id: int, args: list):
        user = self.active_user()
        if not user:
            self.send_message(chat_id, "Belum ada akun aktif. Gunakan /login terlebih dahulu.")
            return
        if len(args) != 1:
            self.send_message(chat_id, "Format: /family <family_code>")
            return
        name, options = self.load_family_options(user, args[0])
        if not options:
            self.send_message(chat_id, "Family code tidak ditemukan atau tidak ada paket.")
            return
        lines = [f"{name} ({args[0]})"]
        for index, opt in enumerate(options, start=1):
            lines.append(f"{index}. [{opt['variant_name']}] {opt['name']} - Rp {opt['price']}")
        self.send_message(chat_id, "\n".join(lines))

    def add_bookmark(self, chat_id: int, args: list):
        user = self.active_user()
        if not user:
            self.send_message(chat_id, "Belum ada akun aktif. Gunakan /login terlebih dahulu.")
            return
        if len(args) != 2:
            self.send_message(chat_id, "Format: /bookmark <family_code> <no>. Lihat nomor dengan /family.")
            return
        name, options = self.load_family_options(user, args[0])
        idx = self.parse_index(args[1], len(options or []))
        if idx is None:
            self.send_message(chat_id, "Family code atau nomor paket tidak valid.")
            return
        opt = options[idx]
        with redirect_stdout(StringIO()):
            added = BookmarkInstance.add_bookmark(
                family_code=args[0],
                family_name=name,
                is_enterprise=opt["is_enterprise"],
                variant_name=opt["variant_name"],
                option_name=opt["name"],
                order=opt["order"],
            )
        self.send_message(
            chat_id,
            "Bookmark ditambahkan." if added else "Bookmark sudah ada.",
        )

    def send_bookmarks(self, chat_id: int):
        bookmarks = BookmarkInstance.get_bookmarks()
        if not bookmarks:
            self.send_message(chat_id, "Belum ada bookmark.")
            return
        lines = ["Bookmark:"]
        for index, b in enumerate(bookmarks, start=1):
            lines.append(
                f"{index}. {b['family_name']} - {b['variant_name']} - {b['option_name']}\n"
                f"   {b['family_code']} (opsi {b['order']})"
            )
        self.send_message(chat_id, "\n".join(lines))

    def remove_bookmark(self, chat_id: int, args: list):
        bookmarks = BookmarkInstance.get_bookmarks()
        idx = self.parse_index(args[0], len(bookmarks)) if len(args) == 1 else None
        if idx is None:
            self.send_message(chat_id, "Format: /unbookmark <no>. Lihat nomor dengan /bookmarks.")
            return
        b = bookmarks[idx]
        with redirect_stdout(StringIO()):
            BookmarkInstance.remove_bookmark(
                b["family_code"], b["is_enterprise"], b["variant_name"], b["order"]
            )
        self.send_message(chat_id, "Bookmark dihapus.")

    def request_buy(self, chat_id: int, args: list):
        user = self.active_user()
        if not user:
            self.send_message(chat_id, "Belum ada akun aktif. Gunakan /login terlebih dahulu.")
            return
        if len(args) != 2:
            self.send_message(chat_id, "Format: /buy <family_code> <no>. Lihat nomor dengan /family.")
            return
        _, options = self.load_family_options(user, args[0])
        idx = self.parse_index(args[1], len(options or []))
        if idx is None:
            self.send_message(chat_id, "Family code atau nomor paket tidak valid.")
            return
        self.set_pending(chat_id, args[0], options[idx])

    def request_buy_bookmark(self, chat_id: int, args: list):
        user = self.active_user()
        if not user:
            self.send_message(chat_id, "Belum ada akun aktif. Gunakan /login terlebih dahulu.")
            return
        bookmarks = BookmarkInstance.get_bookmarks()
        idx = self.parse_index(args[0], len(bookmarks)) if len(args) == 1 else None
        if idx is None:
            self.send_message(chat_id, "Format: /buybookmark <no>. Lihat nomor dengan /bookmarks.")
            return
        b = bookmarks[idx]
        _, options = self.load_family_options(user, b["family_code"])
        match = next(
            (
                o for o in options or []
                if o["order"] == b["order"] and o["variant_name"] == b["variant_name"]
            ),
            None,
        )
        if not match:
            self.send_message(chat_id, "Paket bookmark tidak ditemukan lagi.")
            return
        self.set_pending(chat_id, b["family_code"], match)

    def set_pending(self, chat_id: int, family_code: str, option: dict):
        self.pending_purchase = {"family_code": family_code, "option": option}
        self.send_message(
            chat_id,
            f"Beli {option['variant_name']} - {option['name']} (Rp {option['price']}) "
            "Pilih pembayaran dengan /confirm balance, /confirm qris, atau "
            "/confirm ewallet <dana|shopeepay|gopay|ovo> [nomor].\n"
            "Untuk DANA dan OVO, sertakan nomor ponsel. Gunakan /cancel untuk membatalkan.",
        )

    def confirm_purchase(self, chat_id: int, args: list = None):
        args = args or []
        payment_type = args[0].lower() if args else "balance"
        wallet_method = ""
        wallet_number = ""
        if payment_type == "ewallet":
            if len(args) not in (2, 3):
                self.send_message(
                    chat_id,
                    "Format: /confirm ewallet <dana|shopeepay|gopay|ovo> [nomor].",
                )
                return
            wallet_method = args[1].upper()
            if wallet_method not in ("DANA", "SHOPEEPAY", "GOPAY", "OVO"):
                self.send_message(chat_id, "Metode E-Wallet tidak valid.")
                return
            needs_number = wallet_method in ("DANA", "OVO")
            if needs_number != (len(args) == 3):
                self.send_message(
                    chat_id,
                    f"Nomor ponsel {'wajib' if needs_number else 'tidak diperlukan'} "
                    f"untuk {wallet_method}.",
                )
                return
            if needs_number:
                wallet_number = args[2]
                if (
                    not wallet_number.startswith("08")
                    or not wallet_number.isdigit()
                    or not 10 <= len(wallet_number) <= 13
                ):
                    self.send_message(chat_id, "Nomor ponsel harus diawali 08 dan terdiri dari 10-13 digit.")
                    return
        elif payment_type not in ("balance", "qris") or len(args) > 1:
            self.send_message(
                chat_id,
                "Pilih /confirm balance, /confirm qris, atau "
                "/confirm ewallet <dana|shopeepay|gopay|ovo> [nomor].",
            )
            return

        pending, self.pending_purchase = self.pending_purchase, None
        if not pending:
            self.send_message(chat_id, "Tidak ada pembelian yang menunggu konfirmasi.")
            return
        user = self.active_user()
        if not user:
            self.send_message(chat_id, "Belum ada akun aktif. Gunakan /login terlebih dahulu.")
            return
        option = pending["option"]
        tokens = user["tokens"]
        if pending.get("hot_items"):
            meta = pending.get("hot_meta", {})
            try:
                with redirect_stdout(StringIO()):
                    items = pending["hot_items"]
                    payment_for = meta.get("payment_for", "BUY_PACKAGE")
                    ow = meta.get("overwrite_amount", -1)
                    if payment_type == "balance":
                        res = settlement_balance(
                            AuthInstance.api_key,
                            tokens,
                            items,
                            payment_for,
                            meta.get("ask_overwrite", False),
                            overwrite_amount=ow,
                            token_confirmation_idx=meta.get("token_confirmation_idx", 0),
                            amount_idx=meta.get("amount_idx", -1),
                        )
                    elif payment_type == "ewallet":
                        res = settlement_multipayment(
                            AuthInstance.api_key,
                            tokens,
                            items,
                            wallet_number,
                            wallet_method,
                            payment_for,
                            meta.get("ask_overwrite", False),
                            overwrite_amount=ow,
                            token_confirmation_idx=meta.get("token_confirmation_idx", 0),
                            amount_idx=meta.get("amount_idx", -1),
                        )
                    else:
                        transaction_id = settlement_qris(
                            AuthInstance.api_key,
                            tokens,
                            items,
                            payment_for,
                            meta.get("ask_overwrite", False),
                            overwrite_amount=ow,
                            token_confirmation_idx=meta.get("token_confirmation_idx", 0),
                            amount_idx=meta.get("amount_idx", -1),
                        )
                        qris_code = (
                            get_qris_code(AuthInstance.api_key, tokens, transaction_id)
                            if transaction_id
                            else None
                        )
                        if qris_code:
                            qr = qrcode.QRCode(
                                error_correction=qrcode.constants.ERROR_CORRECT_M,
                                box_size=8,
                                border=4,
                            )
                            qr.add_data(qris_code)
                            qr.make(fit=True)
                            caption = f"QRIS pembayaran {option['name']}."
                            image = BytesIO()
                            from qrcode.image.pil import PilImage
                            qr.make_image(image_factory=PilImage, fill_color="black", back_color="white").save(image, format="PNG")
                            self.send_photo(chat_id, image, caption)
                            return
                        res = None
            except Exception:
                logging.exception("Hot purchase failed")
                res = None
            if isinstance(res, dict) and res.get("status") == "SUCCESS":
                if payment_type == "balance":
                    self.send_message(chat_id, f"Pembelian {option['name']} berhasil.")
                else:
                    deeplink = res.get("data", {}).get("deeplink", "")
                    message = f"Pembayaran {option['name']} dibuat."
                    if deeplink:
                        message += f"\nSelesaikan di sini:\n{deeplink}"
                    self.send_message(chat_id, message)
            else:
                msg = res.get("message", "") if isinstance(res, dict) else ""
                self.send_message(chat_id, f"Pembelian gagal. {msg}".strip())
            return
        try:
            with redirect_stdout(StringIO()):
                detail = get_package_details(
                    AuthInstance.api_key,
                    tokens,
                    pending["family_code"],
                    option["variant_code"],
                    option["order"],
                    None,
                    None,
                )
                opt = detail["package_option"]
                items = [
                    PaymentItem(
                        item_code=opt["package_option_code"],
                        product_type="",
                        item_price=opt["price"],
                        item_name=opt["name"],
                        tax=0,
                        token_confirmation=detail["token_confirmation"],
                    )
                ]
                payment_for = detail["package_family"].get("payment_for") or "BUY_PACKAGE"
                if payment_type == "balance":
                    res = settlement_balance(
                        AuthInstance.api_key,
                        tokens,
                        items,
                        payment_for,
                        False,
                        overwrite_amount=opt["price"],
                        token_confirmation_idx=0,
                    )
                elif payment_type == "ewallet":
                    res = settlement_multipayment(
                        AuthInstance.api_key,
                        tokens,
                        items,
                        wallet_number,
                        wallet_method,
                        payment_for,
                        False,
                        overwrite_amount=opt["price"],
                        token_confirmation_idx=0,
                    )
                else:
                    transaction_id = settlement_qris(
                        AuthInstance.api_key,
                        tokens,
                        items,
                        payment_for,
                        False,
                        overwrite_amount=opt["price"],
                        token_confirmation_idx=0,
                    )
                    qris_code = (
                        get_qris_code(AuthInstance.api_key, tokens, transaction_id)
                        if transaction_id
                        else None
                    )
                    if qris_code:
                        qr = qrcode.QRCode(
                            error_correction=qrcode.constants.ERROR_CORRECT_M,
                            box_size=8,
                            border=4,
                        )
                        qr.add_data(qris_code)
                        qr.make(fit=True)
                        caption = f"QRIS untuk pembayaran {option['name']} (Rp {opt['price']})."
                        try:
                            from qrcode.image.pil import PilImage

                            image = BytesIO()
                            qr.make_image(
                                image_factory=PilImage,
                                fill_color="black",
                                back_color="white",
                            ).save(image, format="PNG")
                        except ImportError:
                            logging.warning("Pillow not installed; sending QRIS as text")
                            self.send_message(
                                chat_id,
                                f"{caption}\nKode QRIS:\n{qris_code}\n"
                                "(Install Pillow: pip install pillow untuk menampilkan gambar QR.)",
                            )
                            return
                        self.send_photo(chat_id, image, caption)
                        return
                    res = None
        except Exception:
            logging.exception("Purchase failed")
            res = None
        if isinstance(res, dict) and res.get("status") == "SUCCESS":
            if payment_type == "balance":
                self.send_message(chat_id, f"Pembelian {option['name']} berhasil.")
            else:
                deeplink = res.get("data", {}).get("deeplink", "")
                message = f"Pembayaran {option['name']} dibuat melalui {wallet_method}."
                if deeplink:
                    message += f"\nSelesaikan pembayaran di sini:\n{deeplink}"
                self.send_message(chat_id, message)
        else:
            msg = res.get("message", "") if isinstance(res, dict) else ""
            self.send_message(chat_id, f"Pembelian gagal. {msg}".strip())
