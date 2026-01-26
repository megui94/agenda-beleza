# -*- coding: utf-8 -*-
import os
from flask import render_template, request, redirect, flash, session, url_for, abort

from ..services.db import get_db_connection
from ..services.marcacoes import atualizar_estado_marcacao, normalize_estado

def register(app):
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

    @app.route("/admin/update_marcacao", methods=["POST"])
    def admin_update_marcacao():
        """Atualiza estado de uma marcacao e notifica cliente."""
        if not session.get("is_admin"):
            flash("Acesso restrito.", "error")
            return redirect(url_for("index"))

        marcacao_id = request.form.get("id")
        acao_raw = request.form.get("acao")
        acao = normalize_estado(acao_raw)

        if (not marcacao_id) or (acao not in ("Aprovada", "Rejeitada", "Cancelada")):
            flash("Acao invalida.", "error")
            return redirect(url_for("admin_marcacoes"))

        ok, msg = atualizar_estado_marcacao(int(marcacao_id), acao)
        flash(msg, "success" if ok else "error")
        return redirect(url_for("admin_marcacoes"))

    @app.route("/admin/marcacoes/aprovar/<int:id>", endpoint="aprovar_marcacao")
    def admin_aprovar_marcacao(id):
        if not session.get("is_admin"):
            abort(403)

        ok, msg = atualizar_estado_marcacao(id, "Aprovada")
        flash(msg, "success" if ok else "error")
        return redirect(url_for("admin_marcacoes"))

    @app.route("/admin/marcacoes/rejeitar/<int:id>", endpoint="rejeitar_marcacao")
    def admin_rejeitar_marcacao(id):
        if not session.get("is_admin"):
            abort(403)

        ok, msg = atualizar_estado_marcacao(id, "Rejeitada")
        flash(msg, "info" if ok else "error")
        return redirect(url_for("admin_marcacoes"))

    @app.route("/admin/mensagens")
    def admin_mensagens():
        """Lista mensagens de contacto com pesquisa (apenas admin)."""
        if not session.get("is_admin"):
            flash("Acesso restrito a administracao.", "error")
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
        if not session.get("is_admin"):
            flash("Acesso restrito a administracao.", "error")
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

    @app.route("/admin/dashboard")
    def admin_dashboard():
        """Dashboard de resumo para administrador (KPIs principais)."""
        if not session.get("is_admin"):
            flash("Acesso restrito: apenas administradores.", "error")
            return redirect(url_for("index"))

        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("SELECT COUNT(*) FROM Marcacoes")
        total_marcacoes = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM Marcacoes WHERE Estado = 'Pendente'")
        pendentes = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM Marcacoes WHERE Estado IN ('Aprovado','Aprovada')")
        aprovadas = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM Utilizador WHERE IsAdmin = FALSE OR IsAdmin IS NULL")
        total_clientes = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM Feedbacks WHERE Aprovado = TRUE")
        total_feedbacks_aprovados = cur.fetchone()[0]

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
