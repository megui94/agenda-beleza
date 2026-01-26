# -*- coding: utf-8 -*-
from flask import render_template, current_app
from datetime import datetime

from .db import get_db_connection
from .email import send_email

def normalize_estado(estado):
    """
    Normaliza estados para evitar inconsistencias.
    Estados canonicos:
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

def atualizar_estado_marcacao(marcacao_id: int, novo_estado: str):
    """
    Atualiza o estado de uma marcacao e envia o e-mail certo ao cliente.

    - Evita codigo repetido (aprovar/rejeitar/cancelar).
    - Garante notificacao ao cliente quando o estado muda.
    - Evita e-mails duplicados se o estado ja for igual.
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
        return False, "Marcacao nao encontrada."

    estado_atual = normalize_estado(marc.get("Estado"))

    if estado_atual == novo_estado:
        conn.close()
        return True, "A marcacao ja estava nesse estado."

    cur.execute("UPDATE Marcacoes SET Estado = %s WHERE Id = %s", (novo_estado, marcacao_id))
    conn.commit()
    conn.close()

    email_cliente = marc.get("EmailCliente")
    if not email_cliente:
        return True, "Estado atualizado (cliente sem e-mail)."

    datahora_str = marc["DataHora"].strftime("%d/%m/%Y %H:%M")

    tags = ["marcacoes"]
    if novo_estado == "Aprovada":
        html = render_template(
            "emails/clientes/marcacao_aprovada.html",
            nome=marc["NomeCliente"],
            servico=marc["NomeServico"],
            datahora=datahora_str,
        )
        assunto = "✅ Marcacao Aprovada • Agenda Beleza"
        tags.append("aprovada")

    elif novo_estado == "Rejeitada":
        html = render_template(
            "emails/clientes/marcacao_rejeitada.html",
            nome=marc["NomeCliente"],
            servico=marc["NomeServico"],
            datahora=datahora_str,
        )
        assunto = "❌ Marcacao Rejeitada • Agenda Beleza"
        tags.append("rejeitada")

    elif novo_estado == "Cancelada":
        html = render_template(
            "emails/clientes/marcacao_cancelada.html",
            nome=marc["NomeCliente"],
            servico=marc["NomeServico"],
            data=datahora_str,
        )
        assunto = "❌ Marcacao Cancelada • Agenda Beleza"
        tags.append("cancelada")

    else:
        return True, "Estado atualizado."

    ok_envio = send_email(assunto, [email_cliente], html, tags=tags)
    if ok_envio:
        current_app.logger.info(
            f"[MARCACOES] Estado {novo_estado} -> e-mail enviado para {email_cliente} (ID {marcacao_id})"
        )
        return True, "Estado atualizado e e-mail enviado."

    current_app.logger.error(f"[MARCACOES] Falha ao enviar e-mail (ID {marcacao_id}) para {email_cliente}")
    return True, "Estado atualizado (falha ao enviar e-mail)."
