# -*- coding: utf-8 -*-
from pathlib import Path
import os

from dotenv import load_dotenv

# Carrega variáveis do ficheiro .env (útil em ambiente local)
load_dotenv()

from flask import Flask
from itsdangerous import URLSafeTimedSerializer

from .config import Config
from .logging_config import setup_logging
from .context import register_context
from .extensions import bcrypt
from .jobs.lembretes import configurar_lembretes

from .routes import auth as auth_routes
from .routes import marcacoes as marcacoes_routes
from .routes import admin as admin_routes
from .routes import public as public_routes
from .routes import feedback as feedback_routes

def create_app():
    # Caminhos absolutos para garantir que templates/static funcionam mesmo com package
    base_dir = Path(__file__).resolve().parent.parent
    templates_dir = base_dir / "templates"
    static_dir = base_dir / "static"

    app = Flask(
        __name__,
        template_folder=str(templates_dir),
        static_folder=str(static_dir),
    )
    app.config.from_object(Config)

    # SERVER_NAME apenas se estiver definido (evita bugs em dev)
    if app.config.get("SERVER_NAME"):
        app.config.pop("SERVER_NAME", None)

    setup_logging(app)
    register_context(app)

    # Brevo API key check
    if not os.getenv("BREVO_API_KEY"):
        app.logger.warning("BREVO_API_KEY nao definida - emails desativados (o site continua a funcionar).")

    bcrypt.init_app(app)

    serializer = URLSafeTimedSerializer(app.config["SECRET_KEY"])

    # Registar rotas
    auth_routes.register(app, serializer)
    marcacoes_routes.register(app)
    admin_routes.register(app)
    public_routes.register(app)
    feedback_routes.register(app)

    # Scheduler / lembretes
    configurar_lembretes(app)

    return app


# -----------------------------------------------------------------------------
# ✅ Compatibilidade com servidores WSGI (ex.: gunicorn "app:app")
#
# Nota: quando existe simultaneamente a pasta package "app/" e um ficheiro
# "app.py" na raiz, o Python normalmente importa primeiro o package.
# Ao expor a variável `app` aqui, garantimos que `gunicorn app:app` funciona
# sem ter de alterar o comando de arranque.
# -----------------------------------------------------------------------------
app = create_app()
