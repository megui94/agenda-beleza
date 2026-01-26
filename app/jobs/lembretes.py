# -*- coding: utf-8 -*-
from datetime import datetime
from flask import render_template

from ..extensions import scheduler
from ..services.db import get_db_connection
from ..services.email import send_email

def enviar_lembretes():
    """
    Job periodico para enviar lembrete de marcacao ~1h antes.
    Requer coluna LembreteEnviado BOOLEAN na tabela Marcacoes.
    """
    app = getattr(scheduler, "app", None)
    if app is None:
        return

    with app.app_context():
        try:
            app.logger.info("A verificar marcacoes para lembrete...")
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
                send_email("⏰ Lembrete de Marcacao • Agenda Beleza", [email], html)
                cur.execute("UPDATE Marcacoes SET LembreteEnviado = 1 WHERE Id = %s", (mid,))
                conn.commit()

            conn.close()

            if marcacoes:
                app.logger.info(f"{len(marcacoes)} lembrete(s) enviados.")
        except Exception as e:
            app.logger.error(f"Erro no envio de lembretes: {e}")

def configurar_lembretes(app):
    enable = bool(app.config.get("ENABLE_SCHEDULER", False))
    if enable:
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
