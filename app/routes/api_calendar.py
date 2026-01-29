# -*- coding: utf-8 -*-
from datetime import datetime, timedelta

from flask import jsonify, request

from ..services.slots import build_slot_events, available_days


def _parse_iso_date(s: str) -> datetime:
    # FullCalendar manda ISO (às vezes com Z). Aqui tratamos o básico.
    s = (s or "").replace("Z", "")
    return datetime.fromisoformat(s)


def register(app):
    if "api_slots" in app.view_functions:
        return
    @app.get("/api/slots")
    def api_slots():
        servico_id = int(request.args.get("servico_id", "0") or 0)
        if servico_id <= 0:
            return jsonify([])

        start = _parse_iso_date(request.args.get("start", ""))
        end = _parse_iso_date(request.args.get("end", ""))

        # FullCalendar usa 'end' exclusivo
        start_d = start.date()
        end_d = (end.date() - timedelta(days=1))

        events = build_slot_events(servico_id, start_d, end_d)
        return jsonify(events)

    @app.get("/api/available-days")
    def api_available_days():
        servico_id = int(request.args.get("servico_id", "0") or 0)
        if servico_id <= 0:
            return jsonify([])

        start = _parse_iso_date(request.args.get("start", ""))
        end = _parse_iso_date(request.args.get("end", ""))

        start_d = start.date()
        end_d = (end.date() - timedelta(days=1))

        days = available_days(servico_id, start_d, end_d)
        return jsonify(days)