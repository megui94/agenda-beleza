from flask import Flask, render_template, request, redirect, flash, session, url_for
import mysql.connector
from flask_bcrypt import Bcrypt
from flask_mail import Mail, Message
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature
from datetime import datetime, timezone
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
serializer = URLSafeTimedSerializer(app.secret_key)

# =========================
# 🔍 Verificação do Brevo API
# =========================
if not os.getenv("BREVO_API_KEY"):
    print("❌ ERRO: A variável de ambiente 'BREVO_API_KEY' não está definida!")
    print("➡️  Vá até Render → Settings → Environment e adicione:")
    print("   BREVO_API_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
    exit(1)
else:
    print("✅ BREVO_API_KEY encontrado — envio de e-mails via Brevo ativado.")


# Logging (ficheiro)
os.makedirs("logs", exist_ok=True)
handler = RotatingFileHandler("logs/app.log", maxBytes=1_000_000, backupCount=5, encoding="utf-8")
handler.setLevel(logging.INFO)
handler.setFormatter(logging.Formatter("%(asctime)s — %(levelname)s — %(message)s"))
app.logger.addHandler(handler)
app.logger.setLevel(logging.INFO)
app.logger.info("Logging iniciado.")

# ==========================================
# 📧 Flask-Mail (Brevo SMTP)
# ==========================================
app.config['MAIL_SERVER'] = os.getenv('MAIL_SERVER', 'smtp-relay.brevo.com')
app.config['MAIL_PORT'] = int(os.getenv('MAIL_PORT', 587))
app.config['MAIL_USE_TLS'] = os.getenv('MAIL_USE_TLS', 'true').lower() == 'true'
app.config['MAIL_USE_SSL'] = os.getenv('MAIL_USE_SSL', 'false').lower() == 'true'
app.config['MAIL_USERNAME'] = os.getenv('MAIL_USERNAME')  # e.g. agenda.beleza.contato@gmail.com ou user do Brevo
app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD')  # chave SMTP Brevo
app.config['MAIL_DEFAULT_SENDER'] = os.getenv('MAIL_DEFAULT_SENDER')  # e.g. no-reply@teudominio.pt

mail = Mail(app)

def _send_async(app, msg):
    with app.app_context():
        try:
            mail.send(msg)
            app.logger.info(f"[MAIL] enviado para {msg.recipients}")
        except Exception as e:
            app.logger.error(f"[MAIL] falhou: {type(e).__name__}: {e}")

import sib_api_v3_sdk
from sib_api_v3_sdk.rest import ApiException

def send_email(subject, recipients, html_body, reply_to=None):
    """Envia e-mails via API do Brevo (sem SMTP)"""
    api_key = os.getenv("BREVO_API_KEY")
    sender_email = os.getenv("MAIL_DEFAULT_SENDER", "agenda.beleza.contato@gmail.com")

    if not api_key:
        app.logger.error("[BREVO] Falha: BREVO_API_KEY não configurada.")
        return

    # Inicializar configuração da API
    configuration = sib_api_v3_sdk.Configuration()
    configuration.api_key['api-key'] = os.getenv("BREVO_API_KEY")
    api_instance = sib_api_v3_sdk.TransactionalEmailsApi(sib_api_v3_sdk.ApiClient(configuration))
    print("API Key:", os.getenv("BREVO_API_KEY"))

    # Preparar mensagem
    sender = {"name": "Agenda de Beleza 💅", "email": sender_email}
    to_list = [{"email": r} for r in recipients]

    email_data = sib_api_v3_sdk.SendSmtpEmail(
        to=to_list,
        sender=sender,
        subject=subject,
        html_content=html_body,
        reply_to={"email": reply_to} if reply_to else None,
    )

    try:
        response = api_instance.send_transac_email(email_data)
        app.logger.info(f"[BREVO] E-mail enviado com sucesso → {recipients}")
    except ApiException as e:
        app.logger.error(f"[BREVO] Erro ao enviar e-mail: {e}")


# ==========================================
# 💾 MySQL (Render / Aiven)
# ==========================================
def get_db_connection():
    from mysql.connector import Error
    host = os.getenv("MYSQL_HOST")
    port = int(os.getenv("MYSQL_PORT", "3306"))
    user = os.getenv("MYSQL_USER")
    password = os.getenv("MYSQL_PASSWORD")
    database = os.getenv("MYSQL_DB")
    ca_path = os.getenv("MYSQL_SSL_CA")

    ssl_config = {}
    if ca_path:
        if os.path.exists(ca_path):
            ssl_config = {"ssl_ca": ca_path}
        elif os.path.exists("ca.pem"):
            ssl_config = {"ssl_ca": "ca.pem"}
            app.logger.info("A usar certificado local ca.pem")
        else:
            app.logger.warning("CA não encontrada — a ligar sem SSL.")

    try:
        ip = socket.gethostbyname(host)
        app.logger.info(f"MySQL host {host} → {ip}")
    except Exception as e:
        app.logger.error(f"Erro DNS: {e}")
        raise

    for i in range(3):
        try:
            conn = mysql.connector.connect(
                host=host, port=port, user=user, password=password,
                database=database, connection_timeout=10, **ssl_config
            )
            if conn.is_connected():
                return conn
        except Error as err:
            app.logger.warning(f"Tentativa MySQL {i+1}/3 falhou: {err}")
            time.sleep(2)
    raise Exception("Não foi possível conectar ao MySQL.")

# ==========================================
# 🔐 Segurança, utils e sessão
# ==========================================
bcrypt = Bcrypt(app)
serializer = URLSafeTimedSerializer(app.secret_key)

@app.context_processor
def inject_globals():
    return {"current_year": datetime.now(timezone.utc).year, "is_admin": bool(session.get("is_admin", False))}

@app.errorhandler(500)
def internal_error(_):
    app.logger.error(traceback.format_exc())
    return "Ocorreu um erro interno no servidor.", 500

@app.context_processor
def inject_user():
    return dict(is_admin=session.get("is_admin", False))


# ==========================================
# 👥 Autenticação
# ==========================================
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT Id, Password, Nome, IsAdmin, EmailVerificado FROM Utilizador WHERE Email=%s", (email,))
        user = cur.fetchone()
        conn.close()

        if user and bcrypt.check_password_hash(user[1], password):
            # 🛑 Verificar se o e-mail foi confirmado
            if not user[4]:
                flash("Por favor, confirme o seu e-mail antes de iniciar sessão.", "warning")
                return redirect(url_for("login"))

            # ✅ Login autorizado
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
        nome = (request.form.get("nome") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        telefone = (request.form.get("telefone") or "").strip()
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm_password") or ""

        if password != confirm:
            flash("As senhas não coincidem.", "error"); return redirect(url_for("registar"))
        if not re.match(r"^(?=.*[A-Z])(?=.*\d).{8,}$", password):
            flash("A senha deve ter 8+ caracteres, 1 maiúscula e 1 número.", "error"); return redirect(url_for("registar"))

        hashed_pw = bcrypt.generate_password_hash(password).decode("utf-8")

        try:
            conn = get_db_connection(); cur = conn.cursor()
            cur.execute(
                "INSERT INTO Utilizador (Nome, Email, Telefone, Password) VALUES (%s,%s,%s,%s)",
                (nome, email, telefone, hashed_pw)
            )
            conn.commit()

            # 🔹 GERAR LINK DE CONFIRMAÇÃO
            token = serializer.dumps(email, salt="email-confirm")
            link = url_for("confirmar_email", token=token, _external=True)

            # ========================
            # ✉️ E-MAIL PARA O CLIENTE
            # ========================
            html_cliente = f"""
            <div style="background-color:#fff9fb;font-family:'Poppins',Arial,sans-serif;padding:40px 0;text-align:center;">
              <div style="max-width:600px;margin:0 auto;background:white;border-radius:20px;padding:40px;box-shadow:0 8px 25px rgba(246,182,194,0.3);">
                <h1 style="color:#a6487a;font-size:28px;margin-bottom:10px;">🌸 Bem-vinda à Agenda Beleza!</h1>
                <p style="color:#444;font-size:16px;line-height:1.6;margin-bottom:25px;">
                  Olá, <b>{nome}</b>!<br>
                  Obrigada por se registar na <b>Agenda Beleza</b>.<br>
                  Para ativar a sua conta, confirme o seu endereço de e-mail clicando no botão abaixo:
                </p>

                <a href="{link}" style="
                  background-color:#ff9ac8;
                  color:white;
                  padding:14px 26px;
                  border-radius:40px;
                  font-weight:600;
                  text-decoration:none;
                  display:inline-block;
                  margin-bottom:30px;
                  transition:background-color 0.3s ease;">
                  Confirmar E-mail
                </a>

                <p style="color:#666;font-size:14px;margin-top:10px;">
                  Este link é válido por <b>30 minutos</b>.<br>
                  Se não criou esta conta, ignore este e-mail.
                </p>

                <hr style="margin:35px 0;border:none;border-top:1px solid #ffe3ef;">

                <p style="font-size:13px;color:#999;">
                  💕 Agenda Beleza · Olhão, Portugal<br>
                  <a href="mailto:agenda.beleza.contato@gmail.com" style="color:#a6487a;text-decoration:none;">agenda.beleza.contato@gmail.com</a>
                </p>
              </div>
            </div>
            """
            send_email("📧 Confirme o seu e-mail • Agenda Beleza", [email], html_cliente)

            # ========================
            # 💌 E-MAIL PARA O ADMIN
            # ========================
            html_admin = f"""
            <div style="background-color:#fff9fb;font-family:'Poppins',Arial,sans-serif;padding:40px 0;text-align:center;">
              <div style="max-width:600px;margin:0 auto;background:white;border-radius:20px;padding:40px;box-shadow:0 8px 25px rgba(246,182,194,0.3);">
                <h1 style="color:#a6487a;font-size:26px;margin-bottom:10px;">
                  👤 Nova Utilizadora Registada
                </h1>

                <p style="color:#444;font-size:16px;line-height:1.6;margin-bottom:25px;">
                  Uma nova cliente acabou de criar uma conta na <b>Agenda Beleza</b>.<br>
                  Aqui estão os detalhes do registo:
                </p>

                <div style="text-align:left;background:#fff4f8;border-radius:14px;padding:18px 24px;margin:0 auto;max-width:420px;border:1px solid #ffd6e5;">
                  <p style="margin:8px 0;font-size:15px;color:#333;"><b>👩 Nome:</b> {nome}</p>
                  <p style="margin:8px 0;font-size:15px;color:#333;"><b>📧 E-mail:</b> {email}</p>
                  <p style="margin:8px 0;font-size:15px;color:#333;"><b>📞 Telefone:</b> {telefone or 'Não informado'}</p>
                  <p style="margin:8px 0;font-size:15px;color:#333;"><b>📅 Data de Registo:</b> {datetime.now().strftime("%d/%m/%Y %H:%M")}</p>
                </div>

                <a href="https://agenda-beleza.onrender.com/admin_marcacoes" style="
                  background-color:#ff9ac8;
                  color:white;
                  padding:12px 24px;
                  border-radius:40px;
                  font-weight:600;
                  text-decoration:none;
                  display:inline-block;
                  margin:32px 0;
                  transition:background-color 0.3s ease;">
                  Abrir Painel Administrativo
                </a>

                <p style="color:#666;font-size:14px;">
                  Este é um alerta automático. Não é necessário responder.
                </p>

                <hr style="margin:35px 0;border:none;border-top:1px solid #ffe3ef;">
                <p style="font-size:13px;color:#999;">
                  💕 Agenda Beleza · Olhão, Portugal<br>
                  <a href="mailto:agenda.beleza.contato@gmail.com" style="color:#a6487a;text-decoration:none;">agenda.beleza.contato@gmail.com</a>
                </p>
              </div>
            </div>
            """
            send_email("👤 Nova cliente registada • Agenda Beleza", ["agenda.beleza.contato@gmail.com"], html_admin)

            flash("Conta criada com sucesso! Verifique o seu e-mail para confirmar.", "success")

        except Exception as e:
            flash(f"Erro ao criar conta: {e}", "error")
        finally:
            conn.close()

        return redirect(url_for("login"))

    return render_template("registar.html")


@app.route("/confirmar_email/<token>")
def confirmar_email(token):
    try:
        email = serializer.loads(token, salt="email-confirm", max_age=1800)
    except SignatureExpired:
        flash("O link de confirmação expirou. Faça login e solicite novo envio.", "error")
        return redirect(url_for("login"))
    except BadSignature:
        flash("Link de confirmação inválido.", "error")
        return redirect(url_for("login"))

    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("UPDATE Utilizador SET EmailVerificado = 1 WHERE Email = %s", (email,))
    conn.commit(); conn.close()

    flash("E-mail confirmado com sucesso! Já pode iniciar sessão.", "success")
    return redirect(url_for("login"))

# ==========================================
# 🔄 Recuperação de senha
# ==========================================
@app.route("/reset_request", methods=["GET", "POST"])
def reset_request():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        conn = get_db_connection(); cur = conn.cursor()
        cur.execute("SELECT Id, Nome FROM Utilizador WHERE Email=%s", (email,))
        user = cur.fetchone(); conn.close()

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
        email = serializer.loads(token, salt="reset-salt", max_age=1800)
    except (SignatureExpired, BadSignature):
        flash("O link expirou ou é inválido.", "error")
        return redirect(url_for("reset_request"))

    if request.method == "POST":
        nova = request.form.get("password") or ""
        confirm = request.form.get("confirm_password") or ""
        if nova != confirm:
            flash("As senhas não coincidem.", "error")
            return redirect(url_for("reset_token", token=token))

        hashed = bcrypt.generate_password_hash(nova).decode("utf-8")
        conn = get_db_connection(); cur = conn.cursor()
        cur.execute("UPDATE Utilizador SET Password=%s WHERE Email=%s", (hashed, email))
        conn.commit(); conn.close()
        flash("Senha atualizada com sucesso!", "success")
        return redirect(url_for("login"))
    return render_template("reset_token.html", token=token)

# ==========================================
# 🗓️ Agendamentos (cliente)
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
            conn = get_db_connection(); cur = conn.cursor()
            cur.execute("""
                INSERT INTO Marcacoes (Cliente_id, Servico_id, DataHora, Estado, Observacoes)
                VALUES (%s,%s,%s,'Pendente',%s)
            """, (session["user_id"], servico_id, datahora_obj, observacoes))
            conn.commit()

            # Nome do serviço
            cur.execute("SELECT Nome FROM Servicos WHERE Id=%s", (servico_id,))
            servico_nome = (cur.fetchone() or ["—"])[0]

            # Email para o cliente
            html_cliente = render_template(
                "emails/clientes/confirmacao_email.html",
                nome=session.get("nome", "Cliente"),
                datahora=datahora_obj.strftime("%d/%m/%Y %H:%M"),
                servico=servico_nome
            )
            send_email("🗓️ Marcação registada com sucesso", [session["email"]], html_cliente)

            # Email para admin
            html_admin = render_template(
                "emails/admin/nova_marcacao_admin.html",
                nome_cliente=session.get("nome", "Cliente"),
                servico=servico_nome,
                datahora=datahora_obj.strftime("%d/%m/%Y %H:%M"),
                observacoes=observacoes
            )
            admin_email = os.getenv("ADMIN_EMAIL", os.getenv("MAIL_DEFAULT_SENDER"))
            send_email("📢 Nova marcação pendente", [admin_email], html_admin, reply_to=session["email"])

            conn.close()
            if "marcacao_sucesso" not in session:
                flash("Marcação enviada com sucesso!", "success")
                session["marcacao_sucesso"] = True
        except Exception as e:
            app.logger.error(f"Erro ao criar marcação: {e}")
            flash("Ocorreu um erro ao criar a marcação.", "error")
        return redirect(url_for("minhas_marcacoes"))

    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT Id, Nome FROM Servicos")
    servicos = cur.fetchall(); conn.close()
    return render_template("marcacoes.html", servicos=servicos)

@app.route("/minhas_marcacoes")
def minhas_marcacoes():
    if not session.get("user_id"):
        session.pop("marcacao_sucesso", None)
        flash("Inicie sessão para aceder às suas marcações.", "error")
        return redirect(url_for("login"))

    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("""
        SELECT m.Id, s.Nome, m.DataHora, m.Estado, m.Observacoes
        FROM Marcacoes m
        JOIN Servicos s ON m.Servico_id = s.Id
        WHERE m.Cliente_id = %s
        ORDER BY m.DataHora DESC
    """, (session["user_id"],))
    marcacoes = cur.fetchall(); conn.close()
    return render_template("minhas_marcacoes.html", marcacoes=marcacoes)

# ==========================================
# 👩‍💼 Admin — gestão de marcações
# ==========================================
@app.route("/admin/marcacoes", methods=["GET"])
def admin_marcacoes():
    if not session.get("is_admin"):
        flash("Acesso restrito: apenas administradores.", "error")
        return redirect(url_for("index"))

    estado = request.args.get("estado", "")
    termo = request.args.get("q", "")

    conn = get_db_connection(); cur = conn.cursor()
    query = """
        SELECT m.Id, u.Nome, s.Nome, m.DataHora, m.Estado, m.Observacoes, u.Email
        FROM Marcacoes m
        JOIN Utilizador u ON m.Cliente_id = u.Id
        JOIN Servicos s ON m.Servico_id = s.Id
        WHERE 1=1
    """
    params = []
    if estado:
        query += " AND m.Estado = %s"; params.append(estado)
    if termo:
        query += " AND (u.Nome LIKE %s OR s.Nome LIKE %s)"; params += [f"%{termo}%", f"%{termo}%"]
    query += " ORDER BY m.DataHora DESC"

    cur.execute(query, tuple(params))
    marcacoes = cur.fetchall(); conn.close()
    return render_template("admin_marcacoes.html", marcacoes=marcacoes)

@app.route("/admin/update_marcacao", methods=["POST"])
def admin_update_marcacao():
    if not session.get("is_admin"):
        flash("Acesso restrito.", "error")
        return redirect(url_for("index"))

    marcacao_id = request.form.get("id")
    acao = request.form.get("acao")  # "Aprovado" ou "Rejeitado"

    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("""
        SELECT m.Id, u.Nome, u.Email, s.Nome, m.DataHora
        FROM Marcacoes m
        JOIN Utilizador u ON m.Cliente_id = u.Id
        JOIN Servicos s ON m.Servico_id = s.Id
        WHERE m.Id = %s
    """, (marcacao_id,))
    info = cur.fetchone()

    if not info:
        conn.close()
        flash("Marcação não encontrada.", "error")
        return redirect(url_for("admin_marcacoes"))

    cur.execute("UPDATE Marcacoes SET Estado = %s WHERE Id = %s", (acao, marcacao_id))
    conn.commit(); conn.close()

    # Email ao cliente
    nome_cliente, email_cliente, servico, datahora_str = info[1], info[2], info[3], info[4].strftime("%d/%m/%Y %H:%M")
    if acao == "Aprovado":
        html = render_template("emails/clientes/marcacao_aprovada.html", nome=nome_cliente, servico=servico, datahora=datahora_str)
        assunto = "✅ Marcação Aprovada • Agenda Beleza"
    else:
        html = render_template("emails/clientes/marcacao_rejeitada.html", nome=nome_cliente, servico=servico, datahora=datahora_str)
        assunto = "❌ Marcação Rejeitada • Agenda Beleza"
    send_email(assunto, [email_cliente], html)

    flash(f"Marcação {acao.lower()} com sucesso!", "success")
    return redirect(url_for("admin_marcacoes"))

@app.route("/admin/mensagens")
def admin_mensagens():
    if not session.get("is_admin"):
        flash("Acesso restrito à administração.", "error")
        return redirect(url_for("index"))

    q = (request.args.get("q") or "").strip()

    conn = get_db_connection()
    cur = conn.cursor()

    if q:
        cur.execute("""
            SELECT Id, Nome, Email, Assunto, Mensagem, DataEnvio
            FROM MensagensContato
            WHERE Nome LIKE %s OR Email LIKE %s OR Assunto LIKE %s
            ORDER BY DataEnvio DESC
        """, (f"%{q}%", f"%{q}%", f"%{q}%"))
    else:
        cur.execute("""
            SELECT Id, Nome, Email, Assunto, Mensagem, DataEnvio
            FROM MensagensContato
            ORDER BY DataEnvio DESC
        """)
    mensagens = cur.fetchall()
    conn.close()

    return render_template("admin_mensagens.html", mensagens=mensagens, q=q)



@app.route("/admin/mensagens/eliminar/<int:id>", methods=["POST"])
def admin_eliminar_mensagem(id):
    """Permite ao administrador apagar uma mensagem de contacto."""
    if not session.get("is_admin"):
        flash("Acesso restrito à administração.", "error")
        return redirect(url_for("index"))

    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM MensagensContato WHERE Id = %s", (id,))
        conn.commit()
        conn.close()
        flash("Mensagem eliminada com sucesso!", "success")
    except Exception as e:
        app.logger.error(f"Erro ao eliminar mensagem: {e}")
        flash("Erro ao eliminar mensagem.", "error")

    return redirect(url_for("admin_mensagens"))

# ==========================================
# 🌐 Páginas gerais
# ==========================================
@app.route("/")
def index():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT Id, NomeCliente, Classificacao, Comentario, Aprovado, DataEnvio
        FROM Feedbacks
        WHERE Aprovado = TRUE
        ORDER BY DataEnvio DESC
        LIMIT 6
    """)
    feedbacks = cur.fetchall()
    conn.close()
    return render_template("index.html", feedbacks=feedbacks)


@app.route("/sobre")
def sobre():
    return render_template("sobre.html")

@app.route("/servicos")
def servicos():
    termo = (request.args.get("q") or "").strip()
    conn = get_db_connection(); cur = conn.cursor()
    if termo:
        cur.execute("SELECT * FROM Servicos WHERE Nome LIKE %s OR Descricao LIKE %s", (f"%{termo}%", f"%{termo}%"))
    else:
        cur.execute("SELECT * FROM Servicos")
    servs = cur.fetchall(); conn.close()
    return render_template("servicos.html", servicos=servs)

@app.route("/contato", methods=["GET", "POST"])
def contato():
    if request.method == "POST":
        assunto = (request.form.get("assunto") or "").strip()
        nome = (request.form.get("nome") or "").strip()
        email = (request.form.get("email") or "").strip()
        mensagem = (request.form.get("mensagem") or "").strip()

        # Validação de campos obrigatórios
        if not (assunto and nome and email and mensagem):
            flash("Por favor, preencha todos os campos antes de enviar.", "error")
            return redirect(url_for("contato"))

        # Guarda a mensagem na base de dados
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO MensagensContato (Nome, Email, Assunto, Mensagem)
                VALUES (%s, %s, %s, %s)
            """, (nome, email, assunto, mensagem))
            conn.commit()
            conn.close()
        except Exception as e:
            app.logger.error(f"Erro ao guardar mensagem: {e}")
            flash("Erro ao enviar mensagem. Tente novamente.", "error")
            return redirect(url_for("contato"))

        # --- Envia e-mail para o administrador ---
        try:
            send_email(
                "📥 Novo contacto recebido • Agenda Beleza",
                [os.getenv("ADMIN_EMAIL")],
                render_template(
                    "emails/admin/novo_contato.html",
                    nome=nome,
                    email=email,
                    assunto=assunto,
                    mensagem=mensagem,
                    now=datetime.utcnow
                )
            )
        except Exception as e:
            app.logger.error(f"Erro ao enviar e-mail para o admin: {e}")

        # --- Envia e-mail de confirmação ao cliente ---
        try:
            send_email(
                "📩 Recebemos a sua mensagem • Agenda Beleza",
                [email],
                render_template(
                    "emails/clientes/confirmacao_contato.html",
                    nome=nome,
                    assunto=assunto,
                    now=datetime.utcnow
                )
            )
        except Exception as e:
            app.logger.error(f"Erro ao enviar e-mail de confirmação: {e}")

        flash("Mensagem enviada com sucesso!", "success")
        return redirect(url_for("contato"))

    return render_template("contato.html")


# ==========================================
# 💬 Feedback dos Clientes
# ==========================================
@app.route("/feedback", methods=["GET", "POST"])
def feedback():
    if request.method == "POST":
        nome = session.get("nome")
        classificacao = request.form["classificacao"]
        comentario = request.form["comentario"]
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO Feedbacks (NomeCliente, Cliente_id, Classificacao, Comentario)
                VALUES (%s, %s, %s, %s)
            """, (nome, session.get("user_id"), classificacao, comentario))
            conn.commit()
            conn.close()
            flash("Feedback enviado com sucesso! 🌸", "success")
            return redirect(url_for("listar_feedbacks"))
        except Exception as e:
            app.logger.error(f"Erro ao enviar feedback: {e}")
            flash("Erro ao enviar o feedback.", "error")
    return render_template("feedback.html")


# ==========================================
# 💬 Página com todos os feedbacks
# ==========================================
@app.route("/feedbacks")
def listar_feedbacks():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT NomeCliente, Classificacao, Comentario, DataEnvio
            FROM Feedbacks
            WHERE Aprovado = 1 OR Aprovado IS NULL
            ORDER BY DataEnvio DESC
        """)
        feedbacks = cur.fetchall()
        conn.close()
        return render_template("feedbacks.html", feedbacks=feedbacks)
    except Exception as e:
        app.logger.error(f"Erro ao carregar feedbacks: {e}")
        flash("Erro ao carregar os feedbacks.", "error")
        return redirect(url_for("index"))


@app.route("/admin_feedbacks")
def admin_feedbacks():
    if not session.get("is_admin"):
        flash("Acesso restrito aos administradores.", "error")
        return redirect(url_for("index"))

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM Feedbacks ORDER BY DataCriacao DESC")
    feedbacks = cur.fetchall()
    conn.close()
    return render_template("admin_feedbacks.html", feedbacks=feedbacks)


@app.route("/aprovar_feedback/<int:id>")
def aprovar_feedback(id):
    if not session.get("is_admin"):
        flash("Acesso negado.", "error")
        return redirect(url_for("index"))

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE Feedbacks SET Aprovado = TRUE WHERE Id = %s", (id,))
    conn.commit()
    conn.close()

    flash("Feedback aprovado com sucesso!", "success")
    return redirect(url_for("admin_feedbacks"))


@app.route("/remover_feedback/<int:id>")
def remover_feedback(id):
    if not session.get("is_admin"):
        flash("Acesso negado.", "error")
        return redirect(url_for("index"))

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM Feedbacks WHERE Id = %s", (id,))
    conn.commit()
    conn.close()

    flash("Feedback removido com sucesso.", "info")
    return redirect(url_for("admin_feedbacks"))

# ==========================================
# ⏰ Lembretes automáticos (1h antes)
# ==========================================
def enviar_lembretes():
    try:
        app.logger.info("A verificar marcações para lembrete...")
        conn = get_db_connection(); cur = conn.cursor()
        # requer coluna LembreteEnviado BOOLEAN DEFAULT 0
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

        for (mid, nome, email, servico, datahora) in marcacoes:
            html = render_template(
                "emails/clientes/lembrete_email.html",
                nome=nome, servico=servico,
                datahora=datahora.strftime("%d/%m/%Y %H:%M"),
                current_year=datetime.now().year
            )
            send_email("⏰ Lembrete de Marcação • Agenda Beleza", [email], html)
            cur.execute("UPDATE Marcacoes SET LembreteEnviado = 1 WHERE Id = %s", (mid,))
            conn.commit()

        conn.close()
        if marcacoes:
            app.logger.info(f"{len(marcacoes)} lembrete(s) enviados.")
    except Exception as e:
        app.logger.error(f"Erro no envio de lembretes: {e}")

# Scheduler
scheduler = APScheduler()
scheduler.init_app(app)
scheduler.start()
scheduler.add_job(id='lembretes_marcacoes', func=enviar_lembretes, trigger='interval', minutes=5)

# ==========================================
# 🧪 Teste SMTP rápido
# ==========================================
@app.route("/debug/email")
def debug_email():
    html = "<p>✅ E-mail de teste enviado a partir do servidor.</p>"
    send_email("🧪 Teste SMTP • Agenda Beleza", [os.getenv("MAIL_USERNAME")], html)
    return "Pedido de envio feito. Verifica a caixa de entrada/SPAM."

# ==========================================
# ▶️ Run
# ==========================================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=False)
