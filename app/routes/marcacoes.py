# -*- coding: utf-8 -*-
from datetime import datetime
from flask import render_template, request, redirect, flash, session, url_for

from ..services.db import get_db_connection
from ..services.email import send_email
from ..services.slots import is_slot_available

def register(app):
    @app.route("/agendar")
    def agendar_redirect():
        """Rota antiga de /agendar. Redireciona para /marcacoes."""
        if not session.get("user_id"):
            flash("Faca login para agendar a sua marcacao.", "info")
            session["next"] = "marcacoes"
            return redirect(url_for("login"))
        return redirect(url_for("marcacoes"))

    @app.route("/marcacoes", methods=["GET", "POST"])
    def marcacoes():
        """Criar nova marcacao e listar servicos disponiveis."""
        if request.method == "POST":
            if not session.get("user_id"):
                flash("Inicie sessao para fazer uma marcacao.", "error")
                return redirect(url_for("login"))

            servico_id = request.form.get("servico_id")
            datahora = request.form.get("datahora")
            observacoes = request.form.get("observacoes", "")

            if not servico_id or not datahora:
                flash("Selecione um servico e horario.", "error")
                return redirect(url_for("marcacoes"))

            try:
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
                    raise ValueError("Formato invalido")

                try:
                    servico_id_int = int(servico_id)
                except Exception:
                    servico_id_int = 0

                if not servico_id_int or not is_slot_available(servico_id_int, datahora_obj):
                    flash("Este dia/horário já não está disponível. Escolha outro.", "error")
                    return redirect(url_for("marcacoes"))

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

                cur.execute("SELECT Nome FROM Servicos WHERE Id=%s", (servico_id,))
                servico_nome = (cur.fetchone() or ["-"])[0]

                html_cliente = render_template(
                    "emails/clientes/marcacao_email.html",
                    nome=session.get("nome", "Cliente"),
                    datahora=datahora_obj.strftime("%d/%m/%Y %H:%M"),
                    servico=servico_nome,
                )
                send_email("🗓️ Marcacao registada com sucesso", [session["email"]], html_cliente)

                html_admin = render_template(
                    "emails/admin/nova_marcacao_admin.html",
                    nome_cliente=session.get("nome", "Cliente"),
                    servico=servico_nome,
                    datahora=datahora_obj.strftime("%d/%m/%Y %H:%M"),
                    observacoes=observacoes,
                )
                admin_email = app.config.get("ADMIN_EMAIL") or app.config.get("MAIL_DEFAULT_SENDER")
                send_email(
                    "📢 Nova marcacao pendente",
                    [admin_email],
                    html_admin,
                    reply_to=session["email"],
                )

                conn.close()
                flash("Marcacao enviada com sucesso!", "success")

            except ValueError:
                flash("Formato de data e hora invalido. Por favor, escolha novamente.", "error")
            except Exception as e:
                app.logger.error(f"Erro ao criar marcacao: {e}")
                flash("Ocorreu um erro ao criar a marcacao.", "error")

            return redirect(url_for("minhas_marcacoes"))

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT Id, Nome FROM Servicos")
        servicos = cur.fetchall()
        conn.close()

        return render_template("marcacoes.html", servicos=servicos)

    @app.route("/minhas_marcacoes")
    def minhas_marcacoes():
        """Lista todas as marcacoes do utilizador autenticado."""
        if not session.get("user_id"):
            session.pop("marcacao_sucesso", None)
            flash("Inicie sessao para aceder as suas marcacoes.", "error")
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

        return render_template("minhas_marcacoes.html", marcacoes=marcacoes, now=datetime.now())

    @app.route("/cancelar_marcacao/<int:id>", methods=["POST"])
    def cancelar_marcacao(id):
        """Cancelar marcacao pelo cliente (ate 4 horas antes)."""
        if "user_id" not in session:
            flash("Tem de iniciar sessao para cancelar uma marcacao.", "warning")
            return redirect(url_for("login"))

        try:
            conn = get_db_connection()
            cur = conn.cursor(dictionary=True)

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
                flash("Marcacao nao encontrada.", "danger")
                conn.close()
                return redirect(url_for("minhas_marcacoes"))

            agora = datetime.now()
            diferenca = marcacao["DataHora"] - agora

            if diferenca.total_seconds() < 4 * 3600:
                flash("Ja nao pode cancelar a menos de 4h da hora marcada.", "warning")
                conn.close()
                return redirect(url_for("minhas_marcacoes"))

            cur.execute("UPDATE Marcacoes SET Estado = 'Cancelada' WHERE Id = %s", (id,))
            conn.commit()
            conn.close()

            try:
                data_str = marcacao["DataHora"].strftime("%d/%m/%Y %H:%M")

                # 1) Cliente
                html_cliente = render_template(
                    "emails/clientes/marcacao_cancelada.html",
                    nome=session.get("nome", "Cliente"),
                    data=data_str,
                    servico=marcacao["NomeServico"],
                )
                send_email(
                    "❌ Marcacao cancelada • Agenda Beleza",
                    [session.get("email")],
                    html_cliente,
                    tags=["marcacoes", "cancelada", "cliente"],
                )

                # 2) Admin
                admin_email = app.config.get("ADMIN_EMAIL") or app.config.get("MAIL_DEFAULT_SENDER")
                if admin_email:
                    html_admin = render_template(
                        "emails/admin/marcacao_cancelada_admin.html",
                        nome_cliente=session.get("nome", "Cliente"),
                        email_cliente=session.get("email"),
                        servico=marcacao["NomeServico"],
                        data=data_str,
                        observacoes=marcacao.get("Observacoes") or "",
                        marcacao_id=id,
                        painel_url=url_for("admin_marcacoes", _external=True),
                    )
                    send_email(
                        "⚠️ Marcacao cancelada pela cliente • Agenda Beleza",
                        [admin_email],
                        html_admin,
                        reply_to=session.get("email"),
                        tags=["marcacoes", "cancelada", "admin"],
                    )

            except Exception as e:
                app.logger.error(f"Erro ao enviar e-mails de cancelamento: {e}")

            flash("Marcacao cancelada com sucesso.", "success")
            return redirect(url_for("minhas_marcacoes"))

        except Exception as e:
            app.logger.error(f"Erro ao cancelar marcacao: {e}")
            flash("Erro ao cancelar marcacao.", "danger")
            return redirect(url_for("minhas_marcacoes"))
