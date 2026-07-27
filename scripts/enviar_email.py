#!/usr/bin/env python3
"""Envia um relatório por e-mail via SMTP.

Fallback para quando o conector Composio/Gmail não está disponível na automação.
Configure estes secrets (Cursor Dashboard > Cloud Agents > Secrets):

    SMTP_HOST       ex.: smtp.gmail.com
    SMTP_PORT       ex.: 587 (STARTTLS) ou 465 (SSL)
    SMTP_USER       usuário/e-mail de autenticação
    SMTP_PASSWORD   senha de app do Gmail (não a senha da conta)
    EMAIL_FROM      remetente (padrão: SMTP_USER)
    EMAIL_TO        destinatário (padrão: ricardojc011@gmail.com)

Uso:
    python3 scripts/enviar_email.py relatorios/2026-07-27.md
    python3 scripts/enviar_email.py relatorios/2026-07-27.md --assunto "Concursos — 27/07/2026 — 2 encontrados"
    python3 scripts/enviar_email.py relatorios/2026-07-27.md --dry-run
"""

from __future__ import annotations

import argparse
import os
import smtplib
import ssl
import sys
from email.message import EmailMessage
from pathlib import Path

DESTINATARIO_PADRAO = "ricardojc011@gmail.com"
OBRIGATORIAS = ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD")


def assunto_do_relatorio(texto: str) -> str:
    """Usa o primeiro cabeçalho `# ...` do Markdown como assunto."""
    for linha in texto.splitlines():
        if linha.startswith("# "):
            return linha[2:].strip()
    return "Concursos — relatório diário"


def montar(texto: str, assunto: str, remetente: str, destinatario: str) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = assunto
    msg["From"] = remetente
    msg["To"] = destinatario
    msg.set_content(texto)
    return msg


def enviar(msg: EmailMessage, host: str, porta: int, usuario: str, senha: str) -> None:
    contexto = ssl.create_default_context()
    if porta == 465:
        with smtplib.SMTP_SSL(host, porta, context=contexto, timeout=60) as smtp:
            smtp.login(usuario, senha)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(host, porta, timeout=60) as smtp:
            smtp.starttls(context=contexto)
            smtp.login(usuario, senha)
            smtp.send_message(msg)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("relatorio", type=Path, help="arquivo Markdown a enviar")
    parser.add_argument("--assunto", help="sobrescreve o assunto derivado do relatório")
    parser.add_argument("--para", help="sobrescreve o destinatário")
    parser.add_argument("--dry-run", action="store_true", help="só mostra o que seria enviado")
    args = parser.parse_args()

    if not args.relatorio.is_file():
        print(f"[erro] relatório não encontrado: {args.relatorio}", file=sys.stderr)
        return 1

    texto = args.relatorio.read_text(encoding="utf-8")
    assunto = args.assunto or assunto_do_relatorio(texto)
    destinatario = args.para or os.environ.get("EMAIL_TO") or DESTINATARIO_PADRAO

    faltando = [v for v in OBRIGATORIAS if not os.environ.get(v)]
    if faltando and not args.dry_run:
        print(
            "[erro] variáveis de ambiente ausentes: " + ", ".join(faltando) + "\n"
            "Cadastre os secrets em Cursor Dashboard > Cloud Agents > Secrets.",
            file=sys.stderr,
        )
        return 2

    usuario = os.environ.get("SMTP_USER", "")
    remetente = os.environ.get("EMAIL_FROM") or usuario

    if args.dry_run:
        print(f"Para:     {destinatario}")
        print(f"De:       {remetente or '(SMTP_USER não definido)'}")
        print(f"Assunto:  {assunto}")
        print(f"Corpo:    {len(texto)} caracteres")
        if faltando:
            print("Faltando: " + ", ".join(faltando))
        return 0

    msg = montar(texto, assunto, remetente, destinatario)
    enviar(
        msg,
        os.environ["SMTP_HOST"],
        int(os.environ.get("SMTP_PORT", "587")),
        usuario,
        os.environ["SMTP_PASSWORD"],
    )
    print(f"[ok] e-mail enviado para {destinatario}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
