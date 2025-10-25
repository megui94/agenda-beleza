import smtplib, ssl
from email.mime.text import MIMEText
from dotenv import load_dotenv
import os

load_dotenv()

sender = os.getenv("MAIL_USERNAME")
password = os.getenv("MAIL_PASSWORD")

msg = MIMEText("🚀 Teste de envio via Gmail com Flask (SSL ativo)")
msg["Subject"] = "Teste Flask Agenda"
msg["From"] = sender
msg["To"] = sender

print("🔌 Ligando ao Gmail SMTP (porta 465)...")

try:
    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
        server.login(sender, password)
        server.sendmail(sender, sender, msg.as_string())
        print("✅ E-mail enviado com sucesso!")
except smtplib.SMTPAuthenticationError as e:
    print("❌ Falha de autenticação:", e)
except smtplib.SMTPServerDisconnected as e:
    print("⚠️ O Gmail encerrou a conexão:", e)
except Exception as e:
    print("💥 Erro inesperado:", e)
