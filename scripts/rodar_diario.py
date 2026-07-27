#!/usr/bin/env python3
"""Executa a rotina diária de ponta a ponta: varre, grava o relatório e envia.

A varredura tem três partes que se complementam: a PCI Concursos (certames com
inscrição aberta, já consolidados), os diários oficiais (atos publicados antes de
qualquer portal noticiar) e os portais e bancas (o Portal de Concursos do Estado de
SP, o Cebraspe e a Folha Dirigida, que cobrem autorizados e previstos). O relatório
junta as três.

Uso:
    python3 scripts/rodar_diario.py              # varre, grava e tenta enviar
    python3 scripts/rodar_diario.py --dry-run    # varre, grava e só simula o envio
    python3 scripts/rodar_diario.py --diagnostico  # só checa se o canal de e-mail existe
    python3 scripts/rodar_diario.py --sem-diarios  # pula os diários (varredura rápida)
    python3 scripts/rodar_diario.py --sem-portais  # pula os portais e bancas

Códigos de saída:
    0  tudo certo (relatório gravado e e-mail enviado, ou dry-run)
    3  relatório gravado, mas o envio falhou por falta de configuração

O código 3 é intencionalmente diferente de 0: sinaliza que o relatório existe e
precisa ser entregue por outro caminho (commit no repo), sem mascarar a falha.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import diarios  # noqa: E402
import enviar_email  # noqa: E402
import fontes_extras  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent
SCRIPTS = RAIZ / "scripts"
RELATORIOS = RAIZ / "relatorios"
CONFIG = RAIZ / "config" / "perfil.json"
FUSO_BR = timezone(timedelta(hours=-3))
JANELA_DIARIOS = 7


def diagnosticar() -> tuple[bool, str]:
    try:
        cfg = enviar_email.resolver_credenciais()
    except enviar_email.ConfiguracaoAusente as exc:
        return False, str(exc)
    return True, f"canal pronto: {cfg['origem']} via {cfg['host']}:{cfg['porta']}"


def gerar_relatorio(com_diarios: bool = True, com_portais: bool = True) -> Path:
    hoje = datetime.now(FUSO_BR)
    destino = RELATORIOS / f"{hoje:%Y-%m-%d}.md"
    subprocess.run(
        [sys.executable, str(SCRIPTS / "buscar_concursos.py"), "--salvar"],
        check=True,
        stdout=subprocess.DEVNULL,
    )

    if com_diarios:
        config = json.loads(CONFIG.read_text(encoding="utf-8"))
        res = diarios.varrer(config, JANELA_DIARIOS, {"dou", "municipais", "manuais"})
        with destino.open("a", encoding="utf-8") as f:
            f.write("\n---\n\n" + diarios.formatar(res))

    if com_portais:
        res = fontes_extras.varrer({"sp", "cebraspe", "folha", "vunesp"})
        with destino.open("a", encoding="utf-8") as f:
            f.write("\n---\n\n" + fontes_extras.formatar(res))

    return destino


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="não envia, só simula")
    parser.add_argument(
        "--diagnostico",
        action="store_true",
        help="apenas verifica se há canal de e-mail configurado",
    )
    parser.add_argument(
        "--sem-diarios",
        action="store_true",
        help="pula a varredura dos diários oficiais",
    )
    parser.add_argument(
        "--sem-portais",
        action="store_true",
        help="pula os portais e bancas (portal SP, Cebraspe, Folha Dirigida)",
    )
    args = parser.parse_args()

    pronto, detalhe = diagnosticar()

    if args.diagnostico:
        print(("[ok] " if pronto else "[falta] ") + detalhe)
        return 0 if pronto else 3

    relatorio = gerar_relatorio(
        com_diarios=not args.sem_diarios,
        com_portais=not args.sem_portais,
    )
    print(f"[ok] relatório gravado em {relatorio.relative_to(RAIZ)}", flush=True)

    if not pronto and not args.dry_run:
        print(f"[falta] {detalhe}", file=sys.stderr)
        print(
            "[info] o relatório está gravado; entregue-o por commit no repositório "
            "até o canal de e-mail existir.",
            file=sys.stderr,
        )
        return 3

    argv = [sys.executable, str(SCRIPTS / "enviar_email.py"), str(relatorio)]
    if args.dry_run:
        argv.append("--dry-run")
    return subprocess.run(argv, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
