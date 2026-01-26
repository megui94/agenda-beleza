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
