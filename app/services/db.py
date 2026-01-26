# -*- coding: utf-8 -*-
import os
import time
import socket
from pathlib import Path

import mysql.connector
from mysql.connector import Error

from flask import current_app

def get_db_connection():
    """
    Cria e devolve uma conexao MySQL com retry e suporte a SSL (CA opcional).
    Le host, user, pass, db, port e CA das variaveis de ambiente.
    """
    host = os.getenv("MYSQL_HOST")
    port = int(os.getenv("MYSQL_PORT", "3306"))
    user = os.getenv("MYSQL_USER")
    password = os.getenv("MYSQL_PASSWORD")
    database = os.getenv("MYSQL_DB")
    ca_path = os.getenv("MYSQL_SSL_CA")

    # Falhas comuns em ambiente local: .env nao carregado / variaveis em falta
    if not host or not user or not password or not database:
        raise Exception(
            "Variaveis MySQL em falta. Confirma MYSQL_HOST, MYSQL_USER, MYSQL_PASSWORD e MYSQL_DB (ou carrega o .env)."
        )

    # Fallback robusto para ca.pem no diretorio raiz do projeto
    root_dir = Path(__file__).resolve().parents[2]
    ca_fallback = root_dir / "ca.pem"

    ssl_config = {}
    if ca_path:
        if os.path.exists(ca_path):
            ssl_config = {"ssl_ca": ca_path}
        elif ca_fallback.exists():
            ssl_config = {"ssl_ca": str(ca_fallback)}
            current_app.logger.info(f"A usar certificado local: {ca_fallback}")
        else:
            current_app.logger.warning("CA nao encontrada - a ligar sem SSL.")
    else:
        # Se MYSQL_SSL_CA nao estiver definido mas existir ca.pem no projeto, usa-o.
        if ca_fallback.exists():
            ssl_config = {"ssl_ca": str(ca_fallback)}
            current_app.logger.info(f"A usar certificado local: {ca_fallback}")

    # Log do IP (debug)
    try:
        ip = socket.gethostbyname(host)
        current_app.logger.info(f"MySQL host {host} -> {ip}")
    except Exception as e:
        current_app.logger.error(f"Erro DNS: {e}")
        raise

    for i in range(3):
        try:
            conn = mysql.connector.connect(
                host=host,
                port=port,
                user=user,
                password=password,
                database=database,
                connection_timeout=10,
                **ssl_config,
            )
            if conn.is_connected():
                return conn
        except Error as err:
            current_app.logger.warning(f"Tentativa MySQL {i+1}/3 falhou: {err}")
            time.sleep(2)

    raise Exception("Nao foi possivel conectar ao MySQL.")
