# -*- coding: utf-8 -*-
import os

# Quando corres localmente com "python app.py" usa a app já criada no package.
from app import app

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", 5000)),
        debug=False,
    )
