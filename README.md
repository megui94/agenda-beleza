# Agenda Beleza — projeto reorganizado

Este ZIP mantém as mesmas funcionalidades, templates (incl. emails) e ficheiros estáticos,
mas reorganiza o código Python para ficar mais claro e fácil de manter.

## Como correr (local)
```bash
pip install -r requirements.txt
python app.py
```

## Deploy (Render / Procfile)
Mantém compatibilidade com:
- `gunicorn app:app`

## Estrutura
- `app/` contém toda a lógica Python (config, serviços, rotas, jobs).
- `templates/` e `static/` mantêm-se como estavam.
