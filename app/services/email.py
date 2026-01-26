# -*- coding: utf-8 -*-
import os
import re
import time
import logging

import sib_api_v3_sdk
from sib_api_v3_sdk.rest import ApiException

from flask import current_app

def _logger():
    try:
        return current_app.logger
    except Exception:
        return logging.getLogger(__name__)

def _normalize_email_list(recipients):
    """Normaliza lista de destinatarios e remove e-mails invalidos/vazios."""
    if not recipients:
        return []
    if isinstance(recipients, str):
        recipients = [recipients]

    email_re = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    out = []
    seen = set()
    for r in recipients:
        if r is None:
            continue
        e = str(r).strip()
        if not e:
            continue
        # Remove nomes do tipo "Nome <email@x.com>"
        m = re.search(r"<([^>]+)>", e)
        if m:
            e = m.group(1).strip()
        if not email_re.match(e):
            continue
        key = e.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out

def send_email(subject, recipients, html_body, reply_to=None, *, tags=None, max_retries=3):
    """
    Envia e-mails via API do Brevo (TransactionalEmailsApi).

    - Retorna True/False (para nao falhar em silencio).
    - Faz retry em erros transitorios (429/5xx/timeouts).
    - Regista logs detalhados para diagnosticar bloqueios por IP / chave invalida.
    """
    log = _logger()
    api_key = os.getenv("BREVO_API_KEY")
    sender_email = os.getenv("MAIL_DEFAULT_SENDER", os.getenv("ADMIN_EMAIL", "agenda.beleza.contato@gmail.com"))

    to_emails = _normalize_email_list(recipients)
    if not to_emails:
        log.warning("[BREVO] Nenhum destinatario valido. Email nao enviado.")
        return False

    if not api_key:
        log.error("[BREVO] Falha: BREVO_API_KEY nao configurada. Emails desativados.")
        return False

    configuration = sib_api_v3_sdk.Configuration()
    configuration.api_key["api-key"] = api_key

    api_client = sib_api_v3_sdk.ApiClient(configuration)
    api_instance = sib_api_v3_sdk.TransactionalEmailsApi(api_client)

    sender = {"name": "Agenda de Beleza", "email": sender_email}
    to_list = [{"email": e} for e in to_emails]

    email_data = sib_api_v3_sdk.SendSmtpEmail(
        to=to_list,
        sender=sender,
        subject=str(subject or "").strip() or "(sem assunto)",
        html_content=html_body or "",
        reply_to={"email": reply_to} if reply_to else None,
        tags=tags if tags else None,
    )

    def _is_ip_block(body_text: str) -> bool:
        t = (body_text or "").lower()
        return ("unauthorized" in t and "ip" in t) or ("ip" in t and "authorize" in t)

    backoff = 1
    for attempt in range(1, int(max_retries) + 1):
        try:
            api_instance.send_transac_email(email_data)
            log.info(f"[BREVO] Email enviado -> {to_emails} | assunto={subject!r}")
            return True

        except ApiException as e:
            status = getattr(e, "status", None)
            body = getattr(e, "body", "") or ""
            log.error(
                f"[BREVO] Erro ao enviar (tentativa {attempt}/{max_retries}) "
                f"status={status} body={body}"
            )

            if _is_ip_block(body):
                log.error(
                    "[BREVO] Bloqueado por IP. Autoriza o(s) IP(s)/range(s) do servidor "
                    "na Brevo (Settings -> Security -> Authorized IPs)."
                )
                return False

            retriable = (
                status in (408, 425, 429)
                or (isinstance(status, int) and 500 <= status <= 599)
                or status is None
            )
            if (not retriable) or attempt == int(max_retries):
                return False

        except Exception as e:
            log.error(f"[BREVO] Excecao ao enviar (tentativa {attempt}/{max_retries}): {e}")
            if attempt == int(max_retries):
                return False

        time.sleep(backoff)
        backoff = min(backoff * 2, 8)

    return False
