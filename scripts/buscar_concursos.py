#!/usr/bin/env python3
"""Varre os municípios do raio de interesse e monta o rascunho do relatório diário.

Uso:
    python3 scripts/buscar_concursos.py                 # imprime o relatório em Markdown
    python3 scripts/buscar_concursos.py --json          # despeja o resultado bruto
    python3 scripts/buscar_concursos.py --salvar        # grava em relatorios/AAAA-MM-DD.md

O resultado é um *rascunho*: a varredura automática cobre apenas o que a PCI
Concursos indexa com inscrição aberta. Concursos autorizados/previstos e as
fontes listadas em `config/perfil.json > fontes_extras` continuam exigindo
verificação manual antes do envio.
"""

from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pci import PCIClient, PCIError  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent
CONFIG = RAIZ / "config" / "perfil.json"
RELATORIOS = RAIZ / "relatorios"
FUSO_BR = timezone(timedelta(hours=-3))


def normalizar(texto: str) -> str:
    decomposto = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in decomposto if unicodedata.category(c) != "Mn")


def classificar_cargo(cargo: str, regras: dict) -> str:
    """Devolve 'alvo', 'excluido' ou 'indefinido' para um cargo."""
    normalizado = normalizar(cargo)
    for termo in regras["cargos_excluidos"]:
        if normalizar(termo) in normalizado:
            return "excluido"
    for termo in regras["cargos_alvo"]:
        if normalizar(termo) in normalizado:
            return "alvo"
    return "indefinido"


def avaliar(concurso: dict, regras: dict) -> dict:
    """Anota um concurso com o grau de aderência ao perfil.

    A PCI informa a escolaridade só no nível do certame, nunca por cargo, então
    nenhum veredito aqui é definitivo: um "Fiscal de Obras" pode ser de nível médio.
    A triagem serve para ordenar a leitura, não para dispensar o edital.
    """
    cargos = concurso.get("cargos") or [concurso.get("cargos_resumo", "")]
    alvos = [c for c in cargos if classificar_cargo(c, regras) == "alvo"]
    indefinidos = [c for c in cargos if classificar_cargo(c, regras) == "indefinido"]
    tem_superior = "superior" in normalizar(concurso.get("formacao", ""))

    if alvos and tem_superior:
        aderencia = "alta"
    elif tem_superior and (alvos or indefinidos):
        aderencia = "media"
    else:
        aderencia = "baixa"

    return {**concurso, "_aderencia": aderencia, "_cargos_alvo": alvos}


def varrer(config: dict) -> list[dict]:
    cliente = PCIClient()
    regras = config["elegibilidade"]
    achados: list[dict] = []
    vistos: set = set()

    for municipio in config["municipios"]:
        cidade, uf, km = municipio["cidade"], municipio["uf"], municipio["km"]
        try:
            resposta = cliente.por_cidade(uf, cidade)
        except PCIError as exc:
            print(f"[aviso] {cidade}/{uf}: {exc}", file=sys.stderr)
            continue

        for concurso in resposta.get("data", []):
            chave = concurso.get("id")
            if chave in vistos:
                continue
            vistos.add(chave)
            achados.append({**avaliar(concurso, regras), "_cidade": cidade, "_uf": uf, "_km": km})

    ordem = {"alta": 0, "media": 1, "baixa": 2}
    achados.sort(key=lambda c: (ordem[c["_aderencia"]], c["_km"]))
    return achados


def formatar(achados: list[dict], data_br: str) -> str:
    relevantes = [c for c in achados if c["_aderencia"] in ("alta", "media")]
    if relevantes:
        plural = "encontrado" if len(relevantes) == 1 else "encontrados"
        titulo = f"# Concursos — {data_br} — {len(relevantes)} {plural}"
    else:
        titulo = f"# Concursos — {data_br} — sem novidades"

    linhas = [titulo, ""]
    if not achados:
        linhas.append("Nenhum concurso com inscrição aberta nos municípios do raio.")
    for c in achados:
        datas = c.get("datas") or {}
        marcador = {
            "alta": " (aderência alta — confirmar escolaridade do cargo no edital)",
            "media": " (aderência a confirmar)",
        }.get(c["_aderencia"], " (provavelmente fora do perfil)")
        linhas += [
            f"## {c.get('titulo', 'Sem título')}{marcador}",
            "",
            f"- Órgão: {c.get('titulo', '—')}",
            f"- Cargo(s): {', '.join(c.get('cargos') or [c.get('cargos_resumo', '—')])}",
            f"- Local: {c['_cidade']}/{c['_uf']} (~{c['_km']} km de Ilha Solteira)",
            "- Status: inscrição aberta",
            f"- Prazo de inscrição: {datas.get('fim', '—')}"
            f" ({datas.get('dias_restantes', '?')} dias restantes)",
            f"- Escolaridade: {c.get('formacao', '—')}",
            f"- Vagas/salário: {c.get('vagas_salario', '—')}",
            f"- Link: {(c.get('noticia') or {}).get('link', '—')}",
            "",
        ]

    linhas += [
        "---",
        "",
        "Rascunho automático (fonte: conector MCP da PCI Concursos, só cobre inscrições",
        "abertas). Antes de enviar, verificar manualmente as fontes de",
        "`config/perfil.json > fontes_extras` e os concursos autorizados/previstos.",
    ]
    return "\n".join(linhas)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="imprime o resultado bruto")
    parser.add_argument("--salvar", action="store_true", help="grava em relatorios/")
    args = parser.parse_args()

    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    achados = varrer(config)

    if args.json:
        print(json.dumps(achados, ensure_ascii=False, indent=2))
        return 0

    hoje = datetime.now(FUSO_BR)
    texto = formatar(achados, hoje.strftime("%d/%m/%Y"))
    print(texto)

    if args.salvar:
        RELATORIOS.mkdir(exist_ok=True)
        destino = RELATORIOS / f"{hoje:%Y-%m-%d}.md"
        destino.write_text(texto + "\n", encoding="utf-8")
        print(f"\n[ok] gravado em {destino.relative_to(RAIZ)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
