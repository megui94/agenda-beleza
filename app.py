# -*- coding: utf-8 -*-
import os

# Quando corres localmente com "python app.py" usa a app já criada no package.
from app import app


# =============================================================================
# 📦 IMPORTS
# =============================================================================
import os
import re
import time
import socket
import logging
import traceback
from datetime import datetime, timezone, timedelta

import mysql.connector
from dotenv import load_dotenv
from flask import (
    Flask, render_template, request, redirect,
    flash, session, url_for, abort, current_app
)
from flask_bcrypt import Bcrypt
from flask_apscheduler import APScheduler
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature
from logging.handlers import RotatingFileHandler

import sib_api_v3_sdk
from sib_api_v3_sdk.rest import ApiException

# =============================================================================
# 🔧 INICIALIZAÇÃO / CONFIGURAÇÃO BASE
# =============================================================================
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "segredo-super-seguro")

# Serializador usado em tokens (confirmar e-mail, reset password)
serializer = URLSafeTimedSerializer(app.secret_key)

# Domínio base para gerar URLs externas corretamente (Render)
app.config["PREFERRED_URL_SCHEME"] = "https"

server_name = os.getenv("SERVER_NAME")
if server_name:
    app.config["SERVER_NAME"] = server_name

# =============================================================================
# 🔍 VERIFICAÇÃO DA BREVO API
# =============================================================================
if not os.getenv("BREVO_API_KEY"):
    app.logger.warning("BREVO_API_KEY não definida — emails desativados (o site continua a funcionar).")
    
# =============================================================================
# 📝 LOGGING (FICHEIRO)
# =============================================================================
from pathlib import Path
import logging
from logging.handlers import RotatingFileHandler

BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

log_path = LOG_DIR / "app.log"

handler = RotatingFileHandler(
    log_path,
    maxBytes=1_000_000,
    backupCount=5,
    encoding="utf-8",
)

handler.setLevel(logging.INFO)
handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))

# Evita duplicar handlers (muito comum com debug/reloader)
if not any(isinstance(h, RotatingFileHandler) for h in app.logger.handlers):
    app.logger.addHandler(handler)

app.logger.setLevel(logging.INFO)
app.logger.info("Logging iniciado.")

# =============================================================================
# 📧 ENVIO DE E-MAILS VIA API BREVO
# =============================================================================
def _normalize_email_list(recipients):
    """Normaliza lista de destinatários e remove e-mails inválidos/vazios."""
    if not recipients:
        return []
    if isinstance(recipients, str):
        recipients = [recipients]

    email_re = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    out = []
    seen = set()
    for r in recipients:
        if r is None:
            continue
        e = str(r).strip()
        if not e:
            continue
        # Remove nomes do tipo "Nome <email@x.com>"
        m = re.search(r"<([^>]+)>", e)
        if m:
            e = m.group(1).strip()
        if not email_re.match(e):
            continue
        if e.lower() in seen:
            continue
        seen.add(e.lower())
        out.append(e)
    return out


def send_email(subject, recipients, html_body, reply_to=None, *, tags=None, max_retries=3):
    """
    Envia e-mails via API do Brevo (TransactionalEmailsApi).

    - Retorna True/False (para não "falhar em silêncio").
    - Faz retry em erros transitórios (429/5xx/timeouts).
    - Regista logs detalhados para diagnosticar bloqueios por IP / chave inválida.
    """
    api_key = os.getenv("BREVO_API_KEY")
    sender_email = os.getenv(
        "MAIL_DEFAULT_SENDER",
        os.getenv("ADMIN_EMAIL", "agenda.beleza.contato@gmail.com"),
    )

    to_emails = _normalize_email_list(recipients)
    if not to_emails:
        app.logger.warning("[BREVO] Nenhum destinatário válido. Email não enviado.")
        return False

    if not api_key:
        app.logger.error("[BREVO] Falha: BREVO_API_KEY não configurada. Emails desativados.")
        return False

    configuration = sib_api_v3_sdk.Configuration()
    configuration.api_key["api-key"] = api_key

    api_client = sib_api_v3_sdk.ApiClient(configuration)
    api_instance = sib_api_v3_sdk.TransactionalEmailsApi(api_client)

    sender = {"name": "Agenda de Beleza 💅", "email": sender_email}
    to_list = [{"email": e} for e in to_emails]

    email_data = sib_api_v3_sdk.SendSmtpEmail(
        to=to_list,
        sender=sender,
        subject=str(subject or "").strip() or "(sem assunto)",
        html_content=html_body or "",
        reply_to={"email": reply_to} if reply_to else None,
        tags=tags if tags else None,
    )

    def _is_ip_block(body_text: str) -> bool:
        t = (body_text or "").lower()
        return ("unauthorized" in t and "ip" in t) or ("ip" in t and "authorize" in t)

    backoff = 1
    for attempt in range(1, int(max_retries) + 1):
        try:
            api_instance.send_transac_email(email_data)
            app.logger.info(f"[BREVO] Email enviado → {to_emails} | assunto={subject!r}")
            return True

        except ApiException as e:
            status = getattr(e, "status", None)
            body = getattr(e, "body", "") or ""
            app.logger.error(
                f"[BREVO] Erro ao enviar (tentativa {attempt}/{max_retries}) "
                f"status={status} body={body}"
            )

            # Bloqueio por IP (muito comum quando a Brevo tem 'IP Security' ativo)
            if _is_ip_block(body):
                app.logger.error(
                    "[BREVO] Bloqueado por IP. Autoriza o(s) IP(s)/range(s) do teu servidor "
                    "na Brevo (Settings → Security → Authorized IPs)."
                )
                return False

            # Retry apenas em erros transitórios
            retriable = (
                status in (408, 425, 429)
                or (isinstance(status, int) and 500 <= status <= 599)
                or status is None
            )
            if (not retriable) or attempt == int(max_retries):
                return False

        except Exception as e:
            app.logger.error(
                f"[BREVO] Exceção ao enviar (tentativa {attempt}/{max_retries}): {e}"
            )
            if attempt == int(max_retries):
                return False

        time.sleep(backoff)
        backoff = min(backoff * 2, 8)

    return False


def normalize_estado(estado):
    """
    Normaliza estados para evitar inconsistências ("Aprovado" vs "Aprovada", etc.).
    Estados canónicos:
      - Pendente
      - Aprovada
      - Rejeitada
      - Cancelada
    """
    if estado is None:
        return None
    s = str(estado).strip()
    if not s:
        return None
    low = s.lower()

    mapping = {
        "pendente": "Pendente",
        "aprovado": "Aprovada",
        "aprovada": "Aprovada",
        "rejeitado": "Rejeitada",
        "rejeitada": "Rejeitada",
        "cancelado": "Cancelada",
        "cancelada": "Cancelada",
        "cancelar": "Cancelada",
        "cancel": "Cancelada",
    }
    return mapping.get(low, s)

def get_db_connection():
    """
    Cria e devolve uma conexão MySQL com retry e suporte a SSL (CA opcional).
    Lê host, user, pass, db, port e CA das variáveis de ambiente.
    """
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

    # Log do IP para debugging
    try:
        ip = socket.gethostbyname(host)
        app.logger.info(f"MySQL host {host} → {ip}")
    except Exception as e:
        app.logger.error(f"Erro DNS: {e}")
        raise

    # Pequeno retry de ligação
    for i in range(3):
        try:
            conn = mysql.connector.connect(
                host=host,
                port=port,
                user=user,
                password=password,
                database=database,
                connection_timeout=10,
                **ssl_config,
            )
            if conn.is_connected():
                return conn
        except Error as err:
            app.logger.warning(f"Tentativa MySQL {i+1}/3 falhou: {err}")
            time.sleep(2)

    raise Exception("Não foi possível conectar ao MySQL.")

# =============================================================================
# 🔐 SEGURANÇA, BCRYPT E CONTEXT PROCESSORS
# =============================================================================
bcrypt = Bcrypt(app)

@app.context_processor
def inject_globals():
    return {
        "current_year": datetime.now(timezone.utc).year,
        "is_admin": bool(session.get("is_admin", False)),
        "user_nome": session.get("nome"),
        "user_email": session.get("email")
    }

@app.errorhandler(500)
def internal_error(_):
    """Erro genérico 500 com logging."""
    app.logger.error(traceback.format_exc())
    return "Ocorreu um erro interno no servidor.", 500

# =============================================================================
# 👥 AUTENTICAÇÃO (LOGIN / LOGOUT / REGISTO / CONFIRMAÇÃO E RESET)
# =============================================================================
@app.route("/login", methods=["GET", "POST"])
def login():
    """Login do utilizador (cliente ou admin)."""
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

        if user and bcrypt.check_password_hash(user["Password"], password):
            # E-mail confirmado?
            if not user.get("EmailVerificado"):
                flash("Por favor, confirme o seu e-mail antes de iniciar sessão.", "warning")
                return redirect(url_for("login"))

            # Guarda dados na sessão
            session.update(
                {
                    "user_id": user["Id"],
                    "email": email,
                    "nome": user["Nome"],
                    "is_admin": bool(user["IsAdmin"]),
                }
            )

            flash(f"Bem-vindo(a), {user['Nome']}!", "success")

            next_page = session.pop("next", None)
            return redirect(url_for(next_page)) if next_page else redirect(url_for("index"))

        flash("E-mail ou senha incorretos.", "error")
        return render_template("login.html")

    return render_template("login.html")


@app.route("/logout")
def logout():
    """Termina a sessão atual."""
    session.clear()
    flash("Saiu da conta com sucesso.", "info")
    return redirect(url_for("index"))


@app.route("/registar", methods=["GET", "POST"])
def registar():
    """Criação de conta + envio de e-mail de confirmação."""
    if request.method == "POST":
        nome = (request.form.get("nome") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        telefone = (request.form.get("telefone") or "").strip()
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm_password") or ""

        # Validações
        if password != confirm:
            flash("As senhas não coincidem.", "error")
            return redirect(url_for("registar"))

        if not re.match(r"^(?=.*[A-Z])(?=.*\d).{8,}$", password):
            flash(
                "A senha deve ter pelo menos 8 caracteres, incluindo uma maiúscula e um número.",
                "error",
            )
            return redirect(url_for("registar"))

        conn = get_db_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM Utilizador WHERE Email = %s", (email,))
        existing = cur.fetchone()

        # Conta já existe
        if existing:
            # Existe mas ainda não confirmou — reenvia e-mail
            if not existing.get("EmailVerificado"):
                token = serializer.dumps(email, salt="email-confirm")
                link = url_for("confirmar_email", token=token, _external=True, _scheme="https")

                html_cliente = render_template(
                    "emails/clientes/reenviar_confirmacao.html",
                    nome=existing["Nome"],
                    confirm_url=link,
                )
                send_email(
                    "🔁 Confirmação pendente • Agenda Beleza",
                    [email],
                    html_cliente,
                )

                flash(
                    "Já existe uma conta com este e-mail, mas ainda não foi verificada. "
                    "Enviámos novamente o e-mail de confirmação.",
                    "info",
                )
                conn.close()
                return redirect(url_for("login"))
            else:
                flash("Já existe uma conta registada com este e-mail.", "error")
                conn.close()
                return redirect(url_for("login"))

        # Cria nova conta
        hashed_pw = bcrypt.generate_password_hash(password).decode("utf-8")

        try:
            cur.execute(
                "INSERT INTO Utilizador (Nome, Email, Telefone, Password) VALUES (%s, %s, %s, %s)",
                (nome, email, telefone, hashed_pw),
            )
            conn.commit()

            token = serializer.dumps(email, salt="email-confirm")
            link = url_for("confirmar_email", token=token, _external=True, _scheme="https")

            # E-mail de confirmação para o cliente
            html_cliente = render_template(
                "emails/clientes/confirmacao_email.html",
                nome=nome,
                confirm_url=link,
            )
            send_email("📧 Confirme o seu e-mail • Agenda Beleza", [email], html_cliente)

            # Notificação para admin
            html_admin = render_template(
                "emails/admin/novo_registo_admin.html",
                nome=nome,
                email=email,
                telefone=telefone,
                data=datetime.now().strftime("%d/%m/%Y %H:%M"),
            )
            send_email(
                "👤 Nova cliente registada • Agenda Beleza",
                ["agenda.beleza.contato@gmail.com"],
                html_admin,
            )

            flash("Conta criada com sucesso! Verifique o seu e-mail para confirmar.", "success")

        except Exception as e:
            flash(f"Erro ao criar conta: {e}", "error")
        finally:
            conn.close()

        return redirect(url_for("login"))

    return render_template("registar.html")


@app.route("/confirmar_email/<token>")
def confirmar_email(token):
    """Confirma e-mail de registo via token (30 minutos)."""
    try:
        email = serializer.loads(token, salt="email-confirm", max_age=1800)
    except SignatureExpired:
        flash("O link de confirmação expirou. Faça login e solicite novo envio.", "error")
        return redirect(url_for("login"))
    except BadSignature:
        flash("Link de confirmação inválido.", "error")
        return redirect(url_for("login"))

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE Utilizador SET EmailVerificado = 1 WHERE Email = %s", (email,))
    conn.commit()
    conn.close()

    flash("E-mail confirmado com sucesso! Já pode iniciar sessão.", "success")
    return redirect(url_for("login"))


# ---- Recuperação de senha ---------------------------------------------------
@app.route("/reset_request", methods=["GET", "POST"])
def reset_request():
    """Formulário para pedir recuperação de senha."""
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT Id, Nome FROM Utilizador WHERE Email=%s", (email,))
        user = cur.fetchone()
        conn.close()

        if not user:
            flash("E-mail não encontrado.", "error")
            return render_template("reset_request.html")

        token = serializer.dumps(email, salt="reset-salt")
        reset_link = url_for("reset_token", token=token, _external=True)
        html = render_template(
            "emails/clientes/reset_email.html",
            nome=user[1],
            reset_link=reset_link,
        )
        send_email("🔐 Recuperação de Senha • Agenda Beleza", [email], html)
        flash("Enviámos um link de redefinição.", "info")
        return redirect(url_for("login"))

    return render_template("reset_request.html")


@app.route("/reset/<token>", methods=["GET", "POST"])
def reset_token(token):
    """Página de redefinição de senha a partir do link enviado por e-mail."""
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
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("UPDATE Utilizador SET Password=%s WHERE Email=%s", (hashed, email))
        conn.commit()
        conn.close()

        flash("Senha atualizada com sucesso!", "success")
        return redirect(url_for("login"))

    return render_template("reset_token.html", token=token)


# =============================================================================
# 🗓️ MARCAÇÕES (CLIENTE)
# =============================================================================
@app.route("/agendar")
def agendar_redirect():
    """
    Rota antiga de /agendar.
    Apenas redireciona para /marcacoes, garantindo que o utilizador esteja logado.
    """
    if not session.get("user_id"):
        flash("Faça login para agendar a sua marcação.", "info")
        session["next"] = "marcacoes"
        return redirect(url_for("login"))
    return redirect(url_for("marcacoes"))


@app.route("/marcacoes", methods=["GET", "POST"])
def marcacoes():
    """Criar nova marcação e listar serviços disponíveis."""
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
            # Aceita vários formatos vindos do input
            formatos = [
                "%Y-%m-%dT%H:%M",
                "%Y-%m-%d %H:%M",
                "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%dT%H:%M:%S.%f",
                "%Y-%m-%d %H:%M:%S.%f",
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

            # Inserir marcação
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO Marcacoes (Cliente_id, Servico_id, DataHora, Estado, Observacoes)
                VALUES (%s, %s, %s, 'Pendente', %s)
                """,
                (session["user_id"], servico_id, datahora_obj, observacoes),
            )
            conn.commit()

            # Nome do serviço
            cur.execute("SELECT Nome FROM Servicos WHERE Id=%s", (servico_id,))
            servico_nome = (cur.fetchone() or ["—"])[0]

            # E-mail para cliente
            html_cliente = render_template(
                "emails/clientes/marcacao_email.html",
                nome=session.get("nome", "Cliente"),
                datahora=datahora_obj.strftime("%d/%m/%Y %H:%M"),
                servico=servico_nome,
            )
            send_email("🗓️ Marcação registada com sucesso", [session["email"]], html_cliente)

            # E-mail para admin
            html_admin = render_template(
                "emails/admin/nova_marcacao_admin.html",
                nome_cliente=session.get("nome", "Cliente"),
                servico=servico_nome,
                datahora=datahora_obj.strftime("%d/%m/%Y %H:%M"),
                observacoes=observacoes,
            )
            admin_email = os.getenv("ADMIN_EMAIL", os.getenv("MAIL_DEFAULT_SENDER"))
            send_email(
                "📢 Nova marcação pendente",
                [admin_email],
                html_admin,
                reply_to=session["email"],
            )

            conn.close()
            flash("Marcação enviada com sucesso!", "success")

        except ValueError:
            flash("Formato de data e hora inválido. Por favor, escolha novamente.", "error")
        except Exception as e:
            app.logger.error(f"Erro ao criar marcação: {e}")
            flash("Ocorreu um erro ao criar a marcação.", "error")

        return redirect(url_for("minhas_marcacoes"))

    # GET → mostrar serviços para agendar
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT Id, Nome FROM Servicos")
    servicos = cur.fetchall()
    conn.close()

    return render_template("marcacoes.html", servicos=servicos)


@app.route("/minhas_marcacoes")
def minhas_marcacoes():
    """Lista todas as marcações do utilizador autenticado."""
    if not session.get("user_id"):
        session.pop("marcacao_sucesso", None)
        flash("Inicie sessão para aceder às suas marcações.", "error")
        return redirect(url_for("login"))

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT m.Id, s.Nome, m.DataHora, m.Estado, m.Observacoes
        FROM Marcacoes m
        JOIN Servicos s ON m.Servico_id = s.Id
        WHERE m.Cliente_id = %s
        ORDER BY m.DataHora DESC
        """,
        (session["user_id"],),
    )
    marcacoes = cur.fetchall()
    conn.close()

    return render_template(
        "minhas_marcacoes.html",
        marcacoes=marcacoes,
        now=datetime.now(),
    )


@app.route("/cancelar_marcacao/<int:id>", methods=["POST"])
def cancelar_marcacao(id):
    """Cancelar marcação pelo cliente (até 4 horas antes)."""
    if "user_id" not in session:
        flash("Tem de iniciar sessão para cancelar uma marcação.", "warning")
        return redirect(url_for("login"))

    try:
        conn = get_db_connection()
        cur = conn.cursor(dictionary=True)

        # Marca para este cliente
        cur.execute(
            """
            SELECT m.*, s.Nome AS NomeServico
            FROM Marcacoes m
            JOIN Servicos s ON m.Servico_id = s.Id
            WHERE m.Id = %s AND m.Cliente_id = %s
            """,
            (id, session["user_id"]),
        )
        marcacao = cur.fetchone()

        if not marcacao:
            flash("Marcação não encontrada.", "danger")
            conn.close()
            return redirect(url_for("minhas_marcacoes"))

        # Verificar antecedência mínima de 4 horas
        agora = datetime.now()
        diferenca = marcacao["DataHora"] - agora

        if diferenca.total_seconds() < 4 * 3600:
            flash("Já não pode cancelar a menos de 4h da hora marcada.", "warning")
            conn.close()
            return redirect(url_for("minhas_marcacoes"))

        # Atualiza estado
        cur.execute(
            "UPDATE Marcacoes SET Estado = 'Cancelada' WHERE Id = %s",
            (id,),
        )
        conn.commit()
        conn.close()

        # E-mail opcional de cancelamento
        try:
            html_email = render_template(
                "emails/clientes/marcacao_cancelada.html",
                nome=session["nome"],
                data=marcacao["DataHora"].strftime("%d/%m/%Y %H:%M"),
                servico=marcacao["NomeServico"],
            )
            send_email(
                "❌ Marcação cancelada • Agenda Beleza",
                [session["email"]],
                html_email,
            )
        except Exception as e:
            app.logger.error(f"Erro ao enviar e-mail de cancelamento: {e}")

        flash("Marcação cancelada com sucesso.", "success")
        return redirect(url_for("minhas_marcacoes"))

    except Exception as e:
        app.logger.error(f"Erro ao cancelar marcação: {e}")
        flash("Erro ao cancelar marcação.", "danger")
        return redirect(url_for("minhas_marcacoes"))


# =============================================================================
# 👩‍💼 ADMIN — GESTÃO DE MARCAÇÕES, MENSAGENS E DASHBOARD
# =============================================================================
@app.route("/admin/marcacoes", methods=["GET"])
def admin_marcacoes():
    if not session.get("is_admin"):
        flash("Acesso restrito: apenas administradores.", "error")
        return redirect(url_for("index"))

    estado = request.args.get("estado", "")
    termo = (request.args.get("q") or "").strip()

    conn = get_db_connection()
    cur = conn.cursor(dictionary=True)

    query = """
        SELECT 
            m.Id, 
            u.Nome      AS Nome,
            s.Nome      AS Servico, 
            m.DataHora  AS DataHora, 
            m.Estado    AS Estado, 
            m.Observacoes AS Observacoes,
            u.Email     AS Email
        FROM Marcacoes m
        JOIN Utilizador u ON m.Cliente_id = u.Id
        JOIN Servicos   s  ON m.Servico_id = s.Id
        WHERE 1 = 1
    """

    params = []
    if estado:
        query += " AND m.Estado = %s"
        params.append(estado)

    if termo:
        query += " AND (u.Nome LIKE %s OR s.Nome LIKE %s)"
        like = f"%{termo}%"
        params.extend([like, like])

    query += " ORDER BY m.DataHora DESC"

    cur.execute(query, tuple(params))
    marcacoes = cur.fetchall()
    conn.close()

    return render_template(
        "admin_marcacoes.html",
        marcacoes=marcacoes,
        estado=estado,
        termo=termo,
    )

# =============================================================================
# ✉️ UTILITÁRIO: ATUALIZAR ESTADO DA MARCAÇÃO + ENVIAR E-MAIL
# =============================================================================
def atualizar_estado_marcacao(marcacao_id: int, novo_estado: str):
    """
    Atualiza o estado de uma marcação e envia o e-mail certo ao cliente.

    Porquê existir esta função?
    - Evita código repetido em várias rotas (aprovar/rejeitar/cancelar).
    - Garante que o cliente é sempre notificado quando o estado muda.
    - Evita e-mails duplicados se o estado já estiver igual.
    """
    novo_estado = normalize_estado(novo_estado)

    conn = get_db_connection()
    cur = conn.cursor(dictionary=True)

    cur.execute(
        """
        SELECT
            m.Id,
            m.DataHora,
            m.Estado,
            m.Observacoes,
            u.Nome  AS NomeCliente,
            u.Email AS EmailCliente,
            s.Nome  AS NomeServico
        FROM Marcacoes m
        JOIN Utilizador u ON u.Id = m.Cliente_id
        JOIN Servicos s   ON s.Id = m.Servico_id
        WHERE m.Id = %s
        """,
        (marcacao_id,),
    )
    marc = cur.fetchone()

    if not marc:
        conn.close()
        return False, "Marcação não encontrada."

    estado_atual = normalize_estado(marc.get("Estado"))

    # Se já estiver no mesmo estado, não faz nada (evita e-mails duplicados)
    if estado_atual == novo_estado:
        conn.close()
        return True, "A marcação já estava nesse estado."

    cur.execute(
        "UPDATE Marcacoes SET Estado = %s WHERE Id = %s",
        (novo_estado, marcacao_id),
    )
    conn.commit()
    conn.close()

    email_cliente = marc.get("EmailCliente")
    if not email_cliente:
        return True, "Estado atualizado (cliente sem e-mail)."

    datahora_str = marc["DataHora"].strftime("%d/%m/%Y %H:%M")

    # Escolher template + assunto
    tags = ["marcacoes"]
    if novo_estado == "Aprovada":
        html = render_template(
            "emails/clientes/marcacao_aprovada.html",
            nome=marc["NomeCliente"],
            servico=marc["NomeServico"],
            datahora=datahora_str,
        )
        assunto = "✅ Marcação Aprovada • Agenda Beleza"
        tags.append("aprovada")

    elif novo_estado == "Rejeitada":
        html = render_template(
            "emails/clientes/marcacao_rejeitada.html",
            nome=marc["NomeCliente"],
            servico=marc["NomeServico"],
            datahora=datahora_str,
        )
        assunto = "❌ Marcação Rejeitada • Agenda Beleza"
        tags.append("rejeitada")

    elif novo_estado == "Cancelada":
        html = render_template(
            "emails/clientes/marcacao_cancelada.html",
            nome=marc["NomeCliente"],
            servico=marc["NomeServico"],
            data=datahora_str,
        )
        assunto = "❌ Marcação Cancelada • Agenda Beleza"
        tags.append("cancelada")

    else:
        # Para outros estados, apenas atualiza (sem e-mail automático)
        return True, "Estado atualizado."

    ok_envio = send_email(assunto, [email_cliente], html, tags=tags)
    if ok_envio:
        app.logger.info(
            f"[MARCACOES] Estado {novo_estado} → e-mail enviado para {email_cliente} (ID {marcacao_id})"
        )
        return True, "Estado atualizado e e-mail enviado."

    app.logger.error(
        f"[MARCACOES] Falha ao enviar e-mail (ID {marcacao_id}) para {email_cliente}"
    )
    return True, "Estado atualizado (falha ao enviar e-mail)."


@app.route("/admin/update_marcacao", methods=["POST"])
def admin_update_marcacao():
    """Atualiza estado de uma marcação (Aprovada / Rejeitada / Cancelada) e notifica cliente."""
    if not session.get("is_admin"):
        flash("Acesso restrito.", "error")
        return redirect(url_for("index"))

    marcacao_id = request.form.get("id")
    acao_raw = request.form.get("acao")
    acao = normalize_estado(acao_raw)

    if (not marcacao_id) or (acao not in ("Aprovada", "Rejeitada", "Cancelada")):
        flash("Ação inválida.", "error")
        return redirect(url_for("admin_marcacoes"))

    ok, msg = atualizar_estado_marcacao(int(marcacao_id), acao)
    flash(msg, "success" if ok else "error")
    return redirect(url_for("admin_marcacoes"))

# ✅ APROVAR MARCAÇÃO (ADMIN, via link)
@app.route("/admin/marcacoes/aprovar/<int:id>", endpoint="aprovar_marcacao")
def admin_aprovar_marcacao(id):
    if not session.get("is_admin"):
        abort(403)

    ok, msg = atualizar_estado_marcacao(id, "Aprovada")
    flash(msg, "success" if ok else "error")
    return redirect(url_for("admin_marcacoes"))


# ✅ REJEITAR / CANCELAR MARCAÇÃO (ADMIN, via link)
@app.route("/admin/marcacoes/rejeitar/<int:id>", endpoint="rejeitar_marcacao")
def admin_rejeitar_marcacao(id):
    if not session.get("is_admin"):
        abort(403)

    ok, msg = atualizar_estado_marcacao(id, "Rejeitada")
    flash(msg, "info" if ok else "error")
    return redirect(url_for("admin_marcacoes"))


# ---- Mensagens de contacto (admin) ------------------------------------------
@app.route("/admin/mensagens")
def admin_mensagens():
    """Lista mensagens de contacto com pesquisa (apenas admin)."""
    if not session.get("is_admin"):
        flash("Acesso restrito à administração.", "error")
        return redirect(url_for("index"))

    q = (request.args.get("q") or "").strip()

    conn = get_db_connection()
    cur = conn.cursor()

    if q:
        cur.execute(
            """
            SELECT Id, Nome, Email, Assunto, Mensagem, DataEnvio
            FROM MensagensContato
            WHERE Nome LIKE %s OR Email LIKE %s OR Assunto LIKE %s
            ORDER BY DataEnvio DESC
            """,
            (f"%{q}%", f"%{q}%", f"%{q}%"),
        )
    else:
        cur.execute(
            """
            SELECT Id, Nome, Email, Assunto, Mensagem, DataEnvio
            FROM MensagensContato
            ORDER BY DataEnvio DESC
            """
        )

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


# ---- Dashboard admin --------------------------------------------------------
@app.route("/admin/dashboard")
def admin_dashboard():
    """Dashboard de resumo para administrador (KPIs principais)."""
    
    # Verificação de segurança
    if not session.get("is_admin"):
        flash("Acesso restrito: apenas administradores.", "error")
        return redirect(url_for("index"))

    conn = get_db_connection()
    cur = conn.cursor()

    # 1. Total de marcações (Geral)
    cur.execute("SELECT COUNT(*) FROM Marcacoes")
    total_marcacoes = cur.fetchone()[0]

    # 2. Total Pendentes (CORRIGIDO: Removido o filtro de data)
    cur.execute("SELECT COUNT(*) FROM Marcacoes WHERE Estado = 'Pendente'")
    pendentes = cur.fetchone()[0]

    # 3. Total Aprovadas (CORRIGIDO: Removido o filtro de data)
    cur.execute("SELECT COUNT(*) FROM Marcacoes WHERE Estado IN ('Aprovado','Aprovada')")
    aprovadas = cur.fetchone()[0]

    # 4. Total de clientes
    cur.execute(
        "SELECT COUNT(*) FROM Utilizador WHERE IsAdmin = FALSE OR IsAdmin IS NULL"
    )
    total_clientes = cur.fetchone()[0]

    # 5. Feedbacks aprovados
    cur.execute("SELECT COUNT(*) FROM Feedbacks WHERE Aprovado = TRUE")
    total_feedbacks_aprovados = cur.fetchone()[0]

    # 6. Serviço mais marcado
    cur.execute(
        """
        SELECT s.Nome, COUNT(*) AS total
        FROM Marcacoes m
        JOIN Servicos s ON m.Servico_id = s.Id
        GROUP BY s.Id, s.Nome
        ORDER BY total DESC
        LIMIT 1
        """
    )
    row = cur.fetchone()
    conn.close()

    if row:
        servico_top, servico_top_qtd = row[0], row[1]
    else:
        servico_top, servico_top_qtd = None, 0

    # Enviar para o HTML com os novos nomes de variáveis
    return render_template(
        "admin_dashboard.html",
        total_marcacoes=total_marcacoes,
        pendentes=pendentes, 
        aprovadas=aprovadas, 
        total_clientes=total_clientes,
        total_feedbacks_aprovados=total_feedbacks_aprovados,
        servico_top=servico_top,
        servico_top_qtd=servico_top_qtd,
    )

# =============================================================================
# 🌐 PÁGINAS GERAIS (INÍCIO, SOBRE, SERVIÇOS, CONTACTO)
# =============================================================================
@app.route("/")
def index():
    """Página inicial — mostra alguns feedbacks aprovados (até 6)."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT Id, NomeCliente, Classificacao, Comentario, Aprovado, DataEnvio
        FROM Feedbacks
        WHERE Aprovado = TRUE
        ORDER BY DataEnvio DESC
        LIMIT 6
        """
    )
    feedbacks = cur.fetchall()
    conn.close()
    return render_template("index.html", feedbacks=feedbacks)


@app.route("/sobre")
def sobre():
    return render_template("sobre.html")


@app.route("/servicos")
def servicos():
    """Lista de serviços com pesquisa opcional."""
    termo = (request.args.get("q") or "").strip()
    conn = get_db_connection()
    cur = conn.cursor()
    if termo:
        cur.execute(
            "SELECT * FROM Servicos WHERE Nome LIKE %s OR Descricao LIKE %s",
            (f"%{termo}%", f"%{termo}%"),
        )
    else:
        cur.execute("SELECT * FROM Servicos")
    servs = cur.fetchall()
    conn.close()
    return render_template("servicos.html", servicos=servs)


@app.route("/contato", methods=["GET", "POST"])
def contato():
    """Página de contacto com envio de mensagem para BD + e-mails (cliente/admin)."""
    if request.method == "POST":
        assunto = (request.form.get("assunto") or "").strip()
        nome = (request.form.get("nome") or "").strip()
        email = (request.form.get("email") or "").strip()
        mensagem = (request.form.get("mensagem") or "").strip()

        if not (assunto and nome and email and mensagem):
            flash("Por favor, preencha todos os campos antes de enviar.", "error")
            return redirect(url_for("contato"))

        # Guarda na BD
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO MensagensContato (Nome, Email, Assunto, Mensagem)
                VALUES (%s, %s, %s, %s)
                """,
                (nome, email, assunto, mensagem),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            app.logger.error(f"Erro ao guardar mensagem: {e}")
            flash("Erro ao enviar mensagem. Tente novamente.", "error")
            return redirect(url_for("contato"))

        # E-mail para admin
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
                    now=datetime.utcnow(),
                ),
            )
        except Exception as e:
            app.logger.error(f"Erro ao enviar e-mail para o admin: {e}")

        # E-mail de confirmação para cliente
        try:
            send_email(
                "📩 Recebemos a sua mensagem • Agenda Beleza",
                [email],
                render_template(
                    "emails/clientes/confirmacao_contato.html",
                    nome=nome,
                    assunto=assunto,
                    now=datetime.utcnow(),
                ),
            )
        except Exception as e:
            app.logger.error(f"Erro ao enviar e-mail de confirmação: {e}")

        flash("Mensagem enviada com sucesso!", "success")
        return redirect(url_for("contato"))

    return render_template("contato.html")


# =============================================================================
# 💬 FEEDBACK DOS CLIENTES (ENVIAR / LISTAR / ADMIN)
# =============================================================================
@app.route("/feedback", methods=["GET", "POST"])
def feedback():
    """
    Formulário para o cliente autenticado enviar feedback.
    Grava na BD + tenta enviar e-mails de confirmação (cliente/admin).
    """
    # 1. Verificação de Login
    if "user_id" not in session:
        flash("Tem de iniciar sessão para enviar feedback.", "warning")
        return redirect(url_for("login"))

    # 2. Processar Formulário
    if request.method == "POST":
        nome = session.get("nome") or "Cliente"
        email_cliente = session.get("email")
        classificacao = request.form.get("classificacao")
        comentario = request.form.get("comentario")

        # Validação simples
        if not classificacao or not comentario:
            flash("Por favor, preencha a classificação e o comentário.", "warning")
            return redirect(url_for("feedback"))

        try:
            # A. Gravar na Base de Dados
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO Feedbacks (NomeCliente, Cliente_id, Classificacao, Comentario, Aprovado, DataEnvio)
                VALUES (%s, %s, %s, %s, FALSE, NOW())
                """,
                (nome, session.get("user_id"), classificacao, comentario),
            )
            conn.commit()
            conn.close()

            # B. Tentar Enviar E-mails (Com a função real 'send_email' descomentada)
            data_envio = datetime.now()
            admin_email = app.config.get("ADMIN_EMAIL", "agenda.beleza.contato@gmail.com")

            # E-mail para o CLIENTE
            if email_cliente:
                try:
                    html_cliente = render_template(
                        "emails/clientes/feedback_confirmacao.html",
                        nome=nome,
                        classificacao=classificacao,
                        comentario=comentario,
                        data_envio=data_envio
                    )
                    # ✅ ENVIAR REAL: Descomentado
                    send_email("💬 Recebemos o seu feedback • Agenda Beleza", [email_cliente], html_cliente)
                    app.logger.info(f"E-mail de confirmação de feedback enviado para {email_cliente}")

                except Exception as e:
                    app.logger.error(f"Erro ao enviar e-mail de confirmação de feedback para cliente: {e}")

            # E-mail para o ADMIN
            try:
                html_admin = render_template(
                    "emails/admin/novo_feedback.html",
                    nome=nome,
                    email_cliente=email_cliente,
                    classificacao=classificacao,
                    comentario=comentario,
                    data_envio=data_envio
                )
                # ✅ ENVIAR REAL: Descomentado
                send_email("📥 Novo feedback recebido • Agenda Beleza", [admin_email], html_admin)
                app.logger.info(f"E-mail de novo feedback enviado para admin ({admin_email})")
                
            except Exception as e:
                app.logger.error(f"Erro ao enviar e-mail de novo feedback para admin: {e}")


            # 3. Sucesso
            flash("Feedback enviado com sucesso! 🌸", "success")
            return redirect(url_for("listar_feedbacks"))

        except Exception as e:
            app.logger.error(f"ERRO CRÍTICO AO PROCESSAR FEEDBACK: {e}")
            flash("Ocorreu um erro ao guardar o feedback.", "error")
            return redirect(url_for("feedback"))

    return render_template("feedback.html")

# ---- Lista pública de feedbacks ---------------------------------------------
@app.route("/feedbacks")
def listar_feedbacks():
    try:
        conn = get_db_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("""
            SELECT NomeCliente, Classificacao, Comentario, DataEnvio
            FROM Feedbacks
            WHERE Aprovado = TRUE
            ORDER BY DataEnvio DESC
        """)
        feedbacks = cur.fetchall()
        conn.close()

        return render_template("feedbacks.html", feedbacks=feedbacks)

    except Exception as e:
        app.logger.error(f"Erro ao carregar feedbacks: {e}")
        flash("Erro ao carregar os feedbacks.", "error")
        return redirect(url_for("index"))

# ==========================================
# 💬 Admin — gestão de feedbacks
# ==========================================
@app.route("/admin/feedbacks", methods=["GET"])
def admin_feedbacks():
    if not session.get("is_admin"):
        flash("Acesso restrito a administradores.", "warning")
        return redirect(url_for("index"))

    estado = (request.args.get("estado") or "").strip()
    termo = (request.args.get("q") or "").strip()

    conn = get_db_connection()
    cur = conn.cursor(dictionary=True)

    query = """
        SELECT Id,
               NomeCliente,
               Classificacao,
               Comentario,
               DataEnvio,
               Aprovado
        FROM Feedbacks
        WHERE 1=1
    """
    params = []

    # filtro de estado
    if estado == "aprovado":
        query += " AND Aprovado = 1"
    elif estado == "pendente":
        query += " AND (Aprovado = 0 OR Aprovado IS NULL)"

    # filtro de pesquisa
    if termo:
        query += " AND (NomeCliente LIKE %s OR Comentario LIKE %s)"
        like = f"%{termo}%"
        params.extend([like, like])

    query += " ORDER BY DataEnvio DESC"

    cur.execute(query, tuple(params))
    feedbacks = cur.fetchall()
    conn.close()

    return render_template(
        "admin_feedbacks.html",
        feedbacks=feedbacks,
        estado=estado,
        termo=termo,
    )

@app.route("/admin/feedbacks/aprovar/<int:id>", methods=["GET", "POST"])
def aprovar_feedback(id):
    if not session.get("is_admin"):
        flash("Acesso restrito a administradores.", "warning")
        return redirect(url_for("index"))

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE Feedbacks SET Aprovado = 1 WHERE Id = %s", (id,))
    conn.commit()
    conn.close()

    flash("Feedback aprovado com sucesso!", "success")
    return redirect(url_for("admin_feedbacks"))

@app.route("/admin/feedbacks/<int:id>/remover", methods=["GET", "POST"])
def remover_feedback(id):
    if not session.get("is_admin"):
        flash("Acesso restrito.", "error")
        return redirect(url_for("index"))

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM Feedbacks WHERE Id = %s", (id,))
    conn.commit()
    conn.close()

    flash("Feedback removido com sucesso.", "success")
    return redirect(url_for("admin_feedbacks"))

# =============================================================================
# ⏰ LEMBRETES AUTOMÁTICOS (APROX. 1H ANTES)
# =============================================================================
def enviar_lembretes():
    """
    Job periódico para enviar lembrete de marcação ~1h antes.
    Requer coluna LembreteEnviado BOOLEAN na tabela Marcacoes.
    """
    with app.app_context():
        try:
            app.logger.info("A verificar marcações para lembrete...")
            conn = get_db_connection()
            cur = conn.cursor()

            cur.execute(
                """
                SELECT m.Id, u.Nome, u.Email, s.Nome, m.DataHora
                FROM Marcacoes m
                JOIN Utilizador u ON m.Cliente_id = u.Id
                JOIN Servicos s ON m.Servico_id = s.Id
                WHERE m.Estado IN ('Aprovado','Aprovada')
                  AND TIMESTAMPDIFF(MINUTE, NOW(), m.DataHora) BETWEEN 55 AND 65
                  AND (m.LembreteEnviado IS NULL OR m.LembreteEnviado = 0)
                """
            )
            marcacoes = cur.fetchall()

            for (mid, nome, email, servico, datahora) in marcacoes:
                html = render_template(
                    "emails/clientes/lembrete_email.html",
                    nome=nome,
                    servico=servico,
                    datahora=datahora.strftime("%d/%m/%Y %H:%M"),
                    current_year=datetime.now().year,
                )
                send_email("⏰ Lembrete de Marcação • Agenda Beleza", [email], html)
                cur.execute("UPDATE Marcacoes SET LembreteEnviado = 1 WHERE Id = %s", (mid,))
                conn.commit()

            conn.close()

            if marcacoes:
                app.logger.info(f"{len(marcacoes)} lembrete(s) enviados.")
        except Exception as e:
            app.logger.error(f"Erro no envio de lembretes: {e}")

# =============================================================================
# 📄 PÁGINAS LEGAIS (POLÍTICA / TERMOS)
# =============================================================================
@app.route("/politica-privacidade")
def politica_privacidade():
    return render_template("politica_privacidade.html")


@app.route("/termos")
def termos():
    return render_template("termos.html")

# =============================================================================
# ⏱️ SCHEDULER (APScheduler) — LEMBRETES DE MARCAÇÃO
# =============================================================================
ENABLE_SCHEDULER = os.getenv("ENABLE_SCHEDULER", "0") == "1"

if ENABLE_SCHEDULER:
    scheduler = APScheduler()
    scheduler.init_app(app)
    scheduler.start()
    scheduler.add_job(
        id="lembretes_marcacoes",
        func=enviar_lembretes,
        trigger="interval",
        minutes=5,
    )
    app.logger.info("✅ Scheduler ligado (ENABLE_SCHEDULER=1).")
else:
    app.logger.info("ℹ️ Scheduler desligado (ENABLE_SCHEDULER!=1).")

# =============================================================================
# ▶️ RUN (APENAS EM LOCAL)
# =============================================================================
if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", 5000)),
        debug=False,
    )
