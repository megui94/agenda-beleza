# -*- coding: utf-8 -*-
import os
import re
from datetime import datetime
from flask import render_template, request, redirect, flash, session, url_for

from itsdangerous import SignatureExpired, BadSignature

from ..services.db import get_db_connection
from ..services.email import send_email
from ..extensions import bcrypt

def register(app, serializer):
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
                if not user.get("EmailVerificado"):
                    flash("Por favor, confirme o seu e-mail antes de iniciar sessao.", "warning")
                    return redirect(url_for("login"))

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
        """Termina a sessao atual."""
        session.clear()
        flash("Saiu da conta com sucesso.", "info")
        return redirect(url_for("index"))

    @app.route("/registar", methods=["GET", "POST"])
    def registar():
        """Criacao de conta + envio de e-mail de confirmacao."""
        if request.method == "POST":
            nome = (request.form.get("nome") or "").strip()
            email = (request.form.get("email") or "").strip().lower()
            telefone = (request.form.get("telefone") or "").strip()
            password = request.form.get("password") or ""
            confirm = request.form.get("confirm_password") or ""

            if password != confirm:
                flash("As senhas nao coincidem.", "error")
                return redirect(url_for("registar"))

            if not re.match(r"^(?=.*[A-Z])(?=.*\d).{8,}$", password):
                flash(
                    "A senha deve ter pelo menos 8 caracteres, incluindo uma maiuscula e um numero.",
                    "error",
                )
                return redirect(url_for("registar"))

            conn = get_db_connection()
            cur = conn.cursor(dictionary=True)
            cur.execute("SELECT * FROM Utilizador WHERE Email = %s", (email,))
            existing = cur.fetchone()

            if existing:
                if not existing.get("EmailVerificado"):
                    token = serializer.dumps(email, salt="email-confirm")
                    link = url_for("confirmar_email", token=token, _external=True, _scheme="https")

                    html_cliente = render_template(
                        "emails/clientes/reenviar_confirmacao.html",
                        nome=existing["Nome"],
                        confirm_url=link,
                    )
                    send_email(
                        "🔁 Confirmacao pendente • Agenda Beleza",
                        [email],
                        html_cliente,
                    )

                    flash(
                        "Ja existe uma conta com este e-mail, mas ainda nao foi verificada. "
                        "Enviamos novamente o e-mail de confirmacao.",
                        "info",
                    )
                    conn.close()
                    return redirect(url_for("login"))
                else:
                    flash("Ja existe uma conta registada com este e-mail.", "error")
                    conn.close()
                    return redirect(url_for("login"))

            hashed_pw = bcrypt.generate_password_hash(password).decode("utf-8")

            try:
                cur.execute(
                    "INSERT INTO Utilizador (Nome, Email, Telefone, Password) VALUES (%s, %s, %s, %s)",
                    (nome, email, telefone, hashed_pw),
                )
                conn.commit()

                token = serializer.dumps(email, salt="email-confirm")
                link = url_for("confirmar_email", token=token, _external=True, _scheme="https")

                html_cliente = render_template(
                    "emails/clientes/confirmacao_email.html",
                    nome=nome,
                    confirm_url=link,
                )
                send_email("📧 Confirme o seu e-mail • Agenda Beleza", [email], html_cliente)

                html_admin = render_template(
                    "emails/admin/novo_registo_admin.html",
                    nome=nome,
                    email=email,
                    telefone=telefone,
                    data=datetime.now().strftime("%d/%m/%Y %H:%M"),
                )
                send_email(
                    "👤 Nova cliente registada • Agenda Beleza",
                    [os.getenv("ADMIN_EMAIL", os.getenv("MAIL_DEFAULT_SENDER"))],
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
            flash("O link de confirmacao expirou. Faca login e solicite novo envio.", "error")
            return redirect(url_for("login"))
        except BadSignature:
            flash("Link de confirmacao invalido.", "error")
            return redirect(url_for("login"))

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("UPDATE Utilizador SET EmailVerificado = 1 WHERE Email = %s", (email,))
        conn.commit()
        conn.close()

        flash("E-mail confirmado com sucesso! Ja pode iniciar sessao.", "success")
        return redirect(url_for("login"))

    @app.route("/reset_request", methods=["GET", "POST"])
    def reset_request():
        """Formulario para pedir recuperacao de senha."""
        if request.method == "POST":
            email = (request.form.get("email") or "").strip().lower()
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("SELECT Id, Nome FROM Utilizador WHERE Email=%s", (email,))
            user = cur.fetchone()
            conn.close()

            if not user:
                flash("E-mail nao encontrado.", "error")
                return render_template("reset_request.html")

            token = serializer.dumps(email, salt="reset-salt")
            reset_link = url_for("reset_token", token=token, _external=True)
            html = render_template(
                "emails/clientes/reset_email.html",
                nome=user[1],
                reset_link=reset_link,
            )
            send_email("🔐 Recuperacao de Senha • Agenda Beleza", [email], html)
            flash("Enviamos um link de redefinicao.", "info")
            return redirect(url_for("login"))

        return render_template("reset_request.html")

    @app.route("/reset/<token>", methods=["GET", "POST"])
    def reset_token(token):
        """Pagina de redefinicao de senha a partir do link enviado por e-mail."""
        try:
            email = serializer.loads(token, salt="reset-salt", max_age=1800)
        except (SignatureExpired, BadSignature):
            flash("O link expirou ou e invalido.", "error")
            return redirect(url_for("reset_request"))

        if request.method == "POST":
            nova = request.form.get("password") or ""
            confirm = request.form.get("confirm_password") or ""

            if nova != confirm:
                flash("As senhas nao coincidem.", "error")
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
