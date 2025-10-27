from flask_mail import Mail, Message
from app import app

mail = Mail(app)

with app.app_context():
    msg = Message("🔑 Teste de e-mail",
                  sender=app.config['MAIL_USERNAME'],
                  recipients=["slowlydiogoo@gmail.com"])
    msg.body = "Olá! Este é um teste do Flask-Mail."
    mail.send(msg)

print("✅ E-mail de teste enviado com sucesso!")
