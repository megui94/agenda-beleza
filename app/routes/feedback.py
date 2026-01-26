# -*- coding: utf-8 -*-
from datetime import datetime
from flask import render_template, request, redirect, flash, session, url_for

from ..services.db import get_db_connection
from ..services.email import send_email

def register(app):
    @app.route("/feedback", methods=["GET", "POST"])
    def feedback():
        """Formulario para cliente autenticado enviar feedback."""
        if "user_id" not in session:
            flash("Tem de iniciar sessao para enviar feedback.", "warning")
            return redirect(url_for("login"))

        if request.method == "POST":
            nome = session.get("nome") or "Cliente"
            email_cliente = session.get("email")
            classificacao = request.form.get("classificacao")
            comentario = request.form.get("comentario")

            if not classificacao or not comentario:
                flash("Por favor, preencha a classificacao e o comentario.", "warning")
                return redirect(url_for("feedback"))

            try:
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

                data_envio = datetime.now()
                admin_email = app.config.get("ADMIN_EMAIL", "agenda.beleza.contato@gmail.com")

                # email cliente
                if email_cliente:
                    try:
                        html_cliente = render_template(
                            "emails/clientes/feedback_confirmacao.html",
                            nome=nome,
                            classificacao=classificacao,
                            comentario=comentario,
                            data_envio=data_envio,
                        )
                        send_email("💬 Recebemos o seu feedback • Agenda Beleza", [email_cliente], html_cliente)
                        app.logger.info(f"E-mail de confirmacao de feedback enviado para {email_cliente}")
                    except Exception as e:
                        app.logger.error(f"Erro ao enviar e-mail de confirmacao para cliente: {e}")

                # email admin
                try:
                    html_admin = render_template(
                        "emails/admin/novo_feedback.html",
                        nome=nome,
                        email_cliente=email_cliente,
                        classificacao=classificacao,
                        comentario=comentario,
                        data_envio=data_envio,
                    )
                    send_email("📥 Novo feedback recebido • Agenda Beleza", [admin_email], html_admin)
                    app.logger.info(f"E-mail de novo feedback enviado para admin ({admin_email})")
                except Exception as e:
                    app.logger.error(f"Erro ao enviar e-mail de novo feedback para admin: {e}")

                flash("Feedback enviado com sucesso! 🌸", "success")
                return redirect(url_for("listar_feedbacks"))

            except Exception as e:
                app.logger.error(f"ERRO CRITICO AO PROCESSAR FEEDBACK: {e}")
                flash("Ocorreu um erro ao guardar o feedback.", "error")
                return redirect(url_for("feedback"))

        return render_template("feedback.html")

    @app.route("/feedbacks")
    def listar_feedbacks():
        try:
            conn = get_db_connection()
            cur = conn.cursor(dictionary=True)
            cur.execute(
                """
                SELECT NomeCliente, Classificacao, Comentario, DataEnvio
                FROM Feedbacks
                WHERE Aprovado = TRUE
                ORDER BY DataEnvio DESC
                """
            )
            feedbacks = cur.fetchall()
            conn.close()

            return render_template("feedbacks.html", feedbacks=feedbacks)

        except Exception as e:
            app.logger.error(f"Erro ao carregar feedbacks: {e}")
            flash("Erro ao carregar os feedbacks.", "error")
            return redirect(url_for("index"))

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

        if estado == "aprovado":
            query += " AND Aprovado = 1"
        elif estado == "pendente":
            query += " AND (Aprovado = 0 OR Aprovado IS NULL)"

        if termo:
            query += " AND (NomeCliente LIKE %s OR Comentario LIKE %s)"
            like = f"%{termo}%"
            params.extend([like, like])

        query += " ORDER BY DataEnvio DESC"

        cur.execute(query, tuple(params))
        feedbacks = cur.fetchall()
        conn.close()

        return render_template("admin_feedbacks.html", feedbacks=feedbacks, estado=estado, termo=termo)

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
