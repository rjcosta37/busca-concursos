#!/usr/bin/env python3
"""Envia um relatório por e-mail via SMTP.

Fallback para quando o conector Composio/Gmail não está disponível na automação.

O caminho mais curto é cadastrar só dois secrets (Cursor Dashboard >
Cloud Agents > Secrets), que já resolvem host e porta do Gmail:

    GMAIL_USER          seu endereço @gmail.com
    GMAIL_APP_PASSWORD  Senha de App de 16 caracteres (não a senha da conta)

Para outro provedor, use as variáveis genéricas:

    SMTP_HOST       ex.: smtp.gmail.com
    SMTP_PORT       587 (STARTTLS) ou 465 (SSL); padrão 587
    SMTP_USER       usuário de autenticação
    SMTP_PASSWORD   senha
    EMAIL_FROM      remetente (padrão: usuário SMTP)
    EMAIL_TO        destinatário (padrão: ricardojc011@gmail.com)

Uso:
    python3 scripts/enviar_email.py relatorios/2026-07-27.md
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
GMAIL_SMTP = ("smtp.gmail.com", 587)

DICA_AUTENTICACAO = """
Causas prováveis, na ordem em que vale checar:
  1. GMAIL_APP_PASSWORD não é uma Senha de App. A senha normal da conta não funciona
     no SMTP do Gmail. Gere uma em https://myaccount.google.com/apppasswords
     (exige verificação em duas etapas ativa na conta).
  2. A Senha de App foi revogada ou pertence a outra conta Google.
  3. GMAIL_USER não é o endereço dono da Senha de App.
Os espaços que o Google mostra na senha são ignorados automaticamente.
""".strip()


class ConfiguracaoAusente(RuntimeError):
    pass


def resolver_credenciais() -> dict:
    """Descobre a configuração SMTP a partir do ambiente.

    Prioriza o atalho do Gmail (GMAIL_USER + GMAIL_APP_PASSWORD) porque reduz o
    cadastro a dois secrets; cai para as variáveis SMTP_* genéricas em seguida.
    """
    gmail_user = (os.environ.get("GMAIL_USER") or "").strip()
    # O Google exibe a Senha de App em quatro blocos separados por espaço
    # ("abcd efgh ijkl mnop"), mas o SMTP só aceita os 16 caracteres corridos.
    gmail_senha = "".join((os.environ.get("GMAIL_APP_PASSWORD") or "").split())
    if gmail_user and gmail_senha:
        host, porta = GMAIL_SMTP
        return {
            "host": os.environ.get("SMTP_HOST", host),
            "porta": int(os.environ.get("SMTP_PORT", porta)),
            "usuario": gmail_user,
            "senha": gmail_senha,
            "origem": "GMAIL_USER + GMAIL_APP_PASSWORD",
        }

    faltando = [v for v in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD") if not os.environ.get(v)]
    if faltando:
        raise ConfiguracaoAusente(
            "nenhum canal de e-mail configurado. Cadastre GMAIL_USER e "
            "GMAIL_APP_PASSWORD (mais simples) ou as variáveis SMTP_* "
            "(faltando: " + ", ".join(faltando) + ") em "
            "Cursor Dashboard > Cloud Agents > Secrets."
        )

    return {
        "host": os.environ["SMTP_HOST"],
        "porta": int(os.environ.get("SMTP_PORT", "587")),
        "usuario": os.environ["SMTP_USER"],
        "senha": os.environ["SMTP_PASSWORD"],
        "origem": "SMTP_*",
    }


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

    try:
        cfg = resolver_credenciais()
    except ConfiguracaoAusente as exc:
        cfg = None
        if not args.dry_run:
            print(f"[erro] {exc}", file=sys.stderr)
            return 2
        problema = str(exc)

    remetente = os.environ.get("EMAIL_FROM") or (cfg["usuario"] if cfg else "")

    if args.dry_run:
        print(f"Para:     {destinatario}")
        print(f"De:       {remetente or '(não configurado)'}")
        print(f"Assunto:  {assunto}")
        print(f"Corpo:    {len(texto)} caracteres")
        if cfg:
            print(f"Canal:    {cfg['origem']} via {cfg['host']}:{cfg['porta']}")
        else:
            print(f"Canal:    indisponível — {problema}")
        return 0

    msg = montar(texto, assunto, remetente, destinatario)
    try:
        enviar(msg, cfg["host"], cfg["porta"], cfg["usuario"], cfg["senha"])
    except smtplib.SMTPAuthenticationError as exc:
        print(f"[erro] o servidor recusou as credenciais ({exc.smtp_code}).", file=sys.stderr)
        print(DICA_AUTENTICACAO, file=sys.stderr)
        return 4
    except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
        print(f"[erro] falha ao enviar: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 4

    print(f"[ok] e-mail enviado para {destinatario} via {cfg['host']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
