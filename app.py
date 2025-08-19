import os
import sqlite3
import hashlib
from flask import Flask, render_template, request, redirect, url_for, send_from_directory, session, abort
from datetime import datetime
from werkzeug.utils import secure_filename
import time

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'default_fallback_key')
app.config["UPLOAD_FOLDER"] = "static/uploads"
app.config["ALLOWED_EXTENSIONS"] = {"png", "jpg", "jpeg", "gif"}
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024  # 2MB max file size
app.config["BOARDS"] = {
    "b": "Бред",
    "a": "Аниме",
    "v": "Видеоигры",
    "mu": "Музыка",
    "t": "Технологии",
    "m": "Мужчины",
    "f": "Девушки",
    "c": "Комиксы",
    "k": "Книги"
}
app.config["MAX_THREADS_PER_BOARD"] = 100  # Лимит тредов
app.config["FLOOD_LIMIT_SECONDS"] = 30  # Задержка между постами
app.config["MOD_PASSWORD_HASH"] = hashlib.sha256("admin123".encode()).hexdigest()  # Пароль: admin123


DB_PATH = "threads.db"

def init_db():
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        # Таблица тредов
        c.execute("""
        CREATE TABLE IF NOT EXISTS threads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            board TEXT,
            title TEXT,
            content TEXT,
            image TEXT,
            created_at TEXT,
            bump_time TEXT
        )
        """)
        # Таблица ответов
        c.execute("""
        CREATE TABLE IF NOT EXISTS replies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id INTEGER,
            content TEXT,
            image TEXT,
            created_at TEXT,
            FOREIGN KEY(thread_id) REFERENCES threads(id)
        )
        """)
        # Таблица флуд-контроля
        c.execute("""
        CREATE TABLE IF NOT EXISTS flood_control (
            ip TEXT PRIMARY KEY,
            last_post_time TEXT
        )
        """)
        conn.commit()



def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in app.config["ALLOWED_EXTENSIONS"]


def delete_thread(thread_id):
    """Удаляет тред и все его файлы"""
    with sqlite3.connect("threads.db") as conn:
        c = conn.cursor()

        # Удаляем файлы ответов
        c.execute("SELECT image FROM replies WHERE thread_id=?", (thread_id,))
        for reply in c.fetchall():
            if reply[0]:
                file_path = os.path.join(app.config["UPLOAD_FOLDER"], reply[0])
                if os.path.exists(file_path):
                    os.remove(file_path)

        # Удаляем файл треда
        c.execute("SELECT image FROM threads WHERE id=?", (thread_id,))
        thread = c.fetchone()
        if thread and thread[0]:
            file_path = os.path.join(app.config["UPLOAD_FOLDER"], thread[0])
            if os.path.exists(file_path):
                os.remove(file_path)

        # Чистим БД
        c.execute("DELETE FROM replies WHERE thread_id=?", (thread_id,))
        c.execute("DELETE FROM threads WHERE id=?", (thread_id,))
        conn.commit()


def check_flood_limit(ip):
    """Проверяет, не флудит ли пользователь"""
    with sqlite3.connect("threads.db") as conn:
        c = conn.cursor()
        c.execute("SELECT last_post_time FROM flood_control WHERE ip=?", (ip,))
        result = c.fetchone()

        if result:
            last_time = datetime.strptime(result[0], "%Y-%m-%d %H:%M:%S")
            elapsed = (datetime.now() - last_time).total_seconds()
            if elapsed < app.config["FLOOD_LIMIT_SECONDS"]:
                return False

        # Обновляем время последнего поста
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        c.execute("""
            INSERT OR REPLACE INTO flood_control (ip, last_post_time)
            VALUES (?, ?)
        """, (ip, now))
        conn.commit()
    return True


# Модераторские функции
def is_moderator():
    return session.get('is_moderator')


@app.route("/mod/login", methods=["GET", "POST"])
def mod_login():
    if request.method == "POST":
        password = request.form.get("password", "")
        if hashlib.sha256(password.encode()).hexdigest() == app.config["MOD_PASSWORD_HASH"]:
            session['is_moderator'] = True
            return redirect(url_for("mod_panel"))
        else:
            return "Неверный пароль", 403
    return render_template("mod_login.html")


@app.route("/mod/logout")
def mod_logout():
    session.pop('is_moderator', None)
    return redirect(url_for("index"))


@app.route("/mod")
def mod_panel():
    if not is_moderator():
        return redirect(url_for("mod_login"))

    with sqlite3.connect("threads.db") as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM threads ORDER BY bump_time DESC LIMIT 100")
        threads = c.fetchall()

    return render_template("mod_panel.html", threads=threads)


@app.route("/mod/delete_thread/<int:thread_id>", methods=["POST"])
def mod_delete_thread(thread_id):
    if not is_moderator():
        abort(403)
    delete_thread(thread_id)
    return redirect(url_for("mod_panel"))


@app.route("/mod/delete_post/<int:post_id>", methods=["POST"])
def mod_delete_post(post_id):
    if not is_moderator():
        abort(403)

    with sqlite3.connect("threads.db") as conn:
        c = conn.cursor()
        # Удаляем файл поста
        c.execute("SELECT image FROM replies WHERE id=?", (post_id,))
        post = c.fetchone()
        if post and post[0]:
            file_path = os.path.join(app.config["UPLOAD_FOLDER"], post[0])
            if os.path.exists(file_path):
                os.remove(file_path)
        # Удаляем пост из БД
        c.execute("DELETE FROM replies WHERE id=?", (post_id,))
        conn.commit()

    return redirect(request.referrer or url_for("mod_panel"))


# Основные роуты
@app.route("/")
def index():
    return render_template("index.html", boards=app.config["BOARDS"])


@app.route("/<board>/")
def board(board):
    if board not in app.config["BOARDS"]:
        return redirect(url_for("index"))

    with sqlite3.connect("threads.db") as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM threads WHERE board=? ORDER BY bump_time DESC LIMIT 10", (board,))
        threads = c.fetchall()

        enriched_threads = []
        for thread in threads:
            c.execute("SELECT COUNT(*) FROM replies WHERE thread_id=?", (thread[0],))
            reply_count = c.fetchone()[0]

            c.execute("SELECT * FROM replies WHERE thread_id=? ORDER BY id ASC LIMIT 3", (thread[0],))
            last_replies = c.fetchall()

            thread_data = {
                'id': thread[0],
                'board': thread[1],
                'title': thread[2],
                'content': thread[3],
                'image': thread[4],
                'created_at': thread[5],
                'bump_time': thread[6],
                'reply_count': reply_count,
                'last_replies': last_replies
            }
            enriched_threads.append(thread_data)

    return render_template("board.html",
                           board=board,
                           board_name=app.config["BOARDS"][board],
                           threads=enriched_threads)


@app.route("/<board>/create", methods=["GET", "POST"])
def create_thread(board):
    if board not in app.config["BOARDS"]:
        return redirect(url_for("index"))

    if request.method == "POST":
        user_ip = request.remote_addr
        if not check_flood_limit(user_ip):
            return "Вы постите слишком часто! Подождите 30 секунд.", 429

        title = request.form.get("title", "")
        content = request.form["content"]
        file = request.files.get("file")
        filename = None

        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            file.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with sqlite3.connect("threads.db") as conn:
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM threads WHERE board=?", (board,))
            if c.fetchone()[0] >= app.config["MAX_THREADS_PER_BOARD"]:
                c.execute("SELECT id FROM threads WHERE board=? ORDER BY bump_time ASC LIMIT 1", (board,))
                oldest_thread_id = c.fetchone()[0]
                delete_thread(oldest_thread_id)

            c.execute("""
                INSERT INTO threads (board, title, content, image, created_at, bump_time) 
                VALUES (?,?,?,?,?,?)
            """, (board, title, content, filename, now, now))
            thread_id = c.lastrowid
            conn.commit()

        return redirect(url_for("thread", thread_id=thread_id))

    return render_template("create_thread.html", board=board)


@app.route("/thread/<int:thread_id>")
def thread(thread_id):
    with sqlite3.connect("threads.db") as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM threads WHERE id=?", (thread_id,))
        thread = c.fetchone()

        if not thread:
            return redirect(url_for("index"))

        c.execute("SELECT * FROM replies WHERE thread_id=? ORDER BY id ASC", (thread_id,))
        replies = c.fetchall()

    return render_template(
        "thread.html",
        thread=thread,
        replies=replies,
        board_name=app.config["BOARDS"].get(thread[1], ""),
        is_moderator=is_moderator()
    )



@app.route("/thread/<int:thread_id>/reply", methods=["POST"])
def reply(thread_id):
    user_ip = request.remote_addr
    if not check_flood_limit(user_ip):
        return "Вы постите слишком часто! Подождите 30 секунд.", 429

    content = request.form["content"]
    file = request.files.get("file")
    filename = None

    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        file.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with sqlite3.connect("threads.db") as conn:
        c = conn.cursor()
        c.execute("""
            INSERT INTO replies (thread_id, content, image, created_at) 
            VALUES (?,?,?,?)
        """, (thread_id, content, filename, now))

        c.execute("UPDATE threads SET bump_time=? WHERE id=?", (now, thread_id))
        conn.commit()

    return redirect(url_for("thread", thread_id=thread_id))


@app.route("/uploads/<filename>")
def uploaded_file(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)


def setup_tor_hidden_service():
    try:
        from stem.control import Controller
        tor_process = None
        try:
            with Controller.from_port(port=9051) as controller:
                controller.authenticate()
                service = controller.create_ephemeral_hidden_service(
                    {80: 5000},
                    await_publication=True
                )
                print(f"\n🔒 Onion адрес: http://{service.service_id}.onion")
        except:
            print("Запускаем Tor...")
            from stem.process import launch_tor_with_config
            tor_process = launch_tor_with_config(
                config={
                    'ControlPort': '9051',
                    'SocksPort': '9050',
                    'HiddenServiceDir': 'tor_data',
                    'HiddenServicePort': '80 127.0.0.1:5000',
                },
                init_msg_handler=lambda line: print(line) if "Bootstrapped" in line else None,
            )
            time.sleep(10)
            with Controller.from_port(port=9051) as controller:
                controller.authenticate()
                service = controller.create_ephemeral_hidden_service(
                    {80: 5000},
                    await_publication=True
                )
                print(f"\n🔒 Onion адрес: http://{service.service_id}.onion")
        return tor_process
    except Exception as e:
        print(f"\n❌ Ошибка при настройке Tor: {e}")
        return None


if __name__ == "__main__":
    init_db()  # создаём базу и таблицы
    tor_process = setup_tor_hidden_service()
    app.run(host='0.0.0.0', port=5000, debug=False)
