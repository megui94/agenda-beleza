# -*- coding: utf-8 -*-

from __future__ import annotations

from datetime import datetime, date, time, timedelta
from functools import lru_cache
from typing import Dict, List, Tuple, Optional, Set, DefaultDict
from collections import defaultdict

from .db import get_db_connection

# Configuração de horários / slots
SLOT_STEP_MINUTES = 150

BUSINESS_HOURS: Dict[int, Tuple[time, time]] = {
    0: (time(9, 0), time(19, 0)),  # Seg
    1: (time(9, 0), time(19, 0)),  # Ter
    2: (time(9, 0), time(19, 0)),  # Qua
    3: (time(9, 0), time(19, 0)),  # Qui
    4: (time(9, 0), time(19, 0)),  # Sex
    5: (time(10, 0), time(14, 0)), # Sáb 
    # 6 (Domingo) fechado
}

# Estados que NÃO bloqueiam intervalos (cancelou ou foi rejeitada)
NON_BLOCKING_STATES = ("Cancelada", "Rejeitada")

# Helpers
def _daterange(start_d: date, end_d: date):
    """Itera dias de start_d até end_d (inclusive)."""
    if end_d < start_d:
        return
    cur = start_d
    while cur <= end_d:
        yield cur
        cur = cur + timedelta(days=1)


@lru_cache(maxsize=128)
def _get_servico_duracao(servico_id: int) -> int:
    """Duração (minutos) do serviço."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT Duracao FROM Servicos WHERE Id=%s", (servico_id,))
    row = cur.fetchone()
    conn.close()

    # fallback seguro
    return int((row[0] if row and row[0] is not None else 60) or 60)


def _get_day_window(d: date) -> Optional[Tuple[datetime, datetime]]:
    """Devolve (open_dt, close_dt) para o dia d, ou None se estiver fechado."""
    window = BUSINESS_HOURS.get(d.weekday())
    if not window:
        return None
    start_t, end_t = window
    open_dt = datetime.combine(d, start_t)
    close_dt = datetime.combine(d, end_t)
    return open_dt, close_dt


def _overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    """True se [a_start,a_end] e [b_start,b_end] se sobrepõem (overlap)."""
    # Overlap real: um começa antes do outro acabar e acaba depois do outro começar
    return a_start < b_end and a_end > b_start


def _booked_intervals_map(range_start: datetime, range_end: datetime) -> Dict[str, List[Tuple[datetime, datetime]]]:
    """
    Mapa date_iso -> lista de intervalos (start,end) já marcados dentro de [range_start, range_end).

    - range_end é EXCLUSIVO.
    - Para obter o 'end' da marcação, junta com Servicos.Duracao.
    - Ignora estados Cancelada/Rejeitada (NON_BLOCKING_STATES).
    """
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            m.DataHora,
            COALESCE(s.Duracao, 60) AS DuracaoMin,
            DATE(m.DataHora) AS Dia
        FROM Marcacoes m
        JOIN Servicos s ON s.Id = m.Servico_id
        WHERE m.DataHora >= %s AND m.DataHora < %s
          AND (m.Estado IS NULL OR m.Estado NOT IN (%s, %s))
        ORDER BY m.DataHora ASC
        """,
        (range_start, range_end, NON_BLOCKING_STATES[0], NON_BLOCKING_STATES[1]),
    )
    rows = cur.fetchall()
    conn.close()

    out: DefaultDict[str, List[Tuple[datetime, datetime]]] = defaultdict(list)
    for (start_dt, dur_min, dia) in rows:
        if not start_dt or not dia:
            continue
        try:
            dur = timedelta(minutes=int(dur_min or 60))
        except Exception:
            dur = timedelta(minutes=60)
        end_dt = start_dt + dur
        out[str(dia)].append((start_dt, end_dt))

    return dict(out)


def _booked_intervals_for_day(d: date) -> List[Tuple[datetime, datetime]]:
    """Lista intervalos marcados num dia específico."""
    day_start = datetime.combine(d, time(0, 0))
    day_end = day_start + timedelta(days=1)  # exclusivo
    return _booked_intervals_map(day_start, day_end).get(d.isoformat(), [])

# API principal
def get_available_slots(d: date, servico_id: int) -> List[Tuple[datetime, datetime]]:
    """
    Lista de slots disponíveis (start,end) num dia específico.
    - o dia pode ter várias marcações;
    - devolvemos apenas slots que NÃO colidem com marcações existentes.
    """
    window = _get_day_window(d)
    if not window:
        return []

    duracao_min = _get_servico_duracao(servico_id)
    open_dt, close_dt = window

    # não sugerir slots no passado
    now = datetime.now()
    slots: List[Tuple[datetime, datetime]] = []

    step = timedelta(minutes=SLOT_STEP_MINUTES)
    dur = timedelta(minutes=duracao_min)

    booked_intervals = _booked_intervals_for_day(d)

    start = open_dt
    last_start = close_dt - dur
    while start <= last_start:
        end = start + dur

        # condições base: dentro do horário e não no passado
        if end <= close_dt and start >= now:
            # novo filtro: não pode sobrepor nenhuma marcação existente
            if not any(_overlaps(start, end, b_start, b_end) for (b_start, b_end) in booked_intervals):
                slots.append((start, end))
        start += step
    return slots

def build_slot_events(servico_id: int, start_d: date, end_d: date) -> List[dict]:
    """Formata slots como events do FullCalendar (apenas para semana/dia)."""
    duration_min = _get_servico_duracao(servico_id)

    # consulta única para todas as marcações do intervalo (mais rápido)
    range_start = datetime.combine(start_d, time(0, 0))
    range_end = datetime.combine(end_d + timedelta(days=1), time(0, 0))  # exclusivo
    booked_map = _booked_intervals_map(range_start, range_end)

    events: List[dict] = []
    now = datetime.now()

    step = timedelta(minutes=SLOT_STEP_MINUTES)
    dur = timedelta(minutes=duration_min)

    for d in _daterange(start_d, end_d):
        window = _get_day_window(d)
        if not window:
            continue

        open_dt, close_dt = window
        booked_intervals = booked_map.get(d.isoformat(), [])

        start = open_dt
        last_start = close_dt - dur

        while start <= last_start:
            end = start + dur
            if end <= close_dt and start >= now:
                if not any(_overlaps(start, end, b_start, b_end) for (b_start, b_end) in booked_intervals):
                    events.append(
                        {
                            "id": f"{servico_id}-{start.strftime('%Y%m%d%H%M')}",
                            "title": f"Disponível • {start.strftime('%H:%M')}",
                            "start": start.strftime("%Y-%m-%dT%H:%M:%S"),
                            "end": end.strftime("%Y-%m-%dT%H:%M:%S"),
                            "classNames": ["slot-available"],
                        }
                    )
            start += step
    return events

def available_days(servico_id: int, start_d: date, end_d: date) -> List[str]:
    """
    Dias que têm pelo menos 1 slot disponível (sem overlap com marcações).

    Útil se quiseres, por exemplo, pintar dias no mês ou bloquear dias sem vagas.
    """
    duration_min = _get_servico_duracao(servico_id)

    range_start = datetime.combine(start_d, time(0, 0))
    range_end = datetime.combine(end_d + timedelta(days=1), time(0, 0))  # exclusivo
    booked_map = _booked_intervals_map(range_start, range_end)

    out: List[str] = []
    today0 = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).date()

    step = timedelta(minutes=SLOT_STEP_MINUTES)
    dur = timedelta(minutes=duration_min)

    for d in _daterange(start_d, end_d):
        if d < today0:
            continue

        window = _get_day_window(d)
        if not window:
            continue

        open_dt, close_dt = window
        booked_intervals = booked_map.get(d.isoformat(), [])

        start = open_dt
        last_start = close_dt - dur
        has_any = False

        while start <= last_start:
            end = start + dur
            if end <= close_dt and start >= datetime.now():
                if not any(_overlaps(start, end, b_start, b_end) for (b_start, b_end) in booked_intervals):
                    has_any = True
                    break
            start += step

        if has_any:
            out.append(d.isoformat())
    return out

def is_slot_available(servico_id: int, start_dt: datetime) -> bool:
    """Verificação final (anti-dupla-reserva)."""
    d = start_dt.date()
    return any(s == start_dt for (s, _) in get_available_slots(d, servico_id))
