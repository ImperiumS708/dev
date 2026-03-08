import os
import logging
import psycopg2
from psycopg2.extras import DictCursor
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler, MessageHandler, CallbackQueryHandler, ConversationHandler, Filters, CallbackContext

# Настройка логирования
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Состояния для ConversationHandler
PROJECT_NAME, ITEM_TEXT, SCREENSHOT = range(3)

# Переменные окружения
BOT_TOKEN = os.environ.get('BOT_TOKEN')
DATABASE_URL = os.environ.get('DATABASE_URL')

if not BOT_TOKEN:
    raise ValueError("No BOT_TOKEN environment variable set")
if not DATABASE_URL:
    raise ValueError("No DATABASE_URL environment variable set")

# Подключение к БД
def get_db():
    conn = psycopg2.connect(DATABASE_URL, sslmode='require')
    return conn

def init_db():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                name TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS items (
                id SERIAL PRIMARY KEY,
                project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
                text TEXT NOT NULL,
                completed BOOLEAN DEFAULT FALSE,
                screenshot_file_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()

# Функции работы с БД
def create_project(user_id, name):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("INSERT INTO projects (user_id, name) VALUES (%s, %s) RETURNING id", (user_id, name))
        return cur.fetchone()[0]

def get_projects(user_id):
    with get_db() as conn:
        cur = conn.cursor(cursor_factory=DictCursor)
        cur.execute("SELECT id, name FROM projects WHERE user_id = %s ORDER BY created_at DESC", (user_id,))
        return cur.fetchall()

def get_project(project_id):
    with get_db() as conn:
        cur = conn.cursor(cursor_factory=DictCursor)
        cur.execute("SELECT id, user_id, name FROM projects WHERE id = %s", (project_id,))
        return cur.fetchone()

def add_item(project_id, text):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("INSERT INTO items (project_id, text) VALUES (%s, %s) RETURNING id", (project_id, text))
        return cur.fetchone()[0]

def get_items(project_id):
    with get_db() as conn:
        cur = conn.cursor(cursor_factory=DictCursor)
        cur.execute("SELECT id, text, completed, screenshot_file_id FROM items WHERE project_id = %s ORDER BY created_at", (project_id,))
        return cur.fetchall()

def mark_item_completed(item_id):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE items SET completed = TRUE WHERE id = %s", (item_id,))
        conn.commit()

def add_screenshot_to_item(item_id, file_id):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE items SET screenshot_file_id = %s WHERE id = %s", (file_id, item_id))
        conn.commit()

def get_item(item_id):
    with get_db() as conn:
        cur = conn.cursor(cursor_factory=DictCursor)
        cur.execute("SELECT id, project_id, text, completed, screenshot_file_id FROM items WHERE id = %s", (item_id,))
        return cur.fetchone()

# --- Обработчики команд ---
def start(update: Update, context: CallbackContext):
    keyboard = [
        [InlineKeyboardButton("📁 Мои проекты", callback_data='list_projects')],
        [InlineKeyboardButton("➕ Создать проект", callback_data='create_project')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text(
        "Привет! Я помогу тебе вести многоуровневые списки задач. Выбери действие:",
        reply_markup=reply_markup
    )

def button_handler(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    data = query.data
    user_id = query.from_user.id

    if data == 'list_projects':
        show_projects(query, user_id)
        return ConversationHandler.END
    elif data == 'create_project':
        query.edit_message_text("Введите название нового проекта:")
        return PROJECT_NAME
    elif data.startswith('open_project_'):
        project_id = int(data.split('_')[2])
        context.user_data['current_project'] = project_id
        show_project_items(query, project_id)
        return ConversationHandler.END
    elif data.startswith('add_item_'):
        project_id = int(data.split('_')[2])
        context.user_data['current_project'] = project_id
        query.edit_message_text("Введите текст нового пункта:")
        return ITEM_TEXT
    elif data.startswith('complete_item_'):
        item_id = int(data.split('_')[2])
        mark_item_completed(item_id)
        item = get_item(item_id)
        if item:
            context.user_data['current_project'] = item['project_id']
            show_project_items(query, item['project_id'])
        return ConversationHandler.END
    elif data.startswith('screenshot_item_'):
        item_id = int(data.split('_')[2])
        context.user_data['screenshot_item_id'] = item_id
        query.edit_message_text("Отправьте скриншот для этого пункта:")
        return SCREENSHOT
    elif data.startswith('view_screenshot_'):
        item_id = int(data.split('_')[2])
        item = get_item(item_id)
        if item and item['screenshot_file_id']:
            context.bot.send_photo(
                chat_id=query.message.chat_id,
                photo=item['screenshot_file_id'],
                caption=f"Скрин к пункту: {item['text']}"
            )
        else:
            query.edit_message_text("Скрин не найден.")
        if item:
            context.user_data['current_project'] = item['project_id']
            show_project_items(query, item['project_id'])
        return ConversationHandler.END
    elif data == 'back_to_projects':
        show_projects(query, user_id)
        return ConversationHandler.END
    elif data == 'main_menu':
        main_menu(query)
        return ConversationHandler.END

def show_projects(query, user_id):
    projects = get_projects(user_id)
    if not projects:
        query.edit_message_text(
            "У вас пока нет проектов. Создайте новый!",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("➕ Создать проект", callback_data="create_project")
            ]])
        )
        return

    text = "📁 Ваши проекты:\n\n"
    keyboard = []
    for proj in projects:
        keyboard.append([InlineKeyboardButton(proj['name'], callback_data=f"open_project_{proj['id']}")])
    keyboard.append([InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    query.edit_message_text(text, reply_markup=reply_markup)

def show_project_items(query, project_id):
    project = get_project(project_id)
    if not project:
        query.edit_message_text("Проект не найден.")
        return

    items = get_items(project_id)
    text = f"📌 Проект: {project['name']}\n\n"
    keyboard = []

    for item in items:
        status = "✅" if item['completed'] else "⏳"
        btn_text = f"{status} {item['text']}"
        if not item['completed']:
            keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"complete_item_{item['id']}")])
        else:
            if item['screenshot_file_id']:
                keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"view_screenshot_{item['id']}")])
            else:
                keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"screenshot_item_{item['id']}")])

    keyboard.append([InlineKeyboardButton("➕ Добавить пункт", callback_data=f"add_item_{project_id}")])
    keyboard.append([InlineKeyboardButton("🔙 К проектам", callback_data="back_to_projects")])
    keyboard.append([InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    query.edit_message_text(text, reply_markup=reply_markup)

def main_menu(query):
    keyboard = [
        [InlineKeyboardButton("📁 Мои проекты", callback_data="list_projects")],
        [InlineKeyboardButton("➕ Создать проект", callback_data="create_project")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    query.edit_message_text("Главное меню. Выбери действие:", reply_markup=reply_markup)

def project_name_handler(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    name = update.message.text
    project_id = create_project(user_id, name)
    update.message.reply_text(f"Проект «{name}» создан!")
    # Показываем меню проекта
    keyboard = [
        [InlineKeyboardButton("📋 Перейти к проекту", callback_data=f"open_project_{project_id}")],
        [InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text("Что дальше?", reply_markup=reply_markup)
    return ConversationHandler.END

def item_text_handler(update: Update, context: CallbackContext):
    project_id = context.user_data.get('current_project')
    if not project_id:
        update.message.reply_text("Ошибка: не выбран проект. Начните заново.")
        return ConversationHandler.END
    text = update.message.text
    item_id = add_item(project_id, text)
    update.message.reply_text(f"Пункт «{text}» добавлен в проект.")
    # Возвращаемся к проекту
    keyboard = [
        [InlineKeyboardButton("📋 К списку пунктов", callback_data=f"open_project_{project_id}")],
        [InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text("Что дальше?", reply_markup=reply_markup)
    return ConversationHandler.END

def screenshot_handler(update: Update, context: CallbackContext):
    item_id = context.user_data.get('screenshot_item_id')
    if not item_id:
        update.message.reply_text("Ошибка: не могу определить пункт для скрина.")
        return ConversationHandler.END
    photo_file = update.message.photo[-1].file_id
    add_screenshot_to_item(item_id, photo_file)
    item = get_item(item_id)
    if item:
        project_id = item['project_id']
        context.user_data['current_project'] = project_id
        update.message.reply_text("Скриншот сохранён!")
        keyboard = [
            [InlineKeyboardButton("📋 К списку пунктов", callback_data=f"open_project_{project_id}")],
            [InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        update.message.reply_text("Что дальше?", reply_markup=reply_markup)
    else:
        update.message.reply_text("Пункт не найден.")
    return ConversationHandler.END

def cancel(update: Update, context: CallbackContext):
    update.message.reply_text("Действие отменено.")
    return ConversationHandler.END

def error_handler(update: Update, context: CallbackContext):
    logger.error(msg="Exception while handling an update:", exc_info=context.error)

def main():
    # Инициализация БД
    init_db()

    # Создаём Updater
    updater = Updater(token=BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    # Обработчики команд
    dp.add_handler(CommandHandler("start", start))

    # ConversationHandler для создания проекта
    conv_project = ConversationHandler(
        entry_points=[CallbackQueryHandler(button_handler, pattern='^create_project$')],
        states={PROJECT_NAME: [MessageHandler(Filters.text & ~Filters.command, project_name_handler)]},
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    dp.add_handler(conv_project)

    # ConversationHandler для добавления пункта
    conv_item = ConversationHandler(
        entry_points=[CallbackQueryHandler(button_handler, pattern='^add_item_')],
        states={ITEM_TEXT: [MessageHandler(Filters.text & ~Filters.command, item_text_handler)]},
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    dp.add_handler(conv_item)

    # ConversationHandler для загрузки скрина
    conv_screenshot = ConversationHandler(
        entry_points=[CallbackQueryHandler(button_handler, pattern='^screenshot_item_')],
        states={SCREENSHOT: [MessageHandler(Filters.photo, screenshot_handler)]},
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    dp.add_handler(conv_screenshot)

    # Обработчики остальных callback'ов
    dp.add_handler(CallbackQueryHandler(button_handler, pattern='^(list_projects|open_project_|complete_item_|view_screenshot_|back_to_projects|main_menu)$'))

    # Обработчик ошибок
    dp.add_error_handler(error_handler)

    # Запуск бота (polling)
    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()