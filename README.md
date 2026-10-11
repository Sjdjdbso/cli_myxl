# MYnyak Engsel Sunset

![Project banner](bnr.png)

An unofficial command-line client for managing MyXL accounts and packages. The interactive menu includes account login and switching, package browsing and purchases, transaction history, family plans, Circle, store listings, notifications, and bookmarks.

## Requirements

- Python 3.10 or newer
- pip
- Termux, Linux, or an OpenWrt system with `opkg` or `apk`

## Setup

Clone this repository and enter its directory:

```sh
git clone https://github.com/taamarin/cli.git
cd cli
```

Create a local environment file from the template:

```sh
cp .env.template .env
```

Fill in the configuration values required by your setup. The template contains empty placeholders; obtain configuration values only from a source you trust. Do not share or commit `.env`, API credentials, account tokens, or OTPs.

Install the dependencies:

```sh
sh setup.sh
```

`setup.sh` installs the packages in `requirements.txt`. On supported OpenWrt systems it also attempts to install Python 3 and pip when they are missing.

## Run

From the repository directory, start the application with:

```sh
python3 main.py
```

On first launch, choose the account menu and follow the prompts to request and enter an SMS OTP. Saved account tokens are stored locally in `refresh-tokens.json`; protect this file and do not share it.

## Telegram bot

The Telegram bot supports login, balance, package browsing, and package purchases using balance, E-Wallet, or QRIS. QRIS payments are sent as a scannable photo in Telegram. It uses the existing MyXL API client and stores account tokens in the same local `refresh-tokens.json` file as the CLI.

1. Create a bot with Telegram's BotFather and set `TELEGRAM_BOT_TOKEN` in `.env`.
2. Set `TELEGRAM_ALLOWED_USER_ID` to your numeric Telegram user ID. The bot only responds to that user in a private chat.
3. Start the bot from the repository directory:

   ```sh
   python3 telegram_bot.py
   ```

Use `/menu` to show the feature menu as Telegram buttons; select **Sembunyikan menu fitur** to hide it or **Tampilkan menu fitur** to show it again. You can also use `/login` to request and enter an SMS OTP, `/balance` to check your balance, and `/packages` to list active packages. Use `/family <family_code>` to list options, `/bookmark <family_code> <no>`, `/bookmarks`, and `/unbookmark <no>` to manage bookmarks. Start a purchase with `/buy <family_code> <no>` or `/buybookmark <no>`, then choose `/confirm balance`, `/confirm qris`, or `/confirm ewallet <dana|shopeepay|gopay|ovo> [nomor]`. DANA and OVO require a phone number; QRIS sends the payment code as a photo to scan. Keep the bot token and account-token file private. Do not run the bot and CLI simultaneously because they share account state.

## Termux

Install the prerequisites and run the setup:

```sh
pkg update && pkg upgrade -y
pkg install git python -y
git clone https://github.com/taamarin/cli.git
cd cli
cp .env.template .env
```

Fill in `.env`, then run:

```sh
sh setup.sh
python3 main.py
```

## OpenWrt

Connect to the router using SSH or its terminal, clone the repository, and enter its directory. Create and fill in `.env` as described above, then run:

```sh
sh setup.sh
python3 main.py
```

The setup script supports OpenWrt package managers `opkg` and `apk`. Run the application in a regular terminal or through `ttyd`.

## Safety and use

This is an unofficial community project. Use it only with accounts you are authorized to access, and follow applicable laws and service terms. Keep your configuration file and saved account tokens private.

## Contact

contact@mashu.lol
