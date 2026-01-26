# -*- coding: utf-8 -*-
from datetime import datetime
from flask import render_template, request, redirect, flash, url_for

from ..services.db import get_db_connection
from ..services.email import send_email

def register(app):
    @app.route("/")
    def index():
        """Página inicial — mostra alguns feedbacks aprovados (até 6)."""
        feedbacks = []
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute(
                """
                SELECT Id, NomeCliente, Classificacao, Comentario, DataEnvio
                FROM Feedbacks
                WHERE Aprovado = TRUE
                ORDER BY DataEnvio DESC
                LIMIT 6
                """
            )
            feedbacks = cur.fetchall()
            conn.close()
        except Exception as e:
            # Mantém o site a funcionar mesmo se a BD estiver temporariamente indisponível
            app.logger.error(f"Erro ao carregar feedbacks na home: {e}")
        return render_template("index.html", feedbacks=feedbacks)

    @app.route("/sobre")
    def sobre():
        return render_template("sobre.html")

    @app.route("/servicos")
    def servicos():
        """Lista de serviços com pesquisa opcional."""
        termo = (request.args.get("q") or "").strip()
        servs = []
        try:
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
        except Exception as e:
            app.logger.error(f"Erro ao carregar serviços: {e}")
        return render_template("servicos.html", servicos=servs)


    @app.route("/contato", methods=["GET", "POST"])
    def contato():
        """Pagina de contacto com envio de mensagem para BD + e-mails (cliente/admin)."""
        if request.method == "POST":
            assunto = (request.form.get("assunto") or "").strip()
            nome = (request.form.get("nome") or "").strip()
            email = (request.form.get("email") or "").strip()
            mensagem = (request.form.get("mensagem") or "").strip()

            if not (assunto and nome and email and mensagem):
                flash("Por favor, preencha todos os campos antes de enviar.", "error")
                return redirect(url_for("contato"))

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

            # Email para admin
            try:
                send_email(
                    "📥 Novo contacto recebido • Agenda Beleza",
                    [app.config.get("ADMIN_EMAIL")],
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

            # Email confirmacao cliente
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
                app.logger.error(f"Erro ao enviar e-mail de confirmacao: {e}")

            flash("Mensagem enviada com sucesso!", "success")
            return redirect(url_for("contato"))

        return render_template("contato.html")

    @app.route("/politica-privacidade")
    def politica_privacidade():
        return render_template("politica_privacidade.html")

    @app.route("/termos")
    def termos():
        return render_template("termos.html")
