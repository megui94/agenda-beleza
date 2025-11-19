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

# 🔹 Configura domínio base para gerar URLs externas corretamente
app.config["PREFERRED_URL_SCHEME"] = "https"
app.config["SERVER_NAME"] = "agenda-beleza-ipca.onrender.com"
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
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT Id, Password, Nome, IsAdmin, EmailVerificado FROM Utilizador WHERE Email=%s",
            (email,),
        )
        user = cur.fetchone()
        conn.close()

        # ✅ Utilizador existe e password correta?
        if user and bcrypt.check_password_hash(user["Password"], password):

            # ✅ Verificação de e-mail
            if not user.get("EmailVerificado"):
                flash("Por favor, confirme o seu e-mail antes de iniciar sessão.", "warning")
                return redirect(url_for("login"))

            # ✅ Guardar dados na sessão
            session.update({
                "user_id": user["Id"],
                "email": email,
                "nome": user["Nome"],
                "is_admin": bool(user["IsAdmin"]),
            })

            flash(f"Bem-vindo(a), {user['Nome']}!", "success")

            next_page = session.pop("next", None)
            return redirect(url_for(next_page)) if next_page else redirect(url_for("index"))

        # Falhou login
        flash("E-mail ou senha incorretos.", "error")
        return render_template("login.html")

    # GET → só mostra o formulário
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

        # 🔒 Validações
        if password != confirm:
            flash("As senhas não coincidem.", "error")
            return redirect(url_for("registar"))

        if not re.match(r"^(?=.*[A-Z])(?=.*\d).{8,}$", password):
            flash("A senha deve ter pelo menos 8 caracteres, incluindo uma maiúscula e um número.", "error")
            return redirect(url_for("registar"))

        conn = get_db_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM Utilizador WHERE Email = %s", (email,))
        existing = cur.fetchone()

        # 🔁 Se já existe e ainda não confirmou o e-mail
        if existing:
            if not existing.get("EmailVerificado"):
                token = serializer.dumps(email, salt="email-confirm")
                link = url_for("confirmar_email", token=token, _external=True, _scheme="https")

                # ✉️ Reenvio do e-mail de confirmação (usando template HTML)
                html_cliente = render_template(
                    "emails/clientes/reenviar_confirmacao.html",
                    nome=existing["Nome"],
                    confirm_url=link
                )

                send_email("🔁 Confirmação pendente • Agenda Beleza", [email], html_cliente)
                flash("Já existe uma conta com este e-mail, mas ainda não foi verificada. Enviámos novamente o e-mail de confirmação.", "info")
                conn.close()
                return redirect(url_for("login"))
            else:
                flash("Já existe uma conta registada com este e-mail.", "error")
                conn.close()
                return redirect(url_for("login"))

        # ✅ Cria nova conta
        hashed_pw = bcrypt.generate_password_hash(password).decode("utf-8")

        try:
            cur.execute(
                "INSERT INTO Utilizador (Nome, Email, Telefone, Password) VALUES (%s, %s, %s, %s)",
                (nome, email, telefone, hashed_pw)
            )
            conn.commit()

            # 🔹 Gera link de confirmação (válido por 30 min)
            token = serializer.dumps(email, salt="email-confirm")
            link = url_for("confirmar_email", token=token, _external=True, _scheme="https")

            # ✉️ E-mail para o cliente (confirmação de conta)
            html_cliente = render_template(
                "emails/clientes/confirmacao_email.html",
                nome=nome,
                confirm_url=link
            )
            send_email("📧 Confirme o seu e-mail • Agenda Beleza", [email], html_cliente)

            # 💌 E-mail para o admin (notificação de novo registo)
            html_admin = render_template(
                "emails/admin/novo_registo_admin.html",
                nome=nome,
                email=email,
                telefone=telefone,
                data=datetime.now().strftime("%d/%m/%Y %H:%M")
            )
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
        email = serializer.loads(token, salt="email-confirm", max_age=1800) #30 min
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
            # ✅ Aceita todos os formatos possíveis do input datetime-local
            formatos = [
                "%Y-%m-%dT%H:%M",
                "%Y-%m-%d %H:%M",
                "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%dT%H:%M:%S.%f",
                "%Y-%m-%d %H:%M:%S.%f"
            ]

            datahora_obj = None
            for fmt in formatos:
                try:
                    datahora_obj = datetime.strptime(datahora.strip(), fmt)
                    break
                except ValueError:
                    continue

            if not datahora_obj:
                raise ValueError("Formato inválido")

            # ✅ Inserir marcação na BD
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO Marcacoes (Cliente_id, Servico_id, DataHora, Estado, Observacoes)
                VALUES (%s, %s, %s, 'Pendente', %s)
            """, (session["user_id"], servico_id, datahora_obj, observacoes))
            conn.commit()

            # 🔹 Buscar nome do serviço
            cur.execute("SELECT Nome FROM Servicos WHERE Id=%s", (servico_id,))
            servico_nome = (cur.fetchone() or ["—"])[0]

            # 💌 Enviar e-mail ao cliente
            html_cliente = render_template(
                "emails/clientes/marcacao_email.html",
                nome=session.get("nome", "Cliente"),
                datahora=datahora_obj.strftime("%d/%m/%Y %H:%M"),
                servico=servico_nome
            )
            send_email("🗓️ Marcação registada com sucesso", [session["email"]], html_cliente)

            # 💼 Enviar e-mail ao admin
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

            flash("Marcação enviada com sucesso!", "success")

        except ValueError:
            flash("Formato de data e hora inválido. Por favor, escolha novamente.", "error")

        except Exception as e:
            app.logger.error(f"Erro ao criar marcação: {e}")
            flash("Ocorreu um erro ao criar a marcação.", "error")

        return redirect(url_for("minhas_marcacoes"))

    # 🗂️ Mostrar os serviços disponíveis
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT Id, Nome FROM Servicos")
    servicos = cur.fetchall()
    conn.close()

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
# 💬 Feedback dos Clientes/Admins
# ==========================================
from datetime import datetime  # garante que tens isto no topo do ficheiro

@app.route("/feedback", methods=["GET", "POST"])
def feedback():
    # Só deixa enviar feedback se estiver autenticado
    if "user_id" not in session:
        flash("Tem de iniciar sessão para enviar feedback.", "warning")
        return redirect(url_for("login"))

    if request.method == "POST":
        nome = session.get("nome") or "Cliente"
        email_cliente = session.get("email")
        classificacao = request.form.get("classificacao")
        comentario = request.form.get("comentario")

        # validação simples
        if not classificacao or not comentario:
            flash("Por favor, preencha a classificação e o comentário.", "warning")
            return redirect(url_for("feedback"))

        try:
            # 🔹 1) Gravar na base de dados
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO Feedbacks (NomeCliente, Cliente_id, Classificacao, Comentario)
                VALUES (%s, %s, %s, %s)
            """, (nome, session.get("user_id"), classificacao, comentario))
            conn.commit()
            conn.close()

            # 🔹 2) E-mail para o CLIENTE
            try:
                if email_cliente:
                    html_cliente = render_template(
                        "emails/clientes/feedback_confirmacao.html",
                        nome=nome,
                        classificacao=classificacao,
                        comentario=comentario,
                        data_envio=datetime.now()
                    )

                    # usa a mesma função send_email que já usas para registo/marcações
                    send_email(
                        "💬 Recebemos o seu feedback • Agenda Beleza",
                        [email_cliente],
                        html_cliente
                    )

                    app.logger.info(
                        f"E-mail de confirmação de feedback enviado para {email_cliente}"
                    )
                else:
                    app.logger.warning(
                        "Feedback enviado mas não há email na sessão para enviar confirmação."
                    )
            except Exception as e:
                app.logger.error(f"Erro ao enviar e-mail de confirmação de feedback: {e}")

            # 🔹 3) E-mail para o ADMIN
            try:
                # usa a mesma config que já usas noutros emails para o admin
                # se já tens ADMIN_EMAIL no config.py, podes fazer: admin_email = ADMIN_EMAIL
                admin_email = app.config.get(
                    "ADMIN_EMAIL",
                    "agenda.beleza.contato@gmail.com"  # fallback se não existir config
                )

                html_admin = render_template(
                    "emails/admin/novo_feedback.html",
                    nome=nome,
                    email_cliente=email_cliente,
                    classificacao=classificacao,
                    comentario=comentario,
                    data_envio=datetime.now()
                )

                send_email(
                    "📥 Novo feedback recebido • Agenda Beleza",
                    [admin_email],
                    html_admin
                )

                app.logger.info(
                    f"E-mail de novo feedback enviado para admin ({admin_email})"
                )
            except Exception as e:
                app.logger.error(
                    f"Erro ao enviar e-mail de novo feedback para admin: {e}"
                )

            # 🔹 4) Mensagem no site + redirect
            flash("Feedback enviado com sucesso! 🌸", "success")
            # se o teu endpoint público se chama diferente, troca "feedbacks" pelo nome certo
            return redirect(url_for("feedbacks"))

        except Exception as e:
            app.logger.error(f"Erro ao enviar feedback: {e}")
            flash("Erro ao enviar o feedback.", "error")
            return redirect(url_for("feedback"))

    # GET → mostra o formulário
    return render_template("feedback.html")



# ==========================================
# 💬 Feedback dos Clientes no site
# ==========================================
@app.route("/feedbacks")
def feedbacks():
    conn = get_db_connection()
    cur = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT NomeCliente, Comentario, Classificacao, DataEnvio
        FROM Feedbacks
        WHERE Aprovado = TRUE
        ORDER BY DataEnvio DESC
    """)
    feedbacks = cur.fetchall()
    conn.close()

    return render_template("feedbacks.html", feedbacks=feedbacks)


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


# 🔹 Ver feedbacks (apenas admin)
@app.route("/admin/feedbacks")
def admin_feedbacks():
    if not session.get("is_admin"):
        flash("Acesso restrito a administradores.", "warning")
        return redirect(url_for("index"))

    conn = get_db_connection()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM Feedbacks ORDER BY DataEnvio DESC")
    feedbacks = cur.fetchall()
    conn.close()

    return render_template("admin_feedbacks.html", feedbacks=feedbacks)


# 🔹 Aprovar feedback
@app.route("/admin/feedbacks/aprovar/<int:id>", methods=["POST"])
def aprovar_feedback(id):
    if not session.get("is_admin"):
        flash("Acesso restrito a administradores.", "warning")
        return redirect(url_for("index"))

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE Feedbacks SET Aprovado = TRUE WHERE Id = %s", (id,))
    conn.commit()
    conn.close()

    flash("Feedback aprovado com sucesso!", "success")
    return redirect(url_for("admin_feedbacks"))


# 🔹 Rejeitar (apagar) feedback
@app.route("/admin/feedbacks/rejeitar/<int:id>", methods=["POST"])
def rejeitar_feedback(id):
    if not session.get("is_admin"):
        flash("Acesso restrito a administradores.", "warning")
        return redirect(url_for("index"))

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM Feedbacks WHERE Id = %s", (id,))
    conn.commit()
    conn.close()

    flash("Feedback rejeitado e removido.", "info")
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
