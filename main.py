import os
import sqlite3
import logging
import random
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
    ContextTypes
)

# ------------------ Загрузка переменных окружения ------------------
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
if not TOKEN or not ADMIN_ID:
    raise ValueError("Не заданы BOT_TOKEN или ADMIN_ID в файле .env")

# ------------------ Конфигурация ------------------
WAITING_FOR_ORDER = 1

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

DB_NAME = "orders.db"

# ------------------ Работа с базой данных ------------------
def init_db():
    """Создаёт таблицу orders, если её нет, и добавляет колонку order_code при необходимости."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='orders'")
    if not cursor.fetchone():
        cursor.execute("""
            CREATE TABLE orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_code INTEGER UNIQUE NOT NULL,
                user_id INTEGER NOT NULL,
                username TEXT,
                order_text TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
    else:
        cursor.execute("PRAGMA table_info(orders)")
        columns = [col[1] for col in cursor.fetchall()]
        if "order_code" not in columns:
            cursor.execute("ALTER TABLE orders ADD COLUMN order_code INTEGER UNIQUE")
            cursor.execute("SELECT id FROM orders WHERE order_code IS NULL")
            rows = cursor.fetchall()
            for (order_id,) in rows:
                while True:
                    code = random.randint(1000, 9999)
                    cursor.execute("SELECT 1 FROM orders WHERE order_code = ?", (code,))
                    if not cursor.fetchone():
                        cursor.execute("UPDATE orders SET order_code = ? WHERE id = ?", (code, order_id))
                        break
            conn.commit()
    conn.close()

def generate_unique_code():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    while True:
        code = random.randint(1000, 9999)
        cursor.execute("SELECT 1 FROM orders WHERE order_code = ?", (code,))
        if not cursor.fetchone():
            conn.close()
            return code

def add_order(user_id, username, order_text):
    code = generate_unique_code()
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO orders (order_code, user_id, username, order_text) VALUES (?, ?, ?, ?)",
        (code, user_id, username, order_text)
    )
    conn.commit()
    conn.close()
    return code

def get_pending_orders():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, order_code, user_id, username, order_text, created_at FROM orders WHERE status = 'pending' ORDER BY id"
    )
    rows = cursor.fetchall()
    conn.close()
    return rows

def get_order_by_id(order_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT id, order_code, user_id, username, order_text, status FROM orders WHERE id = ?", (order_id,))
    row = cursor.fetchone()
    conn.close()
    return row

def update_order_status(order_id, new_status):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM orders WHERE id = ?", (order_id,))
    row = cursor.fetchone()
    if row:
        user_id = row[0]
        cursor.execute("UPDATE orders SET status = ? WHERE id = ?", (new_status, order_id))
        conn.commit()
        conn.close()
        return user_id
    conn.close()
    return None

def update_order_text(order_id, new_text):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM orders WHERE id = ?", (order_id,))
    row = cursor.fetchone()
    if row:
        user_id = row[0]
        cursor.execute("UPDATE orders SET order_text = ? WHERE id = ?", (new_text, order_id))
        conn.commit()
        conn.close()
        return user_id
    conn.close()
    return None

# ------------------ Пользовательские обработчики ------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    await update.message.reply_text(
        f"Привет, {user.first_name}!\n"
        "Добро пожаловать в Владимербериз. 🛍️\n\n"
        "Пожалуйста, отправь одним сообщением свой заказ в формате:\n"
        "Что заказываешь, способ оплаты и сроки.\n\n"
        "Пример: «Алмазная кирка, оплата монетами, готов через 2 дня»"
    )
    return WAITING_FOR_ORDER

async def order_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    order_text = update.message.text
    username = user.username or f"{user.first_name} {user.last_name or ''}".strip()

    order_code = add_order(user.id, username, order_text)

    await update.message.reply_text(
        f"✅ Заказ принят!\n"
        f"Номер вашего заказа: #{order_code}\n"
        "Ожидайте, скоро с вами свяжутся."
    )
    if ADMIN_ID:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"🆕 Новый заказ #{order_code} от {username}\n\n{order_text}"
        )
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Оформление заказа отменено.")
    return ConversationHandler.END

# ------------------ Админские обработчики ------------------
admin_keyboard = ReplyKeyboardMarkup(
    [["📋 Мои заказы"]],
    resize_keyboard=True
)

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("У вас нет доступа.")
        return
    await update.message.reply_text(
        "Панель администратора:",
        reply_markup=admin_keyboard
    )

async def display_orders(chat, context, edit=False):
    """Универсальная функция отображения списка заказов."""
    orders = get_pending_orders()
    if not orders:
        if edit:
            await chat.edit_message_text("Нет ожидающих заказов.")
        else:
            await chat.reply_text("Нет ожидающих заказов.")
        return

    page = context.user_data.get("admin_page", 0)
    total_pages = (len(orders) + 4) // 5
    if page < 0:
        page = 0
    if page >= total_pages:
        page = total_pages - 1
    start = page * 5
    end = start + 5
    page_orders = orders[start:end]

    keyboard = []
    for order in page_orders:
        order_id, code, user_id, username, order_text, created_at = order
        short_text = (order_text[:20] + "...") if len(order_text) > 20 else order_text
        keyboard.append([InlineKeyboardButton(
            f"#{code} | {username} | {short_text}",
            callback_data=f"show_order_{order_id}"
        )])

    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("◀️ Назад", callback_data="admin_page_prev"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("Вперёд ▶️", callback_data="admin_page_next"))
    if nav_buttons:
        keyboard.append(nav_buttons)

    reply_markup = InlineKeyboardMarkup(keyboard)
    if edit:
        await chat.edit_message_text("Выберите заказ:", reply_markup=reply_markup)
    else:
        await chat.reply_text("Выберите заказ:", reply_markup=reply_markup)

async def show_orders_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await display_orders(update.message, context, edit=False)

async def handle_admin_pagination(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if update.effective_user.id != ADMIN_ID:
        await query.edit_message_text("Нет доступа.")
        return

    action = query.data
    page = context.user_data.get("admin_page", 0)
    if action == "admin_page_prev":
        page -= 1
    elif action == "admin_page_next":
        page += 1
    context.user_data["admin_page"] = page
    await display_orders(query, context, edit=True)

async def show_order_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if update.effective_user.id != ADMIN_ID:
        await query.edit_message_text("Нет доступа.")
        return

    order_id = int(query.data.split("_")[2])
    order = get_order_by_id(order_id)
    if not order:
        await query.edit_message_text("Заказ не найден.")
        return

    order_id, code, user_id, username, order_text, status = order
    text = (
        f"📦 Заказ #{code}\n"
        f"👤 Пользователь: {username}\n"
        f"📝 Описание:\n{order_text}\n"
        f"🏷️ Статус: {status}"
    )
    keyboard = [
        [
            InlineKeyboardButton("❌ Отменить", callback_data=f"cancel_order_{order_id}"),
            InlineKeyboardButton("✏️ Изменить", callback_data=f"edit_order_{order_id}"),
            InlineKeyboardButton("✅ Подтвердить", callback_data=f"ready_order_{order_id}")
        ],
        [InlineKeyboardButton("🔙 Назад к списку", callback_data="back_to_orders")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, reply_markup=reply_markup)

async def handle_order_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if update.effective_user.id != ADMIN_ID:
        await query.edit_message_text("Нет доступа.")
        return

    data = query.data
    logger.info(f"Admin action: {data}")

    if data.startswith("cancel_order_"):
        order_id = int(data.split("_")[2])
        user_id = update_order_status(order_id, "cancelled")
        if user_id:
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"❌ Ваш заказ #{order_id} был отменён администратором."
                )
            except Exception as e:
                logger.error(f"Не удалось уведомить пользователя {user_id}: {e}")
            await display_orders(query, context, edit=True)
        else:
            await query.edit_message_text("❌ Ошибка: заказ не найден.")

    elif data.startswith("ready_order_"):
        order_id = int(data.split("_")[2])
        user_id = update_order_status(order_id, "ready")
        if user_id:
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"🎉 Ваш заказ #{order_id} готов! Можете забирать."
                )
            except Exception as e:
                logger.error(f"Не удалось уведомить пользователя {user_id}: {e}")
            await display_orders(query, context, edit=True)
        else:
            await query.edit_message_text("❌ Ошибка: заказ не найден.")

    elif data.startswith("edit_order_"):
        order_id = int(data.split("_")[2])
        context.user_data["editing_order_id"] = order_id
        await query.edit_message_text(
            "✏️ Введите новый текст заказа (одним сообщением).\n"
            "Или нажмите /cancel_edit для отмены."
        )

    elif data == "back_to_orders":
        await display_orders(query, context, edit=True)
    else:
        await query.edit_message_text("Неизвестная команда.")

async def handle_edit_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    editing_order_id = context.user_data.get("editing_order_id")
    if not editing_order_id:
        return

    new_text = update.message.text
    user_id = update_order_text(editing_order_id, new_text)
    if user_id:
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"✏️ Ваш заказ #{editing_order_id} был изменён администратором.\n"
                     f"Новая версия:\n{new_text}"
            )
        except Exception as e:
            logger.error(f"Не удалось уведомить пользователя {user_id}: {e}")
        await update.message.reply_text("✅ Заказ изменён. Пользователь уведомлён.")
    else:
        await update.message.reply_text("❌ Ошибка: заказ не найден.")

    context.user_data.pop("editing_order_id", None)
    await admin_panel(update, context)

async def cancel_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    context.user_data.pop("editing_order_id", None)
    await update.message.reply_text("Редактирование отменено.")
    await admin_panel(update, context)

# ------------------ Основная функция ------------------
def main():
    init_db()

    # Создаём приложение бота
    application = ApplicationBuilder().token(TOKEN).build()

    # ConversationHandler для пользователя
    user_conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            WAITING_FOR_ORDER: [MessageHandler(filters.TEXT & ~filters.COMMAND, order_received)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    application.add_handler(user_conv)

    # Админские команды
    application.add_handler(CommandHandler("admin", admin_panel))
    application.add_handler(MessageHandler(filters.Regex("^📋 Мои заказы$") & filters.User(ADMIN_ID), show_orders_list))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.User(ADMIN_ID), handle_edit_text))
    application.add_handler(CommandHandler("cancel_edit", cancel_edit))

    # Callback-обработчики
    application.add_handler(CallbackQueryHandler(handle_admin_pagination, pattern="^admin_page_"))
    application.add_handler(CallbackQueryHandler(show_order_details, pattern="^show_order_"))
    application.add_handler(CallbackQueryHandler(handle_order_action, pattern="^(cancel_order_.*|ready_order_.*|edit_order_.*|back_to_orders)$"))

    print("Бот запущен...")
    application.run_polling()

if __name__ == "__main__":
    main()