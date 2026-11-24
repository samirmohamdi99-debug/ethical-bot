
import os
import sqlite3
import threading

from flask import Flask
from dotenv import load_dotenv

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = 8286503966
DB_FILE = "bot.db"
TRAINING_COST = 30

app_web = Flask(__name__)
ADMIN_STATE = {}


# =========================
# DATABASE
# =========================

def db():
    return sqlite3.connect(DB_FILE)


def init_db():
    con = db()
    cur = con.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            points INTEGER DEFAULT 0
        )
    """)

    cur.execute("PRAGMA table_info(custom_buttons)")
    cols = [row[1] for row in cur.fetchall()]

    if "content_type" in cols or "content" in cols:
        cur.execute("DROP TABLE custom_buttons")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS custom_buttons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            button_name TEXT UNIQUE NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS button_contents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            button_id INTEGER NOT NULL,
            content_type TEXT NOT NULL,
            content TEXT NOT NULL,
            FOREIGN KEY(button_id) REFERENCES custom_buttons(id)
        )
    """)

    con.commit()
    con.close()


def ensure_user(user_id):
    con = db()
    cur = con.cursor()

    cur.execute(
        "INSERT OR IGNORE INTO users(user_id, points) VALUES (?, 0)",
        (user_id,)
    )

    con.commit()
    con.close()


def get_points(user_id):
    ensure_user(user_id)

    con = db()
    cur = con.cursor()

    cur.execute(
        "SELECT points FROM users WHERE user_id=?",
        (user_id,)
    )

    row = cur.fetchone()
    con.close()

    return row[0] if row else 0


def add_points(user_id, amount):
    ensure_user(user_id)

    con = db()
    cur = con.cursor()

    cur.execute(
        "UPDATE users SET points = points + ? WHERE user_id=?",
        (amount, user_id)
    )

    con.commit()
    con.close()


def deduct_points(user_id, amount):
    ensure_user(user_id)

    con = db()
    cur = con.cursor()

    cur.execute(
        "UPDATE users SET points = points - ? "
        "WHERE user_id = ? AND points >= ?",
        (amount, user_id, amount)
    )

    success = cur.rowcount == 1

    con.commit()
    con.close()

    return success


# =========================
# CUSTOM BUTTONS
# =========================

def get_buttons():
    con = db()
    cur = con.cursor()

    cur.execute("""
        SELECT id, button_name
        FROM custom_buttons
        ORDER BY id ASC
    """)

    rows = cur.fetchall()
    con.close()

    return rows


def get_button_by_name(name):
    con = db()
    cur = con.cursor()

    cur.execute(
        "SELECT id, button_name FROM custom_buttons WHERE button_name=?",
        (name,)
    )

    row = cur.fetchone()
    con.close()

    return row


def get_button_contents(button_id):
    con = db()
    cur = con.cursor()

    cur.execute("""
        SELECT id, content_type, content
        FROM button_contents
        WHERE button_id=?
        ORDER BY id ASC
    """, (button_id,))

    rows = cur.fetchall()
    con.close()

    return rows


def create_button(name):
    con = db()
    cur = con.cursor()

    try:
        cur.execute(
            "INSERT INTO custom_buttons(button_name) VALUES (?)",
            (name,)
        )

        con.commit()
        button_id = cur.lastrowid

    except sqlite3.IntegrityError:
        button_id = None

    con.close()
    return button_id


def delete_button(button_id):
    con = db()
    cur = con.cursor()

    cur.execute(
        "DELETE FROM button_contents WHERE button_id=?",
        (button_id,)
    )

    cur.execute(
        "DELETE FROM custom_buttons WHERE id=?",
        (button_id,)
    )

    con.commit()
    con.close()


def delete_content(content_id):
    con = db()
    cur = con.cursor()

    cur.execute(
        "DELETE FROM button_contents WHERE id=?",
        (content_id,)
    )

    con.commit()
    con.close()


def save_content(button_id, content_type, content):
    con = db()
    cur = con.cursor()

    cur.execute("""
        INSERT INTO button_contents(button_id, content_type, content)
        VALUES (?, ?, ?)
    """, (button_id, content_type, content))

    con.commit()
    con.close()


# =========================
# KEYBOARDS
# =========================

def main_keyboard(user_id):
    rows = [
        ["💎 موجودی من", "💰 خرید موجودی"],
        ["📞 پشتیبانی", "📋 راهنما"],
    ]

    buttons = get_buttons()
    dynamic_row = []

    for _, name in buttons:
        dynamic_row.append(name)

        if len(dynamic_row) == 2:
            rows.append(dynamic_row)
            dynamic_row = []

    if dynamic_row:
        rows.append(dynamic_row)

    if user_id == ADMIN_ID:
        rows.append(["👑 پنل ادمین"])

    return ReplyKeyboardMarkup(
        rows,
        resize_keyboard=True
    )


def admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ ساخت دکمه", callback_data="create_btn")],
        [InlineKeyboardButton("📦 افزودن محتوا", callback_data="add_content")],
        [InlineKeyboardButton("🗑 حذف دکمه", callback_data="delete_btn")],
        [InlineKeyboardButton("🗑 حذف یک محتوا", callback_data="delete_content")],
        [InlineKeyboardButton("📋 لیست دکمه‌ها", callback_data="list_buttons")],
    ])


# =========================
# CONTENT EXTRACTOR
# =========================

def extract_content(update: Update):
    msg = update.message

    if msg.text:
        return "text", msg.text

    if msg.photo:
        return "photo", msg.photo[-1].file_id

    if msg.video:
        return "video", msg.video.file_id

    if msg.document:
        return "document", msg.document.file_id

    if msg.audio:
        return "audio", msg.audio.file_id

    if msg.voice:
        return "voice", msg.voice.file_id

    return None, None


# =========================
# SEND CONTENT
# =========================

async def send_content(update, context, content_type, content):
    chat_id = update.effective_chat.id

    if content_type == "text":
        await context.bot.send_message(
            chat_id=chat_id,
            text=content
        )

    elif content_type == "photo":
        await context.bot.send_photo(
            chat_id=chat_id,
            photo=content
        )

    elif content_type == "video":
        await context.bot.send_video(
            chat_id=chat_id,
            video=content
        )

    elif content_type == "document":
        await context.bot.send_document(
            chat_id=chat_id,
            document=content
        )

    elif content_type == "audio":
        await context.bot.send_audio(
            chat_id=chat_id,
            audio=content
        )

    elif content_type == "voice":
        await context.bot.send_voice(
            chat_id=chat_id,
            voice=content
        )

    else:
        raise ValueError("Unsupported content type")


async def send_button_contents(update, context, button_id):
    user_id = update.effective_user.id

    contents = get_button_contents(button_id)

    if not contents:
        await update.message.reply_text(
            "⚠️ هنوز محتوایی برای این دکمه ثبت نشده."
        )
        return

    # ادمین رایگان به آموزش‌ها دسترسی دارد.
    is_admin = user_id == ADMIN_ID

    if not is_admin:
        if not deduct_points(user_id, TRAINING_COST):
            points = get_points(user_id)

            await update.message.reply_text(
                "❌ موجودی شما کافی نیست!\n\n"
                f"💎 موجودی فعلی: {points} امتیاز\n"
                f"📚 هزینه آموزش: {TRAINING_COST} امتیاز\n\n"
                "برای دریافت امتیاز از بخش خرید موجودی اقدام کنید."
            )
            return

    sent_count = 0

    for _, content_type, content in contents:
        try:
            await send_content(
                update,
                context,
                content_type,
                content
            )
            sent_count += 1

        except Exception as e:
            print("CONTENT ERROR:", e)

    if sent_count == 0:
        if not is_admin:
            add_points(user_id, TRAINING_COST)

        await update.message.reply_text(
            "⚠️ ارسال آموزش ناموفق بود."
            + (
                "\nامتیاز شما برگشت داده شد."
                if not is_admin else ""
            )
        )
        return

    if is_admin:
        await update.message.reply_text(
            "✅ آموزش برای ادمین ارسال شد."
        )
    else:
        await update.message.reply_text(
            "✅ آموزش ارسال شد.\n"
            f"💎 {TRAINING_COST} امتیاز کم شد.\n"
            f"💰 موجودی باقی‌مانده: {get_points(user_id)} امتیاز"
        )


# =========================
# START
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    ensure_user(user_id)

    await update.message.reply_text(
        "🔥 به SAMIR ETHICAL BOT خوش آمدید\n\n"
        "از منوی زیر استفاده کنید.",
        reply_markup=main_keyboard(user_id)
    )


# =========================
# BALANCE
# =========================

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    points = get_points(update.effective_user.id)

    await update.message.reply_text(
        f"💎 موجودی شما: {points} امتیاز"
    )


# =========================
# GUIDE
# =========================

async def guide(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📋 راهنما\n\n"
        "از دکمه‌های منوی اصلی استفاده کنید.\n"
        "محتواهای آموزشی داخل دکمه‌های مربوطه قرار می‌گیرند.\n"
        f"💎 هزینه هر آموزش: {TRAINING_COST} امتیاز"
    )


# =========================
# SUPPORT
# =========================

async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "💬 ارتباط با پشتیبانی",
                url="https://t.me/samir_khass"
            )
        ]
    ])

    await update.message.reply_text(
        "📞 برای ارتباط با پشتیبانی روی دکمه زیر بزنید.",
        reply_markup=keyboard
    )


# =========================
# PURCHASE
# =========================

PACKAGES = {
    7: 20,
    12: 25,
    22: 40,
    30: 55,
    40: 70,
    50: 85,
    60: 95,
    70: 110,
    80: 120,
    90: 135,
    100: 145,
    120: 165,
}


async def buy_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = []

    for points, price in PACKAGES.items():
        keyboard.append([
            InlineKeyboardButton(
                f"💎 {points} امتیاز — {price} افغانی",
                callback_data=f"buy_{points}"
            )
        ])

    await update.message.reply_text(
        "💰 بسته مورد نظر را انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def purchase_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    points = int(query.data.replace("buy_", ""))
    price = PACKAGES[points]

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "💬 پیام به ادمین",
                url="https://t.me/samir_khass"
            )
        ]
    ])

    await query.message.reply_text(
        f"💎 بسته انتخابی: {points} امتیاز\n"
        f"💰 قیمت: {price} افغانی\n\n"
        "📌 برای پرداخت و دریافت امتیاز با ادمین در ارتباط باشید.\n\n"
        "👤 آیدی ادمین: @samir_khass\n"
        "🆔 ID: 8286503966",
        reply_markup=keyboard
    )


# =========================
# ADMIN PANEL
# =========================

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    await update.message.reply_text(
        "👑 پنل مدیریت\n\n"
        "یکی از گزینه‌ها را انتخاب کنید:",
        reply_markup=admin_keyboard()
    )


# =========================
# CREATE BUTTON
# =========================

async def create_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        return

    ADMIN_STATE[ADMIN_ID] = {
        "state": "button_name"
    }

    await query.message.reply_text(
        "✏️ اسم دکمه جدید را بفرست:"
    )


# =========================
# ADD CONTENT
# =========================

async def add_content_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        return

    buttons = get_buttons()

    if not buttons:
        await query.message.reply_text(
            "⚠️ هنوز هیچ دکمه‌ای ساخته نشده."
        )
        return

    keyboard = []

    for button_id, name in buttons:
        keyboard.append([
            InlineKeyboardButton(
                name,
                callback_data=f"addcontent_{button_id}"
            )
        ])

    await query.message.reply_text(
        "📦 دکمه‌ای که می‌خواهی محتوا به آن اضافه شود را انتخاب کن:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def choose_add_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        return

    button_id = int(query.data.replace("addcontent_", ""))

    ADMIN_STATE[ADMIN_ID] = {
        "state": "add_content",
        "button_id": button_id
    }

    await query.message.reply_text(
        "📤 حالا محتوا را بفرست.\n\n"
        "می‌تواند متن، عکس، ویدیو، فایل، APK، صدا یا Voice باشد."
    )


# =========================
# DELETE BUTTON
# =========================

async def delete_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        return

    buttons = get_buttons()

    if not buttons:
        await query.message.reply_text(
            "⚠️ دکمه‌ای وجود ندارد."
        )
        return

    keyboard = []

    for button_id, name in buttons:
        keyboard.append([
            InlineKeyboardButton(
                f"🗑 {name}",
                callback_data=f"delbtn_{button_id}"
            )
        ])

    await query.message.reply_text(
        "دکمه‌ای که می‌خواهی حذف شود را انتخاب کن:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def delete_button_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        return

    button_id = int(query.data.replace("delbtn_", ""))
    delete_button(button_id)

    await query.message.reply_text(
        "✅ دکمه و تمام محتواهای داخل آن حذف شد."
    )


# =========================
# DELETE ONE CONTENT
# =========================

async def delete_content_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        return

    buttons = get_buttons()

    if not buttons:
        await query.message.reply_text(
            "⚠️ دکمه‌ای وجود ندارد."
        )
        return

    keyboard = []

    for button_id, name in buttons:
        keyboard.append([
            InlineKeyboardButton(
                name,
                callback_data=f"delcontent_{button_id}"
            )
        ])

    await query.message.reply_text(
        "دکمه را انتخاب کن:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def show_contents_for_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        return

    button_id = int(query.data.replace("delcontent_", ""))
    contents = get_button_contents(button_id)

    if not contents:
        await query.message.reply_text(
            "⚠️ این دکمه محتوایی ندارد."
        )
        return

    keyboard = []

    for content_id, content_type, content in contents:
        if content_type == "text":
            title = content[:30]
        else:
            title = content_type

        keyboard.append([
            InlineKeyboardButton(
                f"🗑 {title}",
                callback_data=f"delone_{content_id}"
            )
        ])

    await query.message.reply_text(
        "محتوایی که می‌خواهی حذف شود را انتخاب کن:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def delete_one_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        return

    content_id = int(query.data.replace("delone_", ""))
    delete_content(content_id)

    await query.message.reply_text(
        "✅ محتوا حذف شد."
    )


# =========================
# LIST BUTTONS
# =========================

async def list_buttons_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        return

    buttons = get_buttons()

    if not buttons:
        await query.message.reply_text(
            "📭 هنوز هیچ دکمه‌ای ساخته نشده."
        )
        return

    text = "📋 لیست دکمه‌ها:\n\n"

    for button_id, name in buttons:
        contents = get_button_contents(button_id)

        text += (
            f"🔘 {name}\n"
            f"📦 تعداد محتوا: {len(contents)}\n\n"
        )

    await query.message.reply_text(text)


# =========================
# ADMIN STATE HANDLER
# =========================

async def handle_admin_state(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if user_id != ADMIN_ID:
        return False

    state_data = ADMIN_STATE.get(user_id)

    if not state_data:
        return False

    state = state_data["state"]

    if state == "button_name":
        if not update.message.text:
            await update.message.reply_text(
                "❌ لطفاً اسم دکمه را به‌صورت متن بفرست."
            )
            return True

        name = update.message.text.strip()

        if not name:
            await update.message.reply_text(
                "❌ اسم دکمه نمی‌تواند خالی باشد."
            )
            return True

        button_id = create_button(name)

        if not button_id:
            await update.message.reply_text(
                "❌ این اسم قبلاً استفاده شده."
            )
            return True

        ADMIN_STATE[user_id] = {
            "state": "button_content",
            "button_id": button_id
        }

        await update.message.reply_text(
            f"✅ دکمه «{name}» ساخته شد.\n\n"
            "📤 حالا اولین محتوا را بفرست.\n"
            "متن، عکس، ویدیو، فایل، APK، صدا یا Voice."
        )

        return True

    if state in ("button_content", "add_content"):
        content_type, content = extract_content(update)

        if not content_type:
            await update.message.reply_text(
                "❌ این نوع محتوا پشتیبانی نمی‌شود."
            )
            return True

        save_content(
            state_data["button_id"],
            content_type,
            content
        )

        ADMIN_STATE.pop(user_id, None)

        if state == "button_content":
            await update.message.reply_text(
                "✅ محتوا با موفقیت اضافه شد.\n\n"
                "📌 از پنل می‌توانی محتواهای بیشتری به همین دکمه اضافه کنی."
            )
        else:
            await update.message.reply_text(
                "✅ محتوا به دکمه اضافه شد."
            )

        return True

    return False


# =========================
# MESSAGE HANDLER
# =========================

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text

    if await handle_admin_state(update, context):
        return

    if text == "💎 موجودی من":
        await balance(update, context)
        return

    if text == "💰 خرید موجودی":
        await buy_points(update, context)
        return

    if text == "📞 پشتیبانی":
        await support(update, context)
        return

    if text == "📋 راهنما":
        await guide(update, context)
        return

    if text == "👑 پنل ادمین":
        if user_id == ADMIN_ID:
            await admin_panel(update, context)
        return

    if text is None:
        return

    button = get_button_by_name(text)

    if button:
        await send_button_contents(
            update,
            context,
            button[0]
        )


# =========================
# COMMANDS
# =========================

async def addpoints_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    if len(context.args) != 2:
        await update.message.reply_text(
            "فرمت:\n/addpoints USER_ID AMOUNT"
        )
        return

    try:
        user_id = int(context.args[0])
        amount = int(context.args[1])

    except ValueError:
        await update.message.reply_text(
            "❌ مقدار واردشده صحیح نیست."
        )
        return

    if amount <= 0:
        await update.message.reply_text(
            "❌ مقدار امتیاز باید بیشتر از صفر باشد."
        )
        return

    add_points(user_id, amount)

    await update.message.reply_text(
        f"✅ {amount} امتیاز به کاربر {user_id} اضافه شد."
    )


# =========================
# FLASK
# =========================

@app_web.route("/")
def home():
    return "SAMIR ETHICAL BOT is running."


@app_web.route("/health")
def health():
    return "OK"


def run_web():
    port = int(os.getenv("PORT", "10000"))

    app_web.run(
        host="0.0.0.0",
        port=port
    )


# =========================
# MAIN
# =========================

def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN داخل Environment پیدا نشد."
        )

    init_db()

    threading.Thread(
        target=run_web,
        daemon=True
    ).start()

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(
        CommandHandler("start", start)
    )

    application.add_handler(
        CommandHandler("balance", balance)
    )

    application.add_handler(
        CommandHandler("admin", admin_panel)
    )

    application.add_handler(
        CommandHandler("addpoints", addpoints_command)
    )

    application.add_handler(
        CallbackQueryHandler(
            purchase_callback,
            pattern=r"^buy_\d+$"
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            create_button_callback,
            pattern=r"^create_btn$"
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            add_content_callback,
            pattern=r"^add_content$"
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            choose_add_content,
            pattern=r"^addcontent_\d+$"
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            delete_button_callback,
            pattern=r"^delete_btn$"
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            delete_button_confirm,
            pattern=r"^delbtn_\d+$"
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            delete_content_callback,
            pattern=r"^delete_content$"
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            show_contents_for_delete,
            pattern=r"^delcontent_\d+$"
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            delete_one_content,
            pattern=r"^delone_\d+$"
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            list_buttons_callback,
            pattern=r"^list_buttons$"
        )
    )

    application.add_handler(
        MessageHandler(
            filters.ALL & ~filters.COMMAND,
            message_handler
        )
    )

    print("🔥 SAMIR ETHICAL BOT STARTED")

    application.run_polling()


if __name__ == "__main__":
    main()
