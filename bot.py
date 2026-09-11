import os
import time
import sqlite3
import threading
import telebot
from telebot import types
from openai import OpenAI
from dotenv import load_dotenv
from datetime import datetime, timedelta

# ============================================================
# ЗАГРУЗКА НАСТРОЕК
# ============================================================
load_dotenv()

TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
DEEPSEEK_API_KEY = os.getenv('DEEPSEEK_API_KEY')
YOOKASSA_PROVIDER_TOKEN = os.getenv('YOOKASSA_PROVIDER_TOKEN', '')

# --- Настройки монетизации ---
SUBSCRIPTION_PRICE_STARS = 150
SUBSCRIPTION_DAYS = 30
FREE_MESSAGES_PER_DAY = 10
PAYMENT_LINK_SBP = "https://example.com/sbp"

# --- Файлы проекта ---
DB_FILE = '/data/sexology_bot.db'
PROMPT_FILE = 'seksologiya.txt'

# ============================================================
# ЗАГРУЗКА ПРОМТА
# ============================================================
try:
    with open(PROMPT_FILE, 'r', encoding='utf-8') as f:
        SYSTEM_PROMPT = f.read()
    print("✅ Промт загружен")
except Exception as e:
    SYSTEM_PROMPT = "Ты полезный и дружелюбный ассистент."
    print(f"⚠️ Ошибка промта: {e}")

# ============================================================
# ИНИЦИАЛИЗАЦИЯ
# ============================================================
bot = telebot.TeleBot(TELEGRAM_TOKEN)
client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
user_states = {}

# ============================================================
# БАЗА ДАННЫХ
# ============================================================
def init_db():
    try:
        os.makedirs('/data', exist_ok=True)
    except Exception as e:
        print(f"⚠️ Не удалось создать /data: {e}")
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS history (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
        role TEXT, content TEXT, timestamp REAL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY, username TEXT, first_seen REAL, last_seen REAL,
        message_count INTEGER DEFAULT 0, messages_today INTEGER DEFAULT 0,
        last_message_date TEXT, bot_name TEXT DEFAULT 'Сексолог',
        tone TEXT DEFAULT 'soft', lang TEXT DEFAULT 'ru',
        subscription_until REAL, stars_balance INTEGER DEFAULT 0)''')
    c.execute('''CREATE TABLE IF NOT EXISTS reminders (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
        text TEXT, remind_at REAL, created_at REAL, active INTEGER DEFAULT 1)''')
    conn.commit()
    conn.close()

def db_exec(query, params=(), fetch=False):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(query, params)
    result = c.fetchall() if fetch else None
    conn.commit()
    conn.close()
    return result

def add_message(uid, role, content):
    db_exec('INSERT INTO history (user_id, role, content, timestamp) VALUES (?,?,?,?)',
            (uid, role, content, time.time()))

def get_history(uid, limit=10):
    rows = db_exec('SELECT role, content FROM history WHERE user_id=? ORDER BY id DESC LIMIT ?',
                   (uid, limit), fetch=True)
    return [{"role": r, "content": c} for r, c in reversed(rows)]

def clear_history(uid):
    db_exec('DELETE FROM history WHERE user_id=?', (uid,))

def register_user(uid, username):
    now = time.time()
    today = datetime.now().strftime('%Y-%m-%d')
    db_exec('''INSERT INTO users (user_id, username, first_seen, last_seen, message_count, last_message_date)
        VALUES (?,?,?,?,1,?) ON CONFLICT(user_id) DO UPDATE SET
        last_seen=?, message_count=message_count+1,
        messages_today = CASE WHEN last_message_date = ? THEN messages_today + 1 ELSE 1 END,
        last_message_date = ?''',
        (uid, username, now, now, today, now, today, today))

def get_user(uid):
    rows = db_exec('SELECT * FROM users WHERE user_id=?', (uid,), fetch=True)
    return rows[0] if rows else None

def update_user_setting(uid, field, value):
    db_exec(f'UPDATE users SET {field}=? WHERE user_id=?', (value, uid))

def has_active_subscription(uid):
    user = get_user(uid)
    if not user or not user[11]:
        return False
    return user[11] > time.time()

def check_message_limit(uid):
    if has_active_subscription(uid):
        return True
    user = get_user(uid)
    if not user:
        return True
    today = datetime.now().strftime('%Y-%m-%d')
    if user[8] != today:
        return True
    return user[7] < FREE_MESSAGES_PER_DAY

# ============================================================
# RATE LIMITING
# ============================================================
user_last = {}
def is_limited(uid):
    now = time.time()
    if uid in user_last and now - user_last[uid] < 2:
        return True
    user_last[uid] = now
    return False

# ============================================================
# КЛАВИАТУРЫ
# ============================================================
def main_kb():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(
        types.KeyboardButton("💬 Задать вопрос"),
        types.KeyboardButton("📚 Просвещение"),
        types.KeyboardButton("💞 Совместимость"),
        types.KeyboardButton("🧠 Травма и близость"),
        types.KeyboardButton("📊 Прогресс"),
        types.KeyboardButton("⚙️ Настройки"),
        types.KeyboardButton("💎 Подписка"),
        types.KeyboardButton("🚨 Срочно"),
        types.KeyboardButton("❓ Помощь")
    )
    return kb

def settings_kb():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("✏️ Имя бота", callback_data="set_name"),
        types.InlineKeyboardButton("🎭 Тон", callback_data="set_tone"),
        types.InlineKeyboardButton("🔔 Напоминания", callback_data="reminders"),
        types.InlineKeyboardButton("🧹 Сброс памяти", callback_data="reset_mem"),
        types.InlineKeyboardButton("⬅️ Назад", callback_data="back_main")
    )
    return kb

def feedback_kb():
    kb = types.InlineKeyboardMarkup(row_width=3)
    kb.add(
        types.InlineKeyboardButton("👍 Полезно", callback_data="fb_useful"),
        types.InlineKeyboardButton("😐 Нейтрально", callback_data="fb_neutral"),
        types.InlineKeyboardButton("👎 Не помогло", callback_data="fb_bad")
    )
    return kb

def education_kb():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("🫀 Анатомия", callback_data="edu_anatomy"),
        types.InlineKeyboardButton("🩺 Контрацепция", callback_data="edu_contra"),
        types.InlineKeyboardButton("🧬 ИППП", callback_data="edu_sti"),
        types.InlineKeyboardButton("💭 Мифы о сексе", callback_data="edu_myths"),
        types.InlineKeyboardButton("⬅️ Назад", callback_data="back_main")
    )
    return kb

def payment_kb():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("⭐ Telegram Stars", callback_data="pay_stars"),
        types.InlineKeyboardButton("💳 Банковская карта (ЮKassa)", callback_data="pay_yookassa"),
        types.InlineKeyboardButton("⚡ СБП (по ссылке)", callback_data="pay_sbp")
    )
    return kb

# ============================================================
# КОМАНДЫ И ОПЛАТА
# ============================================================
@bot.message_handler(commands=['start'])
def cmd_start(m):
    uid = m.from_user.id
    name = m.from_user.first_name or "друг"
    register_user(uid, m.from_user.username or name)
    user = get_user(uid)
    bot_name = user[7] if user else "Сексолог"
    bot.send_message(m.chat.id,
        f"Здравствуйте, {name}! 🌿\n\n"
        f"Я — {bot_name}. Я работаю с вопросами сексуального здоровья, желания, "
        f"совместимости и интимной коммуникации — без стыда, пошлости и ханжества.\n\n"
        "⚠️ Я не ставлю диагнозов и не заменяю врача. При острых симптомах — "
        "обратитесь к специалисту очно.\n\n"
        "Меню внизу экрана. Выбирайте, с чего начать 💚",
        reply_markup=main_kb())

@bot.message_handler(commands=['help'])
def cmd_help(m):
    bot.send_message(m.chat.id,
        "📚 *Что я умею:*\n\n"
        "💬 Задать вопрос — консультация по сексуальному здоровью\n"
        "📚 Просвещение — анатомия, контрацепция, ИППП, мифы\n"
        "💞 Совместимость — работа с парой\n"
        "🧠 Травма и близость — бережная помощь\n"
        "🚨 Срочно — экстренная помощь\n"
        "💎 Подписка — снять ограничения\n\n"
        "Команды: /start /help /reset /stats /subscribe",
        parse_mode='Markdown')

@bot.message_handler(commands=['reset'])
def cmd_reset(m):
    clear_history(m.from_user.id)
    bot.send_message(m.chat.id, "🧹 Память очищена. Начнём заново?", reply_markup=main_kb())

@bot.message_handler(commands=['stats'])
def cmd_stats(m):
    uid = m.from_user.id
    user = get_user(uid)
    if user:
        count, first = user[4], user[3]
        days = int((time.time() - first) / 86400) + 1
        status = "✅ Активна" if has_active_subscription(uid) else "❌ Нет подписки"
        bot.send_message(m.chat.id,
            f"📊 *Ваш прогресс:*\n\n"
            f"💬 Сообщений: {count}\n"
            f"📅 Дней со мной: {days}\n"
            f"💎 Подписка: {status}",
            parse_mode='Markdown')

@bot.message_handler(commands=['subscribe'])
def cmd_subscribe(m):
    send_payment_options(m.from_user.id)

@bot.message_handler(func=lambda m: m.text == "💎 Подписка")
def btn_subscription(m):
    uid = m.from_user.id
    if has_active_subscription(uid):
        user = get_user(uid)
        until = datetime.fromtimestamp(user[11]).strftime('%d.%m.%Y')
        bot.send_message(m.chat.id, f"✅ У вас уже есть активная подписка до {until}. Спасибо! 💚")
    else:
        send_payment_options(uid)

def send_payment_options(chat_id):
    text = "Выберите удобный способ оплаты подписки:"
    bot.send_message(chat_id, text, reply_markup=payment_kb())

# ============================================================
# КНОПКИ ГЛАВНОГО МЕНЮ
# ============================================================
@bot.message_handler(func=lambda m: m.text == "💬 Задать вопрос")
def btn_talk(m):
    bot.send_message(m.chat.id,
        "Я слушаю. Расскажите, что вас беспокоит — я отвечу бережно и по делу. 💚",
        reply_markup=main_kb())

@bot.message_handler(func=lambda m: m.text == "📚 Просвещение")
def btn_education(m):
    bot.send_message(m.chat.id, "Что вас интересует?", reply_markup=education_kb())

@bot.message_handler(func=lambda m: m.text == "💞 Совместимость")
def btn_compatibility(m):
    bot.send_message(m.chat.id,
        "💞 *Совместимость в паре*\n\n"
        "Расскажите, что происходит: разные желания, разная частота, "
        "непонимание в постели, отсутствие разговора о сексе?\n\n"
        "Опишите ситуацию — и я помогу найти бережный путь друг к другу.",
        parse_mode='Markdown', reply_markup=main_kb())

@bot.message_handler(func=lambda m: m.text == "🧠 Травма и близость")
def btn_trauma(m):
    bot.send_message(m.chat.id,
        "🧠 *Работа с травмой*\n\n"
        "Если у вас был негативный сексуальный опыт, и он мешает близости — "
        "я здесь. Мы пойдём очень медленно и бережно.\n\n"
        "Что сейчас откликается сильнее всего?",
        parse_mode='Markdown', reply_markup=main_kb())

@bot.message_handler(func=lambda m: m.text == "📊 Прогресс")
def btn_progress(m):
    cmd_stats(m)

@bot.message_handler(func=lambda m: m.text == "⚙️ Настройки")
def btn_settings(m):
    bot.send_message(m.chat.id, "⚙️ Настройки бота:", reply_markup=settings_kb())

@bot.message_handler(func=lambda m: m.text == "🚨 Срочно")
def btn_sos(m):
    sos = (
        "🚨 *Срочная помощь*\n\n"
        "Если вы столкнулись с насилием, острой болью, кровотечением, "
        "подозрением на ИППП или психологическим кризисом — не оставайтесь одни.\n\n"
        "🇷🇺 *Телефоны доверия:*\n"
        "• 8-800-2000-122 — детям и подросткам\n"
        "• 8-800-7000-600 — кризисная линия для женщин\n"
        "• 8-495-989-50-50 — центр экстренной психологической помощи МЧС\n"
        "• 103 — скорая помощь\n"
        "• 112 — единый номер экстренных служб\n\n"
        "💚 Я рядом и готов поддержать в переписке, но при угрозе жизни — звоните."
    )
    bot.send_message(m.chat.id, sos, parse_mode='Markdown', reply_markup=main_kb())

@bot.message_handler(func=lambda m: m.text == "❓ Помощь")
def btn_help(m):
    cmd_help(m)

# ============================================================
# INLINE CALLBACKS
# ============================================================
@bot.callback_query_handler(func=lambda c: True)
def handle_callback(c):
    uid = c.from_user.id
    data = c.data

    if data.startswith("fb_"):
        bot.answer_callback_query(c.id, "Спасибо за отзыв! 💚")
        try:
            bot.edit_message_reply_markup(c.message.chat.id, c.message.message_id, reply_markup=None)
        except Exception:
            pass
        return

    # Просвещение
    edu_map = {
        "edu_anatomy": "🫀 *Анатомия и физиология*\n\nЧто именно вас интересует: строение, возбуждение, цикл, гормоны? Напишите вопрос — я объясню доступно.",
        "edu_contra": "🩺 *Контрацепция*\n\nСуществует много методов: барьерные, гормональные, ВМС, экстренная. У каждого свои плюсы и ограничения. Опишите вашу ситуацию — подскажу, что обсудить с врачом.",
        "edu_sti": "🧬 *ИППП и профилактика*\n\nРегулярные проверки, барьерная защита, вакцинация от ВПЧ и гепатита B — база безопасности. Что именно вас интересует?",
        "edu_myths": "💭 *Мифы о сексе*\n\n«Норма частоты», «размер имеет значение», «женщины не хотят», «мастурбация вредна» — это всё мифы. Спросите про конкретный — разберём."
    }
    if data in edu_map:
        bot.answer_callback_query(c.id)
        bot.send_message(c.message.chat.id, edu_map[data], parse_mode='Markdown', reply_markup=main_kb())
        return

    if data == "set_name":
        bot.answer_callback_query(c.id)
        bot.send_message(c.message.chat.id, "Как вы хотите меня называть? Напишите имя.")
        user_states[uid] = {'awaiting': 'bot_name'}
        return
    if data == "set_tone":
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            types.InlineKeyboardButton("🌸 Мягкий", callback_data="tone_soft"),
            types.InlineKeyboardButton("⚡ Прямой", callback_data="tone_direct"),
            types.InlineKeyboardButton("🎓 Наставник", callback_data="tone_mentor"),
            types.InlineKeyboardButton("😊 Друг", callback_data="tone_friend")
        )
        try:
            bot.edit_message_text("Выберите тон общения:", c.message.chat.id, c.message.message_id, reply_markup=kb)
        except Exception:
            pass
        return
    if data.startswith("tone_"):
        tone = data.replace("tone_", "")
        update_user_setting(uid, "tone", tone)
        bot.answer_callback_query(c.id, f"Тон: {tone}")
        try:
            bot.edit_message_text(f"✅ Тон общения изменён: {tone}", c.message.chat.id, c.message.message_id)
        except Exception:
            pass
        return
    if data == "reminders":
        bot.answer_callback_query(c.id)
        bot.send_message(c.message.chat.id, "🔔 *Напоминания*\n\nНапишите в формате:\n`Напоминание | ЧЧ:ММ`\n\nНапример: `Принять таблетку | 21:00`")
        user_states[uid] = {'awaiting': 'reminder'}
        return
    if data == "reset_mem":
        clear_history(uid)
        bot.answer_callback_query(c.id, "Память очищена!")
        try:
            bot.edit_message_text("🧹 Память очищена.", c.message.chat.id, c.message.message_id)
        except Exception:
            pass
        return
    if data == "back_main":
        try:
            bot.edit_message_text("Главное меню — используйте кнопки внизу 👇", c.message.chat.id, c.message.message_id)
        except Exception:
            pass
        return

    # Оплата
    if data == "pay_stars":
        send_stars_invoice(uid)
        bot.answer_callback_query(c.id)
    elif data == "pay_yookassa":
        send_yookassa_invoice(uid)
        bot.answer_callback_query(c.id)
    elif data == "pay_sbp":
        bot.send_message(uid, f"Для оплаты через СБП перейдите по ссылке:\n{PAYMENT_LINK_SBP}")
        bot.answer_callback_query(c.id)

def send_stars_invoice(chat_id):
    try:
        prices = [types.LabeledPrice(label=f"Подписка на {SUBSCRIPTION_DAYS} дней", amount=SUBSCRIPTION_PRICE_STARS)]
        bot.send_invoice(
            chat_id=chat_id,
            title=f"Подписка «Сексолог» на {SUBSCRIPTION_DAYS} дней",
            description=f"Неограниченный доступ ко всем функциям на {SUBSCRIPTION_DAYS} дней.",
            invoice_payload=f"subscription_{SUBSCRIPTION_DAYS}days",
            provider_token="",
            currency="XTR",
            prices=prices,
            start_parameter="subscription"
        )
    except Exception as e:
        bot.send_message(chat_id, "😔 Не удалось создать счёт для оплаты. Попробуйте позже.")
        print(f"[INVOICE ERROR] {e}")

def send_yookassa_invoice(chat_id):
    bot.send_message(chat_id, "💳 Оплата картой временно недоступна. Пожалуйста, используйте Telegram Stars.")

@bot.pre_checkout_query_handler(func=lambda query: True)
def process_pre_checkout(pre_checkout_query):
    bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@bot.message_handler(content_types=['successful_payment'])
def process_successful_payment(message):
    uid = message.from_user.id
    new_subscription_until = time.time() + (SUBSCRIPTION_DAYS * 24 * 60 * 60)
    db_exec('UPDATE users SET subscription_until=? WHERE user_id=?', (new_subscription_until, uid))
    until_date = datetime.fromtimestamp(new_subscription_until).strftime('%d.%m.%Y')
    bot.send_message(
        message.chat.id,
        f"🎉 *Оплата прошла успешно!*\n\nВаша подписка активирована до *{until_date}*.\n\nСпасибо за доверие! 💚",
        parse_mode='Markdown',
        reply_markup=main_kb()
    )

# ============================================================
# ОТПРАВКА ДЛИННЫХ СООБЩЕНИЙ
# ============================================================
def send_long_message(chat_id, text, with_feedback=False):
    LIMIT = 4000
    if len(text) <= LIMIT:
        if with_feedback:
            bot.send_message(chat_id, text, reply_markup=feedback_kb())
        else:
            bot.send_message(chat_id, text)
        return
    parts = [text[i:i+LIMIT] for i in range(0, len(text), LIMIT)]
    for idx, part in enumerate(parts):
        is_last = (idx == len(parts) - 1)
        if is_last and with_feedback:
            bot.send_message(chat_id, part, reply_markup=feedback_kb())
        else:
            bot.send_message(chat_id, part)
        time.sleep(0.3)

# ============================================================
# ОБРАБОТКА ТЕКСТА
# ============================================================
@bot.message_handler(func=lambda m: True)
def handle_all(m):
    uid = m.from_user.id
    text = m.text.strip() if m.text else ""

    if uid in user_states:
        state = user_states[uid]
        if state.get('awaiting') == 'bot_name':
            update_user_setting(uid, "bot_name", text[:30])
            bot.send_message(m.chat.id, f"✅ Теперь меня зовут {text[:30]}. Приятно познакомиться!", reply_markup=main_kb())
            del user_states[uid]
            return
        if state.get('awaiting') == 'reminder':
            try:
                parts = text.split('|')
                remind_text = parts[0].strip()
                remind_time = parts[1].strip()
                h, mi = map(int, remind_time.split(':'))
                now = datetime.now()
                target = now.replace(hour=h, minute=mi, second=0, microsecond=0)
                if target < now:
                    target += timedelta(days=1)
                db_exec('INSERT INTO reminders (user_id, text, remind_at, created_at) VALUES (?,?,?,?)', (uid, remind_text, target.timestamp(), time.time()))
                bot.send_message(m.chat.id, f"🔔 Напоминание сохранено:\n*{remind_text}*\nВ {h:02d}:{mi:02d}", parse_mode='Markdown', reply_markup=main_kb())
            except Exception:
                bot.send_message(m.chat.id, "❌ Не понял формат. Пример: `Принять таблетку | 21:00`", parse_mode='Markdown')
            del user_states[uid]
            return

    if not text:
        return

    # Проверка лимита
    if not has_active_subscription(uid) and not check_message_limit(uid):
        bot.reply_to(m,
            f"😔 Вы использовали {FREE_MESSAGES_PER_DAY} бесплатных сообщений на сегодня.\n\n"
            f"Оформите подписку, чтобы снять ограничения. Нажмите «💎 Подписка» внизу.",
            reply_markup=main_kb())
        return

    if is_limited(uid):
        bot.reply_to(m, "⏳ Подождите пару секунд.")
        return

    register_user(uid, m.from_user.username or m.from_user.first_name or "user")
    add_message(uid, "user", text)
    bot.send_chat_action(m.chat.id, 'typing')

    user = get_user(uid)
    bot_name = user[7] if user else "Сексолог"
    tone = user[8] if user else "soft"
    tone_map = {
        "soft": "Говори мягко, бережно, с эмпатией, без осуждения.",
        "direct": "Говори прямо, чётко, по делу, без лишних слов.",
        "mentor": "Говори как опытный наставник, с примерами и метафорами.",
        "friend": "Говори как близкий друг, тепло и неформально."
    }
    personal_prompt = SYSTEM_PROMPT + f"\n\n[Твоё имя: {bot_name}. {tone_map.get(tone, '')}]"

    try:
        history = get_history(uid)
        messages = [{"role": "system", "content": personal_prompt}] + history
        response = client.chat.completions.create(
            model="deepseek-chat", messages=messages,
            stream=False, temperature=0.7, max_tokens=2000
        )
        answer = response.choices[0].message.content
        add_message(uid, "assistant", answer)
        send_long_message(m.chat.id, answer, with_feedback=True)
        bot.send_message(m.chat.id, "Чем ещё могу помочь? 👇", reply_markup=main_kb())
    except Exception as e:
        err = str(e)
        if "402" in err or "Insufficient" in err:
            bot.reply_to(m, "💳 Средства закончились. Скоро пополним.", reply_markup=main_kb())
        elif "Connection" in err or "Timeout" in err:
            bot.reply_to(m, "🌐 Связь нестабильна. Попробуйте через минуту.", reply_markup=main_kb())
        else:
            bot.reply_to(m, "😔 Небольшая заминка. Попробуйте ещё раз.", reply_markup=main_kb())
            print(f"[ERR] {err}")

# ============================================================
# ФОНОВАЯ ПРОВЕРКА НАПОМИНАНИЙ
# ============================================================
def reminder_worker():
    while True:
        try:
            now = time.time()
            rows = db_exec('SELECT id, user_id, text FROM reminders WHERE active=1 AND remind_at<=?',
                           (now,), fetch=True)
            for rid, uid, txt in rows:
                try:
                    bot.send_message(uid, f"🔔 *Напоминание:*\n\n{txt}", parse_mode='Markdown')
                except Exception:
                    pass
                db_exec('UPDATE reminders SET active=0 WHERE id=?', (rid,))
        except Exception as e:
            print(f"[REMINDER] {e}")
        time.sleep(30)

# ============================================================
# ЗАПУСК
# ============================================================
if __name__ == '__main__':
    init_db()
    print("✅ БД инициализирована")
    threading.Thread(target=reminder_worker, daemon=True).start()
    print("🚀 Бот запущен...")
    bot.polling(none_stop=True)