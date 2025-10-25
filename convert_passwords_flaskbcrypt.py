"""
Script para converter senhas em texto simples na tabela Utilizador
para hashes bcrypt usando Flask-Bcrypt (compatível com o teu app Flask).

⚠️ Faz backup da base de dados antes de executar!
"""

from flask_bcrypt import Bcrypt
import mysql.connector
import sys

# --- CONFIGURAÇÃO ---
DB_CONFIG = {
    "host": "localhost",
    "user": "root",
    "password": "",  # coloca aqui tua password do MySQL
    "database": "AgendaBelezaDB",
    "raise_on_warnings": True,
}

bcrypt = Bcrypt()

# Prefixos válidos de hash bcrypt
VALID_PREFIXES = ("$2a$", "$2b$", "$2y$")

def is_bcrypt_hash(password):
    """Verifica se a string parece um hash bcrypt."""
    if not password:
        return False
    return any(password.startswith(pref) for pref in VALID_PREFIXES)

def main():
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cur = conn.cursor()
        cur.execute("SELECT Id, Username, Password FROM Utilizador")
        rows = cur.fetchall()
    except Exception as e:
        print(f"❌ Erro ao conectar ou ler tabela: {e}")
        sys.exit(1)

    total = len(rows)
    print(f"🔍 Utilizadores encontrados: {total}")

    atualizadas = 0
    ignoradas = 0
    erros = 0

    for user_id, username, pw in rows:
        try:
            if pw is None or pw.strip() == "":
                print(f"⚠️ [{username}] senha vazia — ignorado.")
                ignoradas += 1
                continue

            if is_bcrypt_hash(pw):
                ignoradas += 1
                continue  # já está em formato bcrypt

            # 🔒 Cria hash com Flask-Bcrypt
            hashed_pw = bcrypt.generate_password_hash(pw).decode("utf-8")

            cur.execute(
                "UPDATE Utilizador SET Password=%s WHERE Id=%s",
                (hashed_pw, user_id)
            )
            atualizadas += 1

            # commit a cada 50
            if atualizadas % 50 == 0:
                conn.commit()
                print(f"💾 {atualizadas} senhas actualizadas até agora...")

        except Exception as e:
            print(f"❌ Erro ao processar {username}: {e}")
            erros += 1

    conn.commit()
    cur.close()
    conn.close()

    print("\n✨ Conversão concluída!")
    print(f"🔁 Senhas actualizadas: {atualizadas}")
    print(f"⏭️  Ignoradas (já com hash): {ignoradas}")
    print(f"⚠️ Erros: {erros}")

if __name__ == "__main__":
    main()
