import os
import sqlite3
import logging
from datetime import datetime, timedelta

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# =========================================================
# ⚙️ SOZLAMALAR
# =========================================================

# XAVFSIZLIK: eski token chatga yuborilgan, BotFather'dan yangisini oling.
BOT_TOKEN = "8237971541:AAHOE6j0vnXxWtboDJrpG3TOei0_PQJYZ80"

# Sizning admin ID'ingiz o'zgarishsiz qoldi.
ADMIN_ID = 8423538916

# AI ishlatmoqchi bo'lsangiz:
# 1) pip install openai
# 2) OPENAI_API_KEY environment variable yoki pastga kalit yozish.
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
AI_MODEL = os.getenv("AI_MODEL", "gpt-5-mini")

DB_NAME = "kino_bot.db"

# Majburiy kanallarni admin paneldan qo'shish mumkin.
# Bot o'sha kanallarda admin bo'lishi kerak.

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# =========================================================
# 🗄 DATABASE
# =========================================================

def db():
    return sqlite3.connect(DB_NAME)


def column_exists(cursor, table, column):
    cursor.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cursor.fetchall())


def create_database():
    conn = db()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            premium INTEGER DEFAULT 0,
            joined_at TEXT
        )
    """)

    # Eski bazani buzmasdan yangi ustun qo'shamiz
    if not column_exists(c, "users", "premium_until"):
        c.execute("ALTER TABLE users ADD COLUMN premium_until TEXT")

    c.execute("""
        CREATE TABLE IF NOT EXISTS movies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            file_id TEXT NOT NULL,
            file_type TEXT NOT NULL,
            premium INTEGER DEFAULT 0,
            views INTEGER DEFAULT 0,
            created_at TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            url TEXT NOT NULL
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    c.execute("""
        INSERT OR IGNORE INTO settings(key, value)
        VALUES ('premium_price', 'Premium narxini admin belgilaydi')
    """)

    conn.commit()
    conn.close()


def save_user(user):
    conn = db()
    c = conn.cursor()
    c.execute("""
        INSERT OR IGNORE INTO users
        (user_id, username, first_name, premium, joined_at)
        VALUES (?, ?, ?, 0, ?)
    """, (
        user.id,
        user.username,
        user.first_name,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    ))
    c.execute("""
        UPDATE users SET username=?, first_name=? WHERE user_id=?
    """, (user.username, user.first_name, user.id))
    conn.commit()
    conn.close()


def get_user(user_id):
    conn = db()
    c = conn.cursor()
    c.execute("""
        SELECT user_id, username, first_name, premium, premium_until
        FROM users WHERE user_id=?
    """, (user_id,))
    row = c.fetchone()
    conn.close()
    return row


def is_premium(user_id):
    user = get_user(user_id)
    if not user or user[3] != 1:
        return False

    premium_until = user[4]
    if not premium_until:
        return True

    try:
        until = datetime.fromisoformat(premium_until)
        if datetime.now() <= until:
            return True
    except ValueError:
        return True

    set_premium(user_id, 0, None)
    return False


def set_premium(user_id, value, until=None):
    conn = db()
    c = conn.cursor()
    c.execute("""
        UPDATE users SET premium=?, premium_until=? WHERE user_id=?
    """, (value, until, user_id))
    conn.commit()
    conn.close()


def grant_premium(user_id, days=30):
    until = datetime.now() + timedelta(days=days)
    set_premium(user_id, 1, until.isoformat(timespec="seconds"))
    return until


def add_movie(code, title, file_id, file_type, premium):
    conn = db()
    c = conn.cursor()
    try:
        c.execute("""
            INSERT INTO movies
            (code, title, file_id, file_type, premium, views, created_at)
            VALUES (?, ?, ?, ?, ?, 0, ?)
        """, (
            code, title, file_id, file_type, premium,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ))
        conn.commit()
        ok = True
    except sqlite3.IntegrityError:
        ok = False
    conn.close()
    return ok


def get_movie(code):
    conn = db()
    c = conn.cursor()
    c.execute("""
        SELECT id, code, title, file_id, file_type, premium, views
        FROM movies WHERE code=?
    """, (code,))
    row = c.fetchone()
    conn.close()
    return row


def delete_movie(code):
    conn = db()
    c = conn.cursor()
    c.execute("DELETE FROM movies WHERE code=?", (code,))
    deleted = c.rowcount
    conn.commit()
    conn.close()
    return deleted > 0


def add_view(movie_id):
    conn = db()
    c = conn.cursor()
    c.execute("UPDATE movies SET views=views+1 WHERE id=?", (movie_id,))
    conn.commit()
    conn.close()


def get_movies(limit=30):
    conn = db()
    c = conn.cursor()
    c.execute("""
        SELECT code, title, premium, views
        FROM movies ORDER BY id DESC LIMIT ?
    """, (limit,))
    rows = c.fetchall()
    conn.close()
    return rows


def search_movies(query, limit=10):
    conn = db()
    c = conn.cursor()
    c.execute("""
        SELECT code, title, premium, views
        FROM movies
        WHERE LOWER(title) LIKE ?
        ORDER BY views DESC LIMIT ?
    """, (f"%{query.lower()}%", limit))
    rows = c.fetchall()
    conn.close()
    return rows


def statistics():
    conn = db()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM users")
    users = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM users WHERE premium=1")
    premium_users = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM movies")
    movies = c.fetchone()[0]
    c.execute("SELECT COALESCE(SUM(views),0) FROM movies")
    views = c.fetchone()[0]
    conn.close()
    return users, premium_users, movies, views


def get_all_user_ids():
    conn = db()
    c = conn.cursor()
    c.execute("SELECT user_id FROM users")
    rows = [x[0] for x in c.fetchall()]
    conn.close()
    return rows


def get_recent_users(limit=30):
    conn = db()
    c = conn.cursor()
    c.execute("""
        SELECT user_id, username, first_name, premium
        FROM users ORDER BY joined_at DESC LIMIT ?
    """, (limit,))
    rows = c.fetchall()
    conn.close()
    return rows


def add_channel(chat_id, name, url):
    conn = db()
    c = conn.cursor()
    try:
        c.execute(
            "INSERT INTO channels(chat_id,name,url) VALUES(?,?,?)",
            (chat_id, name, url)
        )
        conn.commit()
        ok = True
    except sqlite3.IntegrityError:
        ok = False
    conn.close()
    return ok


def delete_channel(chat_id):
    conn = db()
    c = conn.cursor()
    c.execute("DELETE FROM channels WHERE chat_id=?", (chat_id,))
    deleted = c.rowcount
    conn.commit()
    conn.close()
    return deleted > 0


def get_channels():
    conn = db()
    c = conn.cursor()
    c.execute("SELECT chat_id,name,url FROM channels ORDER BY id")
    rows = c.fetchall()
    conn.close()
    return rows


def get_setting(key, default=""):
    conn = db()
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else default


def set_setting(key, value):
    conn = db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO settings(key,value) VALUES(?,?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
    """, (key, value))
    conn.commit()
    conn.close()


# =========================================================
# 📢 MAJBURIY OBUNA
# =========================================================

async def check_subscription(context, user_id):
    if user_id == ADMIN_ID:
        return True

    channels = get_channels()
    if not channels:
        return True

    for chat_id, _, _ in channels:
        try:
            member = await context.bot.get_chat_member(chat_id=chat_id, user_id=user_id)
            if member.status not in ("member", "administrator", "creator"):
                return False
        except Exception as e:
            logger.error("Kanal tekshirish xatosi: %s", e)
            return False
    return True


def subscription_keyboard():
    buttons = []
    for _, name, url in get_channels():
        buttons.append([InlineKeyboardButton(f"📢 {name}", url=url)])
    buttons.append([InlineKeyboardButton("✅ Tekshirish", callback_data="check_subscription")])
    return InlineKeyboardMarkup(buttons)


async def require_subscription(update, context):
    if await check_subscription(context, update.effective_user.id):
        return True

    text = (
        "🔒 Botdan foydalanish uchun kanallarga obuna bo‘ling.\n\n"
        "Obuna bo‘lgach «✅ Tekshirish» tugmasini bosing."
    )
    await update.effective_message.reply_text(text, reply_markup=subscription_keyboard())
    return False


# =========================================================
# 🏠 USER MENYU
# =========================================================

def main_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("🔎 Kino qidirish"), KeyboardButton("💎 Premium")],
        [KeyboardButton("🤖 AI yordamchi"), KeyboardButton("🎬 Kinolar")],
        [KeyboardButton("ℹ️ Yordam")],
    ], resize_keyboard=True)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    save_user(user)

    if not await require_subscription(update, context):
        return

    premium_text = "💎 Premium faol" if is_premium(user.id) else "🆓 Oddiy foydalanuvchi"
    await update.message.reply_text(
        f"👋 Assalomu alaykum, {user.first_name}!\n\n"
        "🎬 F1RST MOVIE botga xush kelibsiz!\n"
        "🔢 Kino kodini yuboring — kino chiqadi.\n"
        "🔎 Nomi bo‘yicha ham qidirishingiz mumkin.\n"
        "🤖 AI kino tanlashda yordam beradi.\n\n"
        f"{premium_text}\n\n🍿 Yoqimli tomosha!",
        reply_markup=main_keyboard(),
    )


async def check_subscription_callback(update, context):
    q = update.callback_query
    await q.answer()
    if await check_subscription(context, q.from_user.id):
        await q.message.reply_text(
            "✅ Obuna tasdiqlandi! Endi botdan foydalanishingiz mumkin.",
            reply_markup=main_keyboard()
        )
    else:
        await q.answer("❌ Hali barcha kanallarga obuna bo‘lmagansiz.", show_alert=True)


# =========================================================
# 🎬 KINO
# =========================================================

async def send_movie(update, context, code):
    if not await require_subscription(update, context):
        return

    movie = get_movie(code)
    if not movie:
        await update.effective_message.reply_text("❌ Bunday kodli kino topilmadi.")
        return

    movie_id, movie_code, title, file_id, file_type, premium, _ = movie

    if premium == 1 and not is_premium(update.effective_user.id):
        await update.effective_message.reply_text(
            f"💎 {title}\n\nBu Premium kino.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💎 Premium olish", callback_data="premium_info")],
                [InlineKeyboardButton("👨‍💻 Admin", url=f"tg://user?id={ADMIN_ID}")],
            ])
        )
        return

    caption = f"🎬 {title}\n🔢 Kod: {movie_code}\n\n🍿 Yoqimli tomosha!"

    try:
        if file_type == "video":
            await update.effective_message.reply_video(video=file_id, caption=caption)
        elif file_type == "document":
            await update.effective_message.reply_document(document=file_id, caption=caption)
        elif file_type == "animation":
            await update.effective_message.reply_animation(animation=file_id, caption=caption)
        else:
            await update.effective_message.reply_text("❌ Fayl turi noto‘g‘ri.")
            return
        add_view(movie_id)
    except Exception:
        logger.exception("Kino yuborish xatosi")
        await update.effective_message.reply_text("⚠️ Kinoni yuborishda xatolik yuz berdi.")


# =========================================================
# 👑 ADMIN PANEL
# =========================================================

def admin_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➕ Kino qo‘shish", callback_data="admin_add_movie"),
            InlineKeyboardButton("🗑 Kino o‘chirish", callback_data="admin_delete_movie"),
        ],
        [
            InlineKeyboardButton("🎬 Kinolar", callback_data="admin_movies"),
            InlineKeyboardButton("📊 Statistika", callback_data="admin_stats"),
        ],
        [
            InlineKeyboardButton("💎 Premium berish", callback_data="admin_premium_add"),
            InlineKeyboardButton("❌ Premium olish", callback_data="admin_premium_remove"),
        ],
        [
            InlineKeyboardButton("👥 Userlar", callback_data="admin_users"),
            InlineKeyboardButton("📣 Xabar yuborish", callback_data="admin_broadcast"),
        ],
        [
            InlineKeyboardButton("📢 Kanallar", callback_data="admin_channels"),
            InlineKeyboardButton("⚙️ Sozlamalar", callback_data="admin_settings"),
        ],
        [
            InlineKeyboardButton("🤖 AI holati", callback_data="admin_ai"),
        ],
    ])


async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_text(
        "👑 ADMIN PANEL\n\nKerakli bo‘limni tanlang:",
        reply_markup=admin_keyboard()
    )


async def admin_buttons(update, context):
    q = update.callback_query
    await q.answer()

    if q.from_user.id != ADMIN_ID:
        return

    data = q.data

    if data == "admin_stats":
        users, premium_users, movies, views = statistics()
        await q.message.reply_text(
            f"📊 STATISTIKA\n\n"
            f"👥 Userlar: {users}\n"
            f"💎 Premium: {premium_users}\n"
            f"🎬 Kinolar: {movies}\n"
            f"👁 Ko‘rishlar: {views}"
        )

    elif data == "admin_delete_movie":
        await q.message.reply_text("🗑 Kino o‘chirish:\n/delete KOD\n\nMasalan: /delete 10")

    elif data == "admin_movies":
        rows = get_movies(30)
        if not rows:
            await q.message.reply_text("🎬 Hali kino yo‘q.")
            return
        text = "🎬 OXIRGI KINOLAR\n\n"
        for code, title, premium, views in rows:
            text += f"{'💎' if premium else '🆓'} {code} — {title} | 👁 {views}\n"
        await q.message.reply_text(text[:4000])

    elif data == "admin_premium_add":
        await q.message.reply_text(
            "💎 30 kun Premium berish:\n/premium USER_ID\n\n"
            "Masalan: /premium 123456789"
        )

    elif data == "admin_premium_remove":
        await q.message.reply_text(
            "❌ Premiumni olib tashlash:\n/unpremium USER_ID"
        )

    elif data == "admin_users":
        rows = get_recent_users(30)
        if not rows:
            await q.message.reply_text("👥 Userlar yo‘q.")
            return
        text = "👥 OXIRGI USERLAR\n\n"
        for uid, username, first_name, premium in rows:
            uname = f"@{username}" if username else "-"
            text += f"{'💎' if premium else '🆓'} {uid} | {first_name or '-'} | {uname}\n"
        await q.message.reply_text(text[:4000])

    elif data == "admin_broadcast":
        await q.message.reply_text(
            "📣 Barcha userlarga xabar:\n\n/broadcast XABAR\n\n"
            "Masalan:\n/broadcast Bugun yangi kinolar qo‘shildi! 🎬"
        )

    elif data == "admin_channels":
        channels = get_channels()
        text = "📢 MAJBURIY KANALLAR\n\n"
        if channels:
            for chat_id, name, url in channels:
                text += f"• {name} — {chat_id}\n"
        else:
            text += "Hozir kanal yo‘q.\n"

        text += (
            "\n➕ Qo‘shish:\n"
            "/addchannel @username | Kanal nomi | https://t.me/username\n\n"
            "🗑 O‘chirish:\n"
            "/delchannel @username"
        )
        await q.message.reply_text(text)

    elif data == "admin_settings":
        price = get_setting("premium_price")
        await q.message.reply_text(
            f"⚙️ SOZLAMALAR\n\n"
            f"💎 Premium narxi:\n{price}\n\n"
            "Narxni o‘zgartirish:\n/setprice YANGI_NARX\n\n"
            "Masalan:\n/setprice 1 oy — 25 000 so‘m"
        )

    elif data == "admin_ai":
        status = "✅ AI kaliti mavjud" if OPENAI_API_KEY else "❌ OPENAI_API_KEY qo‘yilmagan"
        await q.message.reply_text(
            f"🤖 AI HOLATI\n\n{status}\n"
            f"Model: {AI_MODEL}\n\n"
            "AI userga kino tavsiya qilish va botdagi kinolarni topishda yordam beradi."
        )


# =========================================================
# ➕ KINO QO‘SHISH
# =========================================================

ADD_VIDEO, ADD_TITLE, ADD_CODE, ADD_TYPE = range(4)


async def add_movie_start(update, context):
    q = update.callback_query
    await q.answer()
    if q.from_user.id != ADMIN_ID:
        return ConversationHandler.END

    context.user_data["new_movie"] = {}
    await q.message.reply_text(
        "🎬 Yangi kino qo‘shish\n\n"
        "1️⃣ Kino videosini/faylini yuboring.\n\n"
        "/cancel — bekor qilish"
    )
    return ADD_VIDEO


async def add_movie_video(update, context):
    message = update.message
    if message.video:
        file_id, file_type = message.video.file_id, "video"
    elif message.document:
        file_id, file_type = message.document.file_id, "document"
    elif message.animation:
        file_id, file_type = message.animation.file_id, "animation"
    else:
        await message.reply_text("❌ Video yoki kino faylini yuboring.")
        return ADD_VIDEO

    context.user_data["new_movie"]["file_id"] = file_id
    context.user_data["new_movie"]["file_type"] = file_type
    await message.reply_text("✅ Qabul qilindi.\n\n2️⃣ Kino nomini yozing:")
    return ADD_TITLE


async def add_movie_title(update, context):
    title = update.message.text.strip()
    if not title:
        return ADD_TITLE
    context.user_data["new_movie"]["title"] = title
    await update.message.reply_text(f"🎬 {title}\n\n3️⃣ Kino kodini yozing:")
    return ADD_CODE


async def add_movie_code(update, context):
    code = update.message.text.strip()
    if not code:
        return ADD_CODE
    if get_movie(code):
        await update.message.reply_text("❌ Bu kod band. Boshqa kod yozing.")
        return ADD_CODE

    context.user_data["new_movie"]["code"] = code
    await update.message.reply_text(
        "4️⃣ Kino turini tanlang:",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🆓 Oddiy", callback_data="movie_normal"),
            InlineKeyboardButton("💎 Premium", callback_data="movie_premium"),
        ]])
    )
    return ADD_TYPE


async def add_movie_type(update, context):
    q = update.callback_query
    await q.answer()

    movie = context.user_data.get("new_movie")
    if not movie:
        await q.message.reply_text("❌ Kino ma’lumotlari topilmadi.")
        return ConversationHandler.END

    premium = 1 if q.data == "movie_premium" else 0
    ok = add_movie(
        movie["code"], movie["title"], movie["file_id"],
        movie["file_type"], premium
    )

    if ok:
        await q.message.reply_text(
            f"✅ KINO SAQLANDI!\n\n"
            f"🎬 {movie['title']}\n"
            f"🔢 Kod: {movie['code']}\n"
            f"📁 {'💎 Premium' if premium else '🆓 Oddiy'}"
        )
    else:
        await q.message.reply_text("❌ Kino saqlanmadi. Kod mavjud bo‘lishi mumkin.")

    context.user_data.pop("new_movie", None)
    return ConversationHandler.END


async def cancel_add(update, context):
    context.user_data.pop("new_movie", None)
    await update.message.reply_text("❌ Kino qo‘shish bekor qilindi.")
    return ConversationHandler.END


# =========================================================
# 🧰 ADMIN COMMANDLAR
# =========================================================

async def delete_movie_command(update, context):
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args:
        await update.message.reply_text("Foydalanish: /delete KOD")
        return
    code = context.args[0]
    await update.message.reply_text(
        f"🗑 {code} o‘chirildi." if delete_movie(code)
        else "❌ Bunday kod topilmadi."
    )


async def premium_command(update, context):
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args:
        await update.message.reply_text("Foydalanish: /premium USER_ID")
        return
    try:
        user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ USER_ID raqam bo‘lishi kerak.")
        return

    if not get_user(user_id):
        await update.message.reply_text("❌ Bu user hali botga /start bosmagan.")
        return

    until = grant_premium(user_id, 30)
    await update.message.reply_text(
        f"💎 {user_id} ga 30 kun Premium berildi.\n"
        f"📅 Tugaydi: {until.strftime('%Y-%m-%d %H:%M')}"
    )
    try:
        await context.bot.send_message(
            user_id,
            "🎉 Sizga 30 kunlik Premium faollashtirildi! 💎"
        )
    except Exception:
        pass


async def unpremium_command(update, context):
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args:
        await update.message.reply_text("Foydalanish: /unpremium USER_ID")
        return
    try:
        user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ USER_ID raqam bo‘lishi kerak.")
        return

    set_premium(user_id, 0, None)
    await update.message.reply_text(f"✅ {user_id} Premiumdan chiqarildi.")


async def broadcast_command(update, context):
    if update.effective_user.id != ADMIN_ID:
        return
    text = " ".join(context.args).strip()
    if not text:
        await update.message.reply_text("Foydalanish: /broadcast XABAR")
        return

    sent, failed = 0, 0
    status = await update.message.reply_text("📣 Xabar yuborilmoqda...")

    for user_id in get_all_user_ids():
        try:
            await context.bot.send_message(user_id, text)
            sent += 1
        except Exception:
            failed += 1

    await status.edit_text(
        f"✅ Broadcast tugadi.\n\n"
        f"📨 Yuborildi: {sent}\n"
        f"❌ Yetmadi: {failed}"
    )


async def addchannel_command(update, context):
    if update.effective_user.id != ADMIN_ID:
        return

    raw = update.message.text.replace("/addchannel", "", 1).strip()
    parts = [x.strip() for x in raw.split("|")]

    if len(parts) != 3:
        await update.message.reply_text(
            "Foydalanish:\n"
            "/addchannel @username | Kanal nomi | https://t.me/username"
        )
        return

    chat_id, name, url = parts
    if add_channel(chat_id, name, url):
        await update.message.reply_text("✅ Kanal qo‘shildi.")
    else:
        await update.message.reply_text("❌ Bu kanal oldin qo‘shilgan.")


async def delchannel_command(update, context):
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args:
        await update.message.reply_text("Foydalanish: /delchannel @username")
        return
    await update.message.reply_text(
        "✅ Kanal o‘chirildi." if delete_channel(context.args[0])
        else "❌ Kanal topilmadi."
    )


async def setprice_command(update, context):
    if update.effective_user.id != ADMIN_ID:
        return
    price = " ".join(context.args).strip()
    if not price:
        await update.message.reply_text("Foydalanish: /setprice 1 oy — 25 000 so‘m")
        return
    set_setting("premium_price", price)
    await update.message.reply_text(f"✅ Premium narxi yangilandi:\n{price}")


# =========================================================
# 🤖 AI
# =========================================================

async def ask_ai(user_id, question):
    if not OPENAI_API_KEY:
        return (
            "🤖 AI hali serverda ulanmagan.\n\n"
            "Admin OPENAI_API_KEY ni qo‘ygandan keyin ishlaydi."
        )

    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=OPENAI_API_KEY)

        movies = get_movies(50)
        catalog = "\n".join(
            f"Kod {code}: {title} ({'Premium' if premium else 'Oddiy'})"
            for code, title, premium, _ in movies
        ) or "Bot katalogida hozircha kino yo‘q."

        premium = is_premium(user_id)
        instruction = (
            "Sen F1RST MOVIE Telegram botining kino yordamchisisan. "
            "Foydalanuvchiga qisqa, do‘stona, o‘zbekcha javob ber. "
            "Kino tanlashda yordam ber. Bot katalogida mavjud kino bo‘lsa "
            "uning kodini aniq ayt. Katalogda yo‘q kino uchun mavjuddek kod o‘ylab topma. "
            f"User turi: {'Premium' if premium else 'Oddiy'}.\n\n"
            f"BOT KATALOGI:\n{catalog}"
        )

        response = await client.responses.create(
            model=AI_MODEL,
            instructions=instruction,
            input=question,
        )
        return response.output_text.strip()

    except Exception as e:
        logger.exception("AI xatosi: %s", e)
        return "⚠️ AI bilan bog‘lanishda xatolik bo‘ldi. Keyinroq urinib ko‘ring."


async def ai_command(update, context):
    if not await require_subscription(update, context):
        return
    context.user_data["ai_mode"] = True
    await update.message.reply_text(
        "🤖 AI KINO YORDAMCHISI\n\n"
        "Menga qanday kino xohlayotganingizni yozing.\n"
        "Masalan: «Menga jangari va qiziqarli kino tavsiya qil».\n\n"
        "❌ AI rejimidan chiqish: /exitai"
    )


async def exit_ai(update, context):
    context.user_data["ai_mode"] = False
    await update.message.reply_text(
        "✅ AI rejimidan chiqdingiz.",
        reply_markup=main_keyboard()
    )


# =========================================================
# 💎 PREMIUM / USER MENYU
# =========================================================

async def premium_info_callback(update, context):
    q = update.callback_query
    await q.answer()
    price = get_setting("premium_price")
    await q.message.reply_text(
        f"💎 PREMIUM\n\n"
        f"💰 {price}\n\n"
        "Premium bilan maxsus filmlarni tomosha qilishingiz mumkin.",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("👨‍💻 Admin", url=f"tg://user?id={ADMIN_ID}")
        ]])
    )


async def menu_message(update, context):
    user = update.effective_user
    save_user(user)

    if not await require_subscription(update, context):
        return

    text = update.message.text.strip()

    if context.user_data.get("ai_mode"):
        if text in ("🔎 Kino qidirish", "💎 Premium", "🎬 Kinolar", "ℹ️ Yordam"):
            context.user_data["ai_mode"] = False
        else:
            waiting = await update.message.reply_text("🤖 O‘ylayapman...")
            answer = await ask_ai(user.id, text)
            await waiting.edit_text(answer[:4000])
            return

    if text == "💎 Premium":
        price = get_setting("premium_price")
        user_row = get_user(user.id)
        if is_premium(user.id):
            until = user_row[4] if user_row else None
            msg = "💎 Sizda Premium faol!"
            if until:
                msg += f"\n📅 Tugash vaqti: {until.replace('T', ' ')}"
            await update.message.reply_text(msg)
        else:
            await update.message.reply_text(
                f"💎 PREMIUM\n\n💰 {price}\n\n"
                "Premium maxsus filmlarni ochadi.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("👨‍💻 Premium olish", url=f"tg://user?id={ADMIN_ID}")
                ]])
            )
        return

    if text == "🤖 AI yordamchi":
        context.user_data["ai_mode"] = True
        await update.message.reply_text(
            "🤖 AI KINO YORDAMCHISI\n\n"
            "Qanday kino xohlaysiz? Menga yozing.\n\n"
            "Masalan: «Marvelga o‘xshagan kino tavsiya qil».\n"
            "Chiqish: /exitai"
        )
        return

    if text == "🔎 Kino qidirish":
        await update.message.reply_text(
            "🔎 Kino kodini yoki nomini yozing.\n"
            "Masalan: 10 yoki Avatar"
        )
        return

    if text == "🎬 Kinolar":
        rows = get_movies(20)
        if not rows:
            await update.message.reply_text("🎬 Hozircha kino qo‘shilmagan.")
            return
        msg = "🎬 KINOLAR\n\n"
        for code, title, premium, _ in rows:
            msg += f"{'💎' if premium else '🆓'} {title} — kod: {code}\n"
        await update.message.reply_text(msg[:4000])
        return

    if text == "ℹ️ Yordam":
        await update.message.reply_text(
            "ℹ️ YORDAM\n\n"
            "🔢 Kod yuborsangiz — kino chiqadi.\n"
            "🔎 Kino nomini yozsangiz — qidiradi.\n"
            "🤖 AI — didingizga mos kino tanlashga yordam beradi.\n"
            "💎 Premium — maxsus kinolarni ochadi."
        )
        return

    if text.isdigit():
        await send_movie(update, context, text)
        return

    # Nomi bo'yicha qidirish
    rows = search_movies(text)
    if rows:
        msg = "🔎 Topilgan kinolar:\n\n"
        for code, title, premium, views in rows:
            msg += f"{'💎' if premium else '🆓'} {title}\n🔢 Kod: {code} | 👁 {views}\n\n"
        await update.message.reply_text(msg[:4000])
        return

    await update.message.reply_text(
        "🤔 Kino topilmadi.\n\n"
        "🔢 Kod yuboring, kino nomini yozing yoki 🤖 AI yordamchidan foydalaning.",
        reply_markup=main_keyboard()
    )


# =========================================================
# ⚠️ ERROR
# =========================================================

async def error_handler(update, context):
    logger.error("Bot xatosi:", exc_info=context.error)


# =========================================================
# 🚀 MAIN
# =========================================================

def main():
    create_database()

    if BOT_TOKEN == "YANGI_TOKENNI_SHU_YERGA_QOY":
        print("❌ Yangi BOT_TOKEN qo‘yilmagan!")
        print("BotFather'dan yangi token olib BOT_TOKEN joyiga qo‘ying.")
        return

    application = Application.builder().token(BOT_TOKEN).build()

    add_movie_conversation = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(add_movie_start, pattern="^admin_add_movie$")
        ],
        states={
            ADD_VIDEO: [
                MessageHandler(
                    filters.VIDEO | filters.Document.ALL | filters.ANIMATION,
                    add_movie_video
                )
            ],
            ADD_TITLE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_movie_title)
            ],
            ADD_CODE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_movie_code)
            ],
            ADD_TYPE: [
                CallbackQueryHandler(add_movie_type, pattern="^movie_(normal|premium)$")
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_add)],
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin))
    application.add_handler(CommandHandler("delete", delete_movie_command))
    application.add_handler(CommandHandler("premium", premium_command))
    application.add_handler(CommandHandler("unpremium", unpremium_command))
    application.add_handler(CommandHandler("broadcast", broadcast_command))
    application.add_handler(CommandHandler("addchannel", addchannel_command))
    application.add_handler(CommandHandler("delchannel", delchannel_command))
    application.add_handler(CommandHandler("setprice", setprice_command))
    application.add_handler(CommandHandler("ai", ai_command))
    application.add_handler(CommandHandler("exitai", exit_ai))

    # Conversation admin callbackdan oldin
    application.add_handler(add_movie_conversation)

    application.add_handler(
        CallbackQueryHandler(
            check_subscription_callback,
            pattern="^check_subscription$"
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            premium_info_callback,
            pattern="^premium_info$"
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            admin_buttons,
            pattern="^admin_"
        )
    )

    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, menu_message)
    )

    application.add_error_handler(error_handler)

    print("")
    print("======================================")
    print("🎬 F1RST MOVIE BOT")
    print("======================================")
    print(f"👑 ADMIN ID: {ADMIN_ID}")
    print("✅ SQLite database")
    print("✅ Kino tizimi")
    print("✅ Kengaytirilgan admin panel")
    print("✅ 30 kunlik Premium")
    print("✅ Majburiy kanallar")
    print("✅ Broadcast")
    print("✅ Userlar")
    print("✅ Kino qidirish")
    print("✅ AI yordamchi")
    print("🚀 Bot ishga tushdi")
    print("======================================")
    print("")

    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
