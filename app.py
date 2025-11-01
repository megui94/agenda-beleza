from flask import Flask, render_template, request, redirect, flash, session, url_for
import mysql.connector
from flask_bcrypt import Bcrypt
from flask_mail import Mail, Message
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature
from datetime import datetime, timezone
from functools import wraps
from flask_apscheduler import APScheduler
from threading import Thread
import traceback
import re
import os
import socket
import time
import logging
from logging.handlers import RotatingFileHandler
from dotenv import load_dotenv

# ==========================================
# 🔧 Inicialização / Configuração Base
# ==========================================
load_dotenv()
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "segredo-super-seguro")

# Logging seguro
os.makedirs("logs", exist_ok=True)
handler = RotatingFileHandler("logs/app.log", maxBytes=1_000_000, backupCount=5, encoding="utf-8")
handler.setLevel(logging.INFO)
handler.setFormatter(logging.Formatter("%(asctime)s — %(levelname)s — %(message)s"))
app.logger.addHandler(handler)
app.logger.setLevel(logging.INFO)
app.logger.info("🪶 Logging iniciado.")

# ==========================================
# 📧 Configuração do Flask-Mail (Brevo SMTP)
# ==========================================
app.config['MAIL_SERVER'] = os.getenv('MAIL_SERVER')
app.config['MAIL_PORT'] = int(os.getenv('MAIL_PORT', 587))
app.config['MAIL_USE_TLS'] = os.getenv('MAIL_USE_TLS', 'true').lower() == 'true'
app.config['MAIL_USE_SSL'] = os.getenv('MAIL_USE_SSL', 'false').lower() == 'true'
app.config['MAIL_USERNAME'] = os.getenv('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD')
app.config['MAIL_DEFAULT_SENDER'] = os.getenv('MAIL_DEFAULT_SENDER')

mail = Mail(app)

# ==========================================
# 📬 Sistema de Envio de E-mails (assíncrono)
# ==========================================
def _send_async(app, msg):
    with app.app_context():
        try:
            mail.send(msg)
            app.logger.info(f"📨 E-mail enviado com sucesso → {msg.recipients}")
        except Exception as e:
            app.logger.error(f"❌ Falha ao enviar e-mail: {type(e).__name__}: {e}")

def send_email(subject, recipients, html, reply_to=None):
    """Função genérica e compatível com Brevo SMTP"""
    sender_email = app.config.get("MAIL_DEFAULT_SENDER") or app.config.get("MAIL_USERNAME")
    msg = Message(
        subject=subject,
        recipients=recipients,
        sender=("Agenda de Beleza 💅", sender_email),
        html=html
    )
    if reply_to:
        msg.reply_to = reply_to
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

        html = render_template("emails/clientes/reset_email.html", nome=user[1], reset_link=reset_link)
        send_email("🔐 Recuperação de Senha • Agenda Beleza", [email], html)
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
# 🗓️ Marcações + E-mails
# ==========================================
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

            cur.execute("SELECT Nome FROM Servicos WHERE Id = %s", (servico_id,))
            servico_nome = cur.fetchone()[0]

            html_cliente = render_template("emails/clientes/confirmacao_email.html",
                                           nome=session["nome"],
                                           datahora=datahora_obj.strftime("%d/%m/%Y %H:%M"),
                                           servico=servico_nome)
            send_email("🗓️ Marcação registada com sucesso", [session["email"]], html_cliente)

            html_admin = render_template("emails/admin/nova_marcacao_admin.html",
                                         nome_cliente=session["nome"],
                                         servico=servico_nome,
                                         datahora=datahora_obj.strftime("%d/%m/%Y %H:%M"),
                                         observacoes=observacoes)
            send_email("📢 Nova marcação pendente", [os.getenv("MAIL_USERNAME")], html_admin, reply_to=session["email"])

            conn.close()
            flash("Marcação enviada com sucesso!", "success")
        except Exception as e:
            app.logger.error(f"Erro ao criar marcação: {e}")
        return redirect(url_for("minhas_marcacoes"))

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT Id, Nome FROM Servicos")
    servicos = cur.fetchall()
    conn.close()
    return render_template("marcacoes.html", servicos=servicos)

# ==========================================
# ⏰ Lembretes automáticos
# ==========================================
def enviar_lembretes():
    """Envia e-mails automáticos 1 hora antes da marcação"""
    try:
        app.logger.info("🕐 Verificando marcações para lembrete...")
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT m.Id, u.Nome, u.Email, s.Nome, m.DataHora
            FROM Marcacoes m
            JOIN Utilizador u ON m.Cliente_id = u.Id
            JOIN Servicos s ON m.Servico_id = s.Id
            WHERE m.Estado = 'Aprovado'
              AND TIMESTAMPDIFF(MINUTE, NOW(), m.DataHora) BETWEEN 55 AND 65
              AND (m.LembreteEnviado IS NULL OR m.LembreteEnviado = 0)
        """)
        marcacoes = cur.fetchall()

        for m in marcacoes:
            id_marcacao, nome, email, servico, datahora = m
            html = render_template(
                "emails/clientes/lembrete_email.html",
                nome=nome,
                servico=servico,
                datahora=datahora.strftime("%d/%m/%Y %H:%M"),
                current_year=datetime.utcnow().year
            )
            send_email("⏰ Lembrete de Marcação • Agenda Beleza", [email], html)
            cur.execute("UPDATE Marcacoes SET LembreteEnviado = 1 WHERE Id = %s", (id_marcacao,))
            conn.commit()

        conn.close()
        app.logger.info(f"📨 {len(marcacoes)} lembretes enviados com sucesso!")
    except Exception as e:
        app.logger.error(f"❌ Erro ao enviar lembretes: {e}")

# ==========================================
# ▶️ Run + Scheduler
# ==========================================
scheduler = APScheduler()
scheduler.init_app(app)
scheduler.start()
scheduler.add_job(id='lembretes_marcacoes', func=enviar_lembretes, trigger='interval', minutes=5)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=False)
