#!/usr/bin/env python3
"""Busca em diários oficiais.

Cobre duas fontes com API utilizável sem autenticação:

- **DOU** (Imprensa Nacional). A página de busca renderiza os resultados no
  cliente, mas embute o JSON num `<script id="..._BuscaDouPortlet_params">`.
  É esse JSON que lemos, o que evita depender do HTML renderizado.
- **Diários municipais**, via API do Querido Diário (Open Knowledge Brasil),
  que indexa e faz busca full-text em diários de prefeituras.

Os diários estaduais de SP e MS ficam de fora: o `do-api-web-search.doe.sp.gov.br`
devolve lista vazia para qualquer consulta anônima e o `spdo.ms.gov.br` é um app
Next.js sem endpoint público estável. Para esses dois, `urls_manuais()` devolve as
URLs de busca já montadas, para conferência manual ou via navegador.

Uso:
    python3 scripts/diarios.py                 # últimos 7 dias, todas as fontes
    python3 scripts/diarios.py --dias 30
    python3 scripts/diarios.py --fonte dou
    python3 scripts/diarios.py --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
CONFIG = RAIZ / "config" / "perfil.json"

DOU_BUSCA = "https://www.in.gov.br/consulta/-/buscar/dou"
DOU_PAYLOAD = re.compile(
    r'id="_br_com_seatecnologia_in_buscadou_BuscaDouPortlet_params"[^>]*>(.*?)</script>',
    re.S,
)
QD_APIS = (
    "https://api.queridodiario.ok.org.br/api",
    "https://queridodiario.ok.org.br/api",
)
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36"
TIMEOUT = 60

# Atos que criam oportunidade nova — é o que interessa no relatório.
TERMOS_ABERTURA = (
    "edital de abertura",
    "abertura de inscricoes",
    "abertura das inscricoes",
    "torna publica a abertura",
    "torna publico a abertura",
    "autoriza a realizacao de concurso",
    "autoriza a abertura",
    "prorroga o prazo de inscricoes",
    "prorrogacao das inscricoes",
    "reabertura de inscricoes",
    "fica autorizada a realizacao de concurso",
)

# Títulos que, combinados com menção a concurso, indicam abertura mesmo quando o
# trecho recortado pela busca não contém a frase completa. Mantidos deliberadamente
# específicos: um "Edital nº 25" genérico tanto abre quanto homologa certame.
TITULOS_ABERTURA = (
    "edital de concurso publico",
    "edital de processo seletivo",
    "aviso de concurso",
    "aviso de processo seletivo",
)

# Atos de andamento de certame já existente: úteis como contexto, não como novidade.
TERMOS_ANDAMENTO = (
    "concurso publico",
    "processo seletivo",
    "provimento de cargo",
    "provimento de vagas",
)

# Ruído puro: mesmo citando concurso, não abrem nada.
TERMOS_RUIDO = (
    "convoca",
    "convocacao",
    "homologacao",
    "homologa ",
    "resultado final",
    "resultado preliminar",
    "classificacao final",
    "nomeia",
    "nomeacao",
    "posse",
    "extrato de contrato",
    "extrato de aditamento",
    "aditamento contratual",
    "pregao",
    "licitacao",
    "exoneracao",
    "exonera ",
)

TAGS_HTML = re.compile(r"<[^>]+>")

# Quantos atos de andamento mostrar por fonte antes de resumir o resto.
TETO_ANDAMENTO = 5


def normalizar(texto: str) -> str:
    decomposto = unicodedata.normalize("NFD", (texto or "").lower())
    return "".join(c for c in decomposto if unicodedata.category(c) != "Mn")


def limpar(texto: str) -> str:
    """Remove as tags de destaque que o DOU injeta no trecho e normaliza espaços."""
    return re.sub(r"\s+", " ", TAGS_HTML.sub("", texto or "")).strip()


# O in.gov.br devolve 403 intermitente quando a requisição tem poucos
# cabeçalhos (visto em 30/08/2026 sob rajada de consultas; na repetição os dois
# conjuntos passaram). Mandar o conjunto completo reduz a chance do 403 sem
# mudar o resultado quando a fonte está saudável.
CABECALHOS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    "Referer": "https://www.in.gov.br/consulta/-/buscar/dou",
    "Upgrade-Insecure-Requests": "1",
}


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers=CABECALHOS)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def _qd_gazettes(params: str, cidade: str) -> dict | None:
    """Consulta o Querido Diário tentando os hosts conhecidos, na ordem.

    Em 30/08/2026 o host histórico (`api.queridodiario...`) passou a responder
    404 em todas as rotas e o domínio do site devolve o HTML da SPA. Percorrer a
    lista deixa a função voltar a funcionar sozinha quando a API reabrir, em vez
    de exigir uma nova edição do script.
    """
    ultimo_erro: Exception | None = None
    for base in QD_APIS:
        try:
            return json.loads(_get(f"{base}/gazettes?{params}"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            ultimo_erro = exc
    print(
        f"[aviso] Querido Diário {cidade}: nenhum host respondeu JSON "
        f"({ultimo_erro}) — diários municipais NÃO conferidos",
        file=sys.stderr,
    )
    return None


def classificar_ato(titulo: str, *trechos: str) -> str | None:
    """Diz se o ato abre uma oportunidade, é andamento de certame ou é ruído.

    O título pesa mais que o corpo. Tanto o DOU quanto o Querido Diário devolvem
    apenas um recorte do texto ao redor do termo buscado, então a frase
    "edital de abertura" pode estar no documento sem aparecer no trecho. O título,
    quando existe, costuma dizer a natureza do ato — daí a checagem separada.

    A ordem importa: um edital de abertura continua valendo mesmo que o texto
    também contenha "homologação" em outro parágrafo.
    """
    cabecalho = normalizar(titulo)
    corpo = normalizar(" ".join(t for t in trechos if t))
    tudo = f"{cabecalho} {corpo}"

    if any(t in tudo for t in TERMOS_ABERTURA):
        return "abertura"
    # Ruído vem antes do título porque "EDITAL DE CONVOCAÇÃO ... (Edital nº 01/2023)"
    # é convocação, não abertura, por mais que cite um edital.
    if any(t in tudo for t in TERMOS_RUIDO):
        return None
    if any(t in cabecalho for t in TITULOS_ABERTURA):
        return "abertura"
    if any(t in tudo for t in TERMOS_ANDAMENTO):
        return "andamento"
    return None


def buscar_dou(termos: list[str], dias: int = 7) -> list[dict]:
    """Procura os termos no Diário Oficial da União nos últimos `dias`."""
    ate = date.today()
    desde = ate - timedelta(days=dias)
    achados: list[dict] = []
    vistos: set[str] = set()

    for termo in termos:
        params = urllib.parse.urlencode(
            {
                "q": f'"{termo}"',
                "s": "todos",
                "exactDate": "personalizado",
                "publishFrom": desde.strftime("%d-%m-%Y"),
                "publishTo": ate.strftime("%d-%m-%Y"),
                "sortType": "0",
                "delta": "20",
                "currentPage": "1",
            }
        )
        try:
            html = _get(f"{DOU_BUSCA}?{params}")
        except (urllib.error.URLError, TimeoutError) as exc:
            print(f"[aviso] DOU '{termo}': {exc}", file=sys.stderr)
            continue

        bloco = DOU_PAYLOAD.search(html)
        if not bloco:
            print(f"[aviso] DOU '{termo}': payload não encontrado", file=sys.stderr)
            continue

        for item in json.loads(bloco.group(1)).get("jsonArray", []):
            titulo = limpar(item.get("title", ""))
            conteudo = limpar(item.get("content", ""))
            tipo = classificar_ato(titulo, conteudo)
            if not tipo:
                continue
            url_titulo = item.get("urlTitle", "")
            if url_titulo in vistos:
                continue
            vistos.add(url_titulo)
            achados.append(
                {
                    "fonte": "DOU",
                    "tipo": tipo,
                    "termo": termo,
                    "data": item.get("pubDate"),
                    "secao": item.get("pubName"),
                    "titulo": titulo,
                    "orgao": item.get("hierarchyStr"),
                    "trecho": conteudo[:300],
                    "link": f"https://www.in.gov.br/web/dou/-/{url_titulo}",
                }
            )
    return achados


def buscar_diarios_municipais(territorios: list[dict], dias: int = 7) -> list[dict]:
    """Procura atos de concurso nos diários municipais indexados pelo Querido Diário."""
    ate = date.today()
    desde = ate - timedelta(days=dias)
    achados: list[dict] = []

    for t in territorios:
        params = urllib.parse.urlencode(
            {
                "territory_ids": t["territory_id"],
                "querystring": '"concurso público"',
                "published_since": desde.isoformat(),
                "published_until": ate.isoformat(),
                "size": 10,
                "sort_by": "descending_date",
            }
        )
        dados = _qd_gazettes(params, t["cidade"])
        if dados is None:
            continue

        for g in dados.get("gazettes", []):
            trechos = [limpar(e) for e in (g.get("excerpts") or [])]
            titulo = f"Diário de {g.get('territory_name')} de {g.get('date')}"
            tipo = classificar_ato(titulo, *trechos)
            if not tipo:
                continue
            achados.append(
                {
                    "fonte": f"DO municipal — {t['cidade']}/{t['uf']}",
                    "tipo": tipo,
                    "data": g.get("date"),
                    "titulo": titulo,
                    "trecho": " ".join(trechos)[:300],
                    "link": g.get("url"),
                }
            )
    return achados


def urls_manuais(dias: int = 7) -> list[dict]:
    """Diários sem API utilizável — devolve a URL de busca pronta."""
    ate = date.today()
    desde = ate - timedelta(days=dias)
    return [
        {
            "fonte": "DOE-SP (Diário Oficial do Estado de São Paulo)",
            "motivo": "a API pública devolve lista vazia para consultas anônimas",
            "link": "https://doe.sp.gov.br/busca-avancada",
        },
        {
            "fonte": "DOE-MS (Diário Oficial do Estado de Mato Grosso do Sul)",
            "motivo": "app Next.js sem endpoint de busca público estável",
            "link": "https://www.spdo.ms.gov.br/diariodoe/",
        },
        {
            "fonte": "Diário Oficial dos Municípios de MS (Assomasul)",
            "motivo": "busca exige sessão autenticada",
            "link": "https://diariooficialms.com.br/",
        },
        {
            "fonte": f"período sugerido para as três buscas acima",
            "motivo": f"{desde.strftime('%d/%m/%Y')} a {ate.strftime('%d/%m/%Y')}",
            "link": "",
        },
    ]


def varrer(config: dict, dias: int, fontes: set[str]) -> dict:
    termos_dou = [m["cidade"] for m in config["municipios"] if m.get("dou", True)]
    territorios = [
        m for m in config["municipios"] if m.get("territory_id") and m.get("qd_nivel", 0) > 0
    ]

    resultado: dict = {"dias": dias, "dou": [], "municipais": [], "manuais": []}
    if "dou" in fontes:
        resultado["dou"] = buscar_dou(termos_dou, dias)
    if "municipais" in fontes:
        resultado["municipais"] = buscar_diarios_municipais(territorios, dias)
    if "manuais" in fontes:
        resultado["manuais"] = urls_manuais(dias)
    return resultado


def formatar(res: dict, mostrar_andamento: bool = False) -> str:
    linhas = [f"# Diários oficiais — últimos {res['dias']} dias", ""]

    for chave, rotulo in (("dou", "Diário Oficial da União"), ("municipais", "Diários municipais")):
        itens = res.get(chave) or []
        aberturas = [i for i in itens if i.get("tipo") == "abertura"]
        andamento = [i for i in itens if i.get("tipo") == "andamento"]

        linhas += [f"## {rotulo}", ""]
        linhas.append(
            f"{len(aberturas)} ato(s) de abertura e {len(andamento)} de andamento de certame."
        )
        linhas.append("")

        limite = None if mostrar_andamento else TETO_ANDAMENTO
        cortados = andamento if limite is None else andamento[:limite]
        grupos = [
            ("Aberturas", aberturas, 0),
            ("Andamento de certames já existentes", cortados, len(andamento) - len(cortados)),
        ]

        for nome, grupo, ocultos in grupos:
            linhas += [f"### {nome}", ""]
            if not grupo:
                linhas += ["Nada no período.", ""]
                continue
            for i in grupo:
                linhas += [
                    f"- **{i.get('data')} — {i.get('titulo')}**",
                    f"  - Fonte: {i.get('fonte')}"
                    + (f" · {i['orgao']}" if i.get("orgao") else "")
                    + (f" · seção {i['secao']}" if i.get("secao") else ""),
                    f"  - {i.get('trecho')}",
                    f"  - {i.get('link')}",
                ]
            if ocultos:
                linhas.append(f"- _(+{ocultos} omitidos; use `--tudo` para ver todos)_")
            linhas.append("")

    manuais = res.get("manuais") or []
    if manuais:
        linhas += ["## Conferir manualmente", ""]
        for m in manuais:
            alvo = f" — {m['link']}" if m["link"] else ""
            linhas.append(f"- {m['fonte']}: {m['motivo']}{alvo}")
        linhas.append("")

    return "\n".join(linhas)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dias", type=int, default=7, help="janela de busca (padrão 7)")
    parser.add_argument(
        "--fonte",
        choices=["dou", "municipais", "manuais", "todas"],
        default="todas",
    )
    parser.add_argument("--json", action="store_true", help="imprime o resultado bruto")
    parser.add_argument(
        "--tudo",
        action="store_true",
        help="inclui atos de andamento (convocação, homologação) além das aberturas",
    )
    args = parser.parse_args()

    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    fontes = {"dou", "municipais", "manuais"} if args.fonte == "todas" else {args.fonte}
    res = varrer(config, args.dias, fontes)

    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(formatar(res, mostrar_andamento=args.tudo))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
