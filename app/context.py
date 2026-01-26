# -*- coding: utf-8 -*-
import traceback
from datetime import datetime, timezone
from flask import session

def register_context(app):
    @app.context_processor
    def inject_globals():
        return {
            "current_year": datetime.now(timezone.utc).year,
            "is_admin": bool(session.get("is_admin", False)),
            "user_nome": session.get("nome"),
            "user_email": session.get("email"),
        }

    @app.errorhandler(500)
    def internal_error(_):
        app.logger.error(traceback.format_exc())
        return "Ocorreu um erro interno no servidor.", 500
