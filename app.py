import os
import sqlite3
from flask import Flask, render_template, request, redirect, url_for, send_from_directory
from datetime import datetime
from werkzeug.utils import secure_filename

app = Flask(__name__)
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


def init_db():
    with sqlite3.connect("threads.db") as conn:
        c = conn.cursor()
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
        conn.commit()


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in app.config["ALLOWED_EXTENSIONS"]


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
            # Получаем количество ответов
            c.execute("SELECT COUNT(*) FROM replies WHERE thread_id=?", (thread[0],))
            reply_count = c.fetchone()[0]

            # Получаем 3 последних ответа
            c.execute("""
                SELECT * FROM replies 
                WHERE thread_id=? 
                ORDER BY id ASC 
                LIMIT 3
            """, (thread[0],))
            last_replies = c.fetchall()

            # Создаем кортеж с данными треда и ответами
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

        c.execute("UPDATE threads SET bump_time=? WHERE id=?",
                  (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), thread_id))
        conn.commit()

    return render_template("thread.html",
                           thread=thread,
                           replies=replies,
                           board_name=app.config["BOARDS"].get(thread[1], ""))


@app.route("/thread/<int:thread_id>/reply", methods=["POST"])
def reply(thread_id):
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

        # Убедитесь что Tor работает с контрольным портом
        tor_process = None
        try:
            # Пробуем подключиться к уже запущенному Tor
            with Controller.from_port(port=9051) as controller:
                controller.authenticate()
                service = controller.create_ephemeral_hidden_service(
                    {80: 5000},
                    await_publication=True
                )
                print(f"\n🔒 Onion адрес: http://{service.service_id}.onion")
        except:
            # Если Tor не запущен, запускаем его
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

            # Даём Tor время на запуск
            import time
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
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    init_db()

    # Запускаем Tor скрытый сервис
    tor_process = setup_tor_hidden_service()

    try:
        # Запускаем Flask
        app.run(host='0.0.0.0', port=5000, debug=False)
    finally:
        # Гарантированно останавливаем Tor при завершении
        if tor_process:
            tor_process.terminate()