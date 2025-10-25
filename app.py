from flask import Flask, render_template, request, redirect, flash, session, url_for
import mysql.connector
from flask_bcrypt import Bcrypt
from flask_mail import Mail, Message
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature
from datetime import datetime, timezone, timedelta
from functools import wraps
from dotenv import load_dotenv
from flask_apscheduler import APScheduler
from threading import Thread
import traceback
import re
import os
import socket
import time
import logging
from logging.handlers import RotatingFileHandler

# ==========================================
# 🔧 Inicialização / Configuração Base
# ==========================================
load_dotenv()
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "segredo-super-seguro")

# Logging seguro (sem emojis, compatível Windows)
os.makedirs("logs", exist_ok=True)
handler = RotatingFileHandler("logs/app.log", maxBytes=1_000_000, backupCount=5, encoding="utf-8")
handler.setLevel(logging.INFO)
handler.setFormatter(logging.Formatter("%(asctime)s — %(levelname)s — %(message)s"))
app.logger.addHandler(handler)
app.logger.setLevel(logging.INFO)
app.logger.info("🪶 Logging iniciado.")

# ==========================================
# 📬 Configuração de E-mail (Gmail / Sendinblue)
# ==========================================
app.config.update(
    MAIL_SERVER=os.getenv("MAIL_SERVER", "smtp.gmail.com"),
    MAIL_PORT=int(os.getenv("MAIL_PORT", 587)),
    MAIL_USE_TLS=os.getenv("MAIL_USE_TLS", "true").lower() == "true",
    MAIL_USE_SSL=os.getenv("MAIL_USE_SSL", "false").lower() == "true",
    MAIL_USERNAME=os.getenv("MAIL_USERNAME"),
    MAIL_PASSWORD=os.getenv("MAIL_PASSWORD"),
    MAIL_DEFAULT_SENDER=("Agenda Beleza", os.getenv("MAIL_USERNAME")),
)
mail = Mail(app)

def _send_async(app, msg):
    """Envia e-mails em thread paralela."""
    with app.app_context():
        try:
            mail.send(msg)
            app.logger.info(f"📧 E-mail enviado para {msg.recipients}")
        except Exception as e:
            app.logger.error(f"⚠️ Erro ao enviar e-mail: {e}")

def send_email(subject, recipients, html, reply_to=None):
    msg = Message(subject=subject, recipients=recipients)
    if reply_to:
        msg.reply_to = reply_to
    msg.html = html
    Thread(target=_send_async, args=(app, msg), daemon=True).start()

# ==========================================
# 💾 Conexão MySQL (Render / Aiven)
# ==========================================
def get_db_connection():
    from mysql.connector import Error
    host = os.getenv("MYSQL_HOST")
    port = int(os.getenv("MYSQL_PORT", "3306"))
    user = os.getenv("MYSQL_USER")
    password = os.getenv("MYSQL_PASSWORD")
    database = os.getenv("MYSQL_DB")
    ca_path = os.getenv("MYSQL_SSL_CA")

    if ca_path and not os.path.exists(ca_path):
        if os.path.exists("ca.pem"):
            ca_path = "ca.pem"
            app.logger.info("🔒 A usar certificado local ca.pem")
        else:
            ca_path = None
            app.logger.warning("⚠️ CA não encontrada — conexão sem SSL.")

    ssl_config = {"ssl_ca": ca_path} if ca_path else {}

    try:
        ip = socket.gethostbyname(host)
        app.logger.info(f"Host {host} → {ip}")
    except Exception as e:
        app.logger.error(f"Erro DNS: {e}")
        raise

    for i in range(3):
        try:
            conn = mysql.connector.connect(
                host=host,
                port=port,
                user=user,
                password=password,
                database=database,
                connection_timeout=10,
                **ssl_config
            )
            if conn.is_connected():
                return conn
        except Error as err:
            app.logger.warning(f"Tentativa {i+1}/3 falhou: {err}")
            time.sleep(2)
    raise Exception("❌ Não foi possível conectar ao MySQL.")

# ==========================================
# 🔐 Segurança, Utils e Sessão
# ==========================================
bcrypt = Bcrypt(app)
serializer = URLSafeTimedSerializer(app.secret_key)

@app.context_processor
def inject_globals():
    return {
        "current_year": datetime.now(timezone.utc).year,
        "is_admin": bool(session.get("is_admin", False))
    }

@app.errorhandler(500)
def internal_error(error):
    app.logger.error(traceback.format_exc())
    return "Ocorreu um erro interno no servidor.", 500

# ==========================================
# 👥 Autenticação e Utilizadores
# ==========================================
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT Id, Password, Nome, IsAdmin FROM Utilizador WHERE Email=%s", (email,))
        user = cur.fetchone()
        conn.close()

        if user and bcrypt.check_password_hash(user[1], password):
            session.update({
                "user_id": user[0],
                "email": email,
                "nome": user[2],
                "is_admin": bool(user[3])
            })
            flash(f"Bem-vindo(a), {user[2]}!", "success")
            next_page = session.pop("next", None)
            return redirect(url_for(next_page)) if next_page else redirect(url_for("index"))
        flash("E-mail ou senha incorretos.", "error")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    flash("Saiu da conta com sucesso.", "info")
    return redirect(url_for("index"))

@app.route("/registar", methods=["GET", "POST"])
def registar():
    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        email = request.form.get("email", "").strip().lower()
        telefone = request.form.get("telefone", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")

        if password != confirm:
            flash("As senhas não coincidem.", "error")
            return redirect(url_for("registar"))

        if not re.match(r"^(?=.*[A-Z])(?=.*\d).{8,}$", password):
            flash("A senha deve ter 8+ caracteres, 1 maiúscula e 1 número.", "error")
            return redirect(url_for("registar"))

        hashed_pw = bcrypt.generate_password_hash(password).decode("utf-8")
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO Utilizador (Nome, Email, Telefone, Password) VALUES (%s,%s,%s,%s)",
                (nome, email, telefone, hashed_pw)
            )
            conn.commit()
            flash("Conta criada com sucesso!", "success")
        except Exception as e:
            flash(f"Erro ao criar conta: {e}", "error")
        finally:
            conn.close()
        return redirect(url_for("login"))
    return render_template("registar.html")

# ==========================================
# 🔄 Recuperação de Senha
# ==========================================
@app.route("/reset_request", methods=["GET", "POST"])
def reset_request():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT Id, Nome FROM Utilizador WHERE Email = %s", (email,))
        user = cur.fetchone()
        conn.close()

        if not user:
            flash("E-mail não encontrado.", "error")
            return render_template("reset_request.html")

        token = serializer.dumps(email, salt="reset-salt")
        reset_link = url_for("reset_token", token=token, _external=True)

        html = render_template("emails/reset_email.html", nome=user[1], reset_link=reset_link)
        send_email("Redefinição de senha • Agenda Beleza", [email], html)
        flash("Enviámos um link de redefinição.", "info")
        return redirect(url_for("login"))
    return render_template("reset_request.html")

@app.route("/reset/<token>", methods=["GET", "POST"])
def reset_token(token):
    try:
        email = serializer.loads(token, salt="reset-salt", max_age=3600)
    except (SignatureExpired, BadSignature):
        flash("O link expirou ou é inválido.", "error")
        return redirect(url_for("reset_request"))

    if request.method == "POST":
        nova = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        if nova != confirm:
            flash("As senhas não coincidem.", "error")
            return redirect(url_for("reset_token", token=token))

        hashed = bcrypt.generate_password_hash(nova).decode("utf-8")
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("UPDATE Utilizador SET Password=%s WHERE Email=%s", (hashed, email))
        conn.commit()
        conn.close()
        flash("Senha atualizada com sucesso!", "success")
        return redirect(url_for("login"))
    return render_template("reset_token.html", token=token)

# ==========================================
# 🗓️ Marcações
# ==========================================
@app.route("/agendar")
def agendar_redirect():
    if not session.get("user_id"):
        flash("Faça login para agendar a sua marcação.", "info")
        session["next"] = "marcacoes"
        return redirect(url_for("login"))
    return redirect(url_for("marcacoes"))

@app.route("/marcacoes", methods=["GET", "POST"])
def marcacoes():
    if request.method == "POST":
        if not session.get("user_id"):
            flash("Inicie sessão para fazer uma marcação.", "error")
            return redirect(url_for("login"))

        servico_id = request.form.get("servico_id")
        datahora = request.form.get("datahora")
        observacoes = request.form.get("observacoes", "")

        if not servico_id or not datahora:
            flash("Selecione um serviço e horário.", "error")
            return redirect(url_for("marcacoes"))

        try:
            datahora_obj = datetime.strptime(datahora, "%Y-%m-%dT%H:%M")
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO Marcacoes (Cliente_id, Servico_id, DataHora, Estado, Observacoes)
                VALUES (%s,%s,%s,'Pendente',%s)
            """, (session["user_id"], servico_id, datahora_obj, observacoes))
            conn.commit()
            conn.close()

            html_cliente = render_template("emails/confirmacao_marcacao.html", nome=session["nome"], datahora=datahora_obj)
            send_email("🗓️ Marcação registada", [session["email"]], html_cliente)

            html_admin = render_template("emails/nova_marcacao_admin.html", nome_cliente=session["nome"], servico=servico_id, datahora=datahora_obj, observacoes=observacoes)
            send_email("📢 Nova marcação pendente", [os.getenv("MAIL_USERNAME")], html_admin, reply_to=session["email"])

            flash("Marcação enviada com sucesso!", "success")
        except Exception as e:
            app.logger.error(f"Erro ao criar marcação: {e}")
            flash("Erro ao criar marcação.", "error")
        return redirect(url_for("minhas_marcacoes"))

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT Id, Nome FROM Servicos")
    servicos = cur.fetchall()
    conn.close()
    return render_template("marcacoes.html", servicos=servicos)

@app.route("/minhas_marcacoes")
def minhas_marcacoes():
    if not session.get("user_id"):
        flash("Inicie sessão para aceder às suas marcações.", "error")
        return redirect(url_for("login"))

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT m.Id, s.Nome, m.DataHora, m.Estado, m.Observacoes
        FROM Marcacoes m
        JOIN Servicos s ON m.Servico_id = s.Id
        WHERE m.Cliente_id = %s
        ORDER BY m.DataHora DESC
    """, (session["user_id"],))
    marcacoes = cur.fetchall()
    conn.close()
    return render_template("minhas_marcacoes.html", marcacoes=marcacoes)

# ==========================================
# 🌐 Páginas Gerais
# ==========================================
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/sobre")
def sobre():
    return render_template("sobre.html")

@app.route("/servicos")
def servicos():
    termo = (request.args.get("q") or "").strip()
    conn = get_db_connection()
    cur = conn.cursor()
    if termo:
        cur.execute("SELECT * FROM Servicos WHERE Nome LIKE %s OR Descricao LIKE %s", (f"%{termo}%", f"%{termo}%"))
    else:
        cur.execute("SELECT * FROM Servicos")
    servs = cur.fetchall()
    conn.close()
    return render_template("servicos.html", servicos=servs)

# ==========================================
# ▶️ Run
# ==========================================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=False)
