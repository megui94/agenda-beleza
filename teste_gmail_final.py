from flask_mail import Mail, Message
from app import app

mail = Mail(app)

with app.app_context():
    msg = Message(
    "💅 Teste via Brevo SMTP",
    sender=("Agenda de Beleza", "agenda.beleza.contato@gmail.com"),
    recipients=["slowlydiogoo@gmail.com"],
    body="Este é um teste de envio via Brevo SMTP com Flask-Mail."
)


    try:
        mail.send(msg)
        print("✅ E-mail de teste enviado com sucesso!")
    except Exception as e:
        print("❌ Erro ao enviar e-mail:", e)
