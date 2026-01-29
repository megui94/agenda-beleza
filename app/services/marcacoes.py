# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Tuple

from flask import render_template, current_app

from .db import get_db_connection
from .email import send_email
from ..services.slots import is_slot_available

# =============================================================================
# ✅ ESTADOS (NORMALIZAÇÃO) + ATUALIZAÇÃO DE ESTADO COM EMAIL
# =============================================================================


def normalize_estado(estado: Optional[str]) -> Optional[str]:
    """
    Normaliza estados para evitar inconsistências.
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


def atualizar_estado_marcacao(marcacao_id: int, novo_estado: str) -> Tuple[bool, str]:
    """
    Atualiza o estado de uma marcação e envia o e-mail certo ao cliente.

    - Evita código repetido (aprovar/rejeitar/cancelar).
    - Garante notificação ao cliente quando o estado muda.
    - Evita e-mails duplicados se o estado já for igual.
    """
    novo_estado = normalize_estado(novo_estado)

    conn = get_db_connection()
    cur = conn.cursor(dictionary=True)
    try:
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
            return False, "Marcação não encontrada."

        estado_atual = normalize_estado(marc.get("Estado"))
        if estado_atual == novo_estado:
            return True, "A marcação já estava nesse estado."

        cur.execute(
            "UPDATE Marcacoes SET Estado = %s WHERE Id = %s",
            (novo_estado, marcacao_id),
        )
        conn.commit()

    finally:
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
        # outros estados: atualiza mas não envia e-mail automático
        return True, "Estado atualizado."

    ok_envio = send_email(assunto, [email_cliente], html, tags=tags)

    if ok_envio:
        current_app.logger.info(
            f"[MARCACOES] Estado {novo_estado} -> email enviado para {email_cliente} (ID {marcacao_id})"
        )
        return True, "Estado atualizado e e-mail enviado."

    current_app.logger.error(
        f"[MARCACOES] Falha ao enviar e-mail (ID {marcacao_id}) para {email_cliente}"
    )
    return True, "Estado atualizado (falha ao enviar e-mail)."


# =============================================================================
# 📅 CALENDÁRIO / SLOTS (HORÁRIOS DISPONÍVEIS)
# =============================================================================

def _parse_hhmm(s: str):
    return datetime.strptime(s, "%H:%M").time()


def _day_window(d: date):
    """
    Devolve (hora_abertura, hora_fecho) para esse dia, com base no Config.
    Se for dia fechado -> None
    """
    hours = current_app.config.get("BUSINESS_HOURS", {})
    win = hours.get(d.weekday())
    if not win:
        return None
    start_s, end_s = win
    return _parse_hhmm(start_s), _parse_hhmm(end_s)


def _get_service_duration_minutes(servico_id: int) -> int:
    """Busca a duração do serviço na BD (minutos)."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT Duracao FROM Servicos WHERE Id=%s", (servico_id,))
        row = cur.fetchone()
        return int(row[0]) if row and row[0] is not None else 0
    finally:
        conn.close()


def _get_existing_intervals(day: date) -> List[Tuple[datetime, datetime]]:
    """
    Intervalos ocupados no dia [(inicio, fim), ...]
    Calcula o fim com base na duração do serviço associado a cada marcação.
    Conta apenas estados que bloqueiam (Pendente/Aprovada...)
    """
    start = datetime.combine(day, datetime.min.time())
    end = start + timedelta(days=1)

    states = tuple(current_app.config.get(
        "BOOKING_BLOCK_STATES",
        ("Pendente", "Aprovada", "Aprovado")
    ))

    placeholders = ", ".join(["%s"] * len(states))

    conn = get_db_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            f"""
            SELECT m.DataHora AS inicio, s.Duracao AS duracao
            FROM Marcacoes m
            JOIN Servicos s ON s.Id = m.Servico_id
            WHERE m.DataHora >= %s AND m.DataHora < %s
              AND m.Estado IN ({placeholders})
            """,
            (start, end, *states),
        )
        rows = cur.fetchall() or []
    finally:
        conn.close()

    intervals: List[Tuple[datetime, datetime]] = []
    for r in rows:
        ini = r["inicio"]
        dur = int(r["duracao"] or 0)
        fim = ini + timedelta(minutes=dur)
        intervals.append((ini, fim))

    return intervals


def _overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    """True se os intervalos [a] e [b] se cruzam."""
    return a_start < b_end and b_start < a_end


def get_available_slots(day: date, servico_id: int) -> List[Dict[str, str]]:
    """
    Gera slots no horário de funcionamento do dia, com passo fixo (ex.: 150 min),
    removendo slots que colidem com marcações existentes.

    Retorna lista:
      [{"iso": "YYYY-MM-DDTHH:MM", "label": "HH:MM"}, ...]
    """
    window = _day_window(day)
    if not window:
        return []

    open_t, close_t = window
    duration = _get_service_duration_minutes(servico_id)
    if duration <= 0:
        return []

    step = int(current_app.config.get("SLOT_STEP_MINUTES", 150))

    start_dt = datetime.combine(day, open_t)
    close_dt = datetime.combine(day, close_t)

    # último início possível para caber dentro do horário
    last_start = close_dt - timedelta(minutes=duration)
    if last_start < start_dt:
        return []

    occupied = _get_existing_intervals(day)

    slots: List[Dict[str, str]] = []
    cur = start_dt
    while cur <= last_start:
        cand_start = cur
        cand_end = cur + timedelta(minutes=duration)

        if any(_overlaps(cand_start, cand_end, o_s, o_e) for (o_s, o_e) in occupied):
            cur += timedelta(minutes=step)
            continue

        slots.append({
            "iso": cand_start.strftime("%Y-%m-%dT%H:%M"),
            "label": cand_start.strftime("%H:%M"),
        })

        cur += timedelta(minutes=step)

    return slots


def is_slot_available(day: date, servico_id: int, start_dt: datetime) -> bool:
    """
    Validação FINAL no servidor antes de inserir na BD:
    confirma se o horário ainda está disponível.
    """
    iso = start_dt.strftime("%Y-%m-%dT%H:%M")
    return any(s["iso"] == iso for s in get_available_slots(day, servico_id))


