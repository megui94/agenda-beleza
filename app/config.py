# -*- coding: utf-8 -*-
import os

class Config:
    # Segurança
    SECRET_KEY = os.getenv("SECRET_KEY", "segredo-super-seguro")

    # URLs externas (Render)
    PREFERRED_URL_SCHEME = "https"

    # Opcional (útil em alguns deploys)
    SERVER_NAME = os.getenv("SERVER_NAME") or None

    # Admin / email
    MAIL_DEFAULT_SENDER = os.getenv("MAIL_DEFAULT_SENDER", os.getenv("ADMIN_EMAIL", "agenda.beleza.contato@gmail.com"))
    ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", MAIL_DEFAULT_SENDER)

    # Scheduler / lembretes
    ENABLE_SCHEDULER = os.getenv("ENABLE_SCHEDULER", "0") == "1"

    # Horário por dia da semana (0=segunda ... 6=domingo)
    BUSINESS_HOURS = {
        0: ("09:00", "18:00"),
        1: ("09:00", "18:00"),
        2: ("09:00", "18:00"),
        3: ("09:00", "18:00"),
        4: ("09:00", "18:00"),
        5: ("09:00", "13:00"),  # sábado
        6: None,                # domingo fechado
    }

    # Diferença mínima entre horários (2h30 = 150 min)
    SLOT_STEP_MINUTES = 150

    # Estados que contam como “ocupado”
    BOOKING_BLOCK_STATES = ("Pendente", "Aprovada", "Aprovado")
