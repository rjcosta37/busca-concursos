#!/usr/bin/env python3
"""Varre o PNCP procurando, nos municípios do raio, contratações cujo objeto seja a
realização de concurso público.

O sinal aparece meses antes do edital: a prefeitura precisa contratar a banca, e a
contratação é publicada obrigatoriamente no PNCP (que, ao contrário dos sites das
prefeituras, responde a requisições simples). Ver `trilho-pncp-municipios.md` na
memória da automação para a receita completa de leitura do Termo de Referência.

Uso:
    python3 scripts/pncp_municipios.py [--ano 2026] [--desde AAAAMMDD]
"""

from __future__ import annotations

import argparse
import json
import pathlib
import random
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

BASE = "https://pncp.gov.br/api/consulta/v1/contratacoes/publicacao"

# Códigos de modalidade do PNCP. "Concurso" (3) é modalidade licitatória para escolha
# de trabalho técnico/artístico, não concurso de pessoal — mas é varrida assim mesmo
# porque a contratação da banca já apareceu classificada de forma inesperada.
MODALIDADES = {
    1: "Leilão eletrônico",
    2: "Diálogo competitivo",
    3: "Concurso",
    4: "Concorrência eletrônica",
    5: "Concorrência presencial",
    6: "Pregão eletrônico",
    7: "Pregão presencial",
    8: "Dispensa de licitação",
    9: "Inexigibilidade",
    12: "Credenciamento",
    13: "Leilão presencial",
}

TERMOS = (
    "concurso publico",
    "concurso público",
    "processo seletivo",
    "prova objetiva",
    "banca examinadora",
    "organizacao e realizacao de concurso",
)

RAIZ = pathlib.Path(__file__).resolve().parent.parent


def normalizar(texto: str) -> str:
    return re.sub(r"\s+", " ", (texto or "")).strip()


def buscar(ibge: str, uf: str, modalidade: int, inicial: str, final: str) -> list[dict]:
    """O PNCP devolve 429 sob rajada; sem o backoff a varredura vira falso negativo."""
    url = (
        f"{BASE}?dataInicial={inicial}&dataFinal={final}"
        f"&codigoModalidadeContratacao={modalidade}&uf={uf}"
        f"&codigoMunicipioIbge={ibge}&pagina=1&tamanhoPagina=50"
    )
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    for tentativa in range(6):
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                if resp.status == 204:
                    return []
                return json.loads(resp.read().decode("utf-8")).get("data") or []
        except urllib.error.HTTPError as exc:
            if exc.code in (204, 404, 422):
                return []
            if exc.code not in (429, 500, 502, 503):
                print(f"[aviso] {ibge}/mod{modalidade}: HTTP {exc.code}", file=sys.stderr)
                return []
        except Exception:  # noqa: BLE001 - rede instável, tentar de novo
            pass
        time.sleep((2**tentativa) * 0.7 + random.random())
    print(f"[FALHA] {ibge}/mod{modalidade}: esgotou as tentativas", file=sys.stderr)
    return []


def interessa(registro: dict) -> bool:
    alvo = " ".join(
        str(registro.get(campo) or "")
        for campo in ("objetoCompra", "informacaoComplementar")
    ).lower()
    return any(termo in alvo for termo in TERMOS)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--desde", default="20260101", help="data inicial AAAAMMDD")
    parser.add_argument("--ate", default=None, help="data final AAAAMMDD")
    parser.add_argument("--municipio", default=None, help="filtra por nome de município")
    args = parser.parse_args()

    if args.ate is None:
        import datetime
        import zoneinfo

        hoje = datetime.datetime.now(zoneinfo.ZoneInfo("America/Sao_Paulo")).date()
        args.ate = hoje.strftime("%Y%m%d")

    perfil = json.loads((RAIZ / "config" / "perfil.json").read_text(encoding="utf-8"))
    municipios = perfil["municipios"]
    if args.municipio:
        alvo = args.municipio.lower()
        municipios = [m for m in municipios if alvo in m["cidade"].lower()]

    tarefas = [
        (m, mod)
        for m in municipios
        if m.get("territory_id")
        for mod in MODALIDADES
    ]
    print(
        f"# PNCP — {len(municipios)} municípios × {len(MODALIDADES)} modalidades "
        f"({args.desde} a {args.ate})\n",
    )

    achados: list[tuple[dict, int, dict]] = []

    def tarefa(par):
        municipio, modalidade = par
        registros = buscar(
            municipio["territory_id"], municipio["uf"], modalidade, args.desde, args.ate
        )
        return [(municipio, modalidade, r) for r in registros if interessa(r)]

    with ThreadPoolExecutor(max_workers=4) as pool:
        for lote in pool.map(tarefa, tarefas):
            achados.extend(lote)

    if not achados:
        print("Nenhuma contratação com objeto de concurso público no período.")
        return 0

    vistos = set()
    for municipio, modalidade, registro in sorted(
        achados, key=lambda t: (t[0]["km"], str(registro_data(t[2])))
    ):
        chave = registro.get("numeroControlePNCP")
        if chave in vistos:
            continue
        vistos.add(chave)
        orgao = registro.get("orgaoEntidade") or {}
        print(f"## {municipio['cidade']}/{municipio['uf']} (~{municipio['km']} km)")
        print(f"- Órgão: {normalizar(orgao.get('razaoSocial'))} (CNPJ {orgao.get('cnpj')})")
        print(f"- Modalidade: {MODALIDADES[modalidade]}")
        print(f"- Objeto: {normalizar(registro.get('objetoCompra'))[:400]}")
        print(f"- Publicação: {registro_data(registro)}")
        print(f"- Controle PNCP: {chave}")
        print(f"- Link: {registro.get('linkSistemaOrigem') or '(não informado)'}")
        print()
    return 0


def registro_data(registro: dict) -> str:
    return (
        registro.get("dataPublicacaoPncp")
        or registro.get("dataInclusao")
        or registro.get("dataAberturaProposta")
        or ""
    )[:10]


if __name__ == "__main__":
    raise SystemExit(main())
