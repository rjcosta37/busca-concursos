#!/usr/bin/env python3
"""Consulta portais e bancas que a PCI Concursos não cobre bem.

A varredura da PCI (`buscar_concursos.py`) só enxerga certames com inscrição
aberta e já indexados. Este módulo cobre o que falta:

- **Portal de Concursos do Estado de SP** — a fonte oficial. Tem uma seção só de
  concursos **autorizados**, que é justamente o que nenhuma outra fonte automatizada
  entrega, já que o DOE-SP não tem API pública utilizável.
- **Cebraspe** — a página é uma SPA, mas o backend (`apis.cebraspe.org.br`) responde
  JSON sem autenticação, agrupado por fase do certame.
- **Folha Dirigida (Qconcursos)** — HTML renderizado no servidor, com editorias de
  concursos abertos, previstos e por estado. Bom para autorizados/previstos, que
  costumam sair primeiro em portal de notícia.
- **VUNESP** — a banca de boa parte dos certames paulistas. O site responde 403 a
  qualquer requisição fora do navegador, então aqui só devolvemos a URL para
  conferência via busca web.
- **Bancas regionais** — quem publica os editais de dentro do raio não são as bancas
  grandes, e sim institutos pequenos: o Instituto DOM (Andradina), o IBAM (Ilha
  Solteira), o Instituto Avalia (Três Lagoas), a CONSESP (Santa Fé do Sul) e a
  Valespe (Castilho). Vigiar a banca pega o edital no dia da publicação, antes de
  qualquer portal indexar. Só o Instituto DOM entrega HTML raspável; os outros
  respondem 403 ou montam a lista por JavaScript e ficam na conferência manual.

Uso:
    python3 scripts/fontes_extras.py            # relatório em Markdown
    python3 scripts/fontes_extras.py --json     # resultado bruto
    python3 scripts/fontes_extras.py --fonte sp
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

SP_PORTAL = "http://www.concursopublico.sp.gov.br/PortalConcurso/noauth/PortalDeConcursos.do"
SP_SECOES = {
    "inscrições abertas": "concursoInscrAberta",
    "autorizados": "concursoAutorizado",
    "próximos": "proximoConcurso",
}
CEBRASPE_API = "https://apis.cebraspe.org.br/cebraspe/eventos/tipo/concursos"
CEBRASPE_FASES = ("novos", "inscricoes-abertas")
FOLHA = "https://folha.qconcursos.com/e"
FOLHA_EDITORIAS = (
    "concursos-abertos",
    "concursos-previstos",
    "concursos-sao-paulo-sp",
    "concursos-mato-grosso-do-sul-ms",
)
VUNESP_BUSCA = "https://www.vunesp.com.br/busca/concurso/inscricoes%20abertas"

INSTITUTO_DOM = "https://www.institutodom.com/"
# Bancas que atendem os municípios do raio mas não podem ser raspadas: 403 no caso do
# IBAM e da CONSESP, lista montada por JavaScript no caso do Avalia e da Valespe.
BANCAS_MANUAIS = {
    "IBAM (Ilha Solteira)": "https://www.ibamsp-concursos.org.br/",
    "Instituto Avalia (Três Lagoas)": "https://www.avalia.org.br/concursos/inscricoes-abertas",
    "CONSESP (Santa Fé do Sul)": "https://www.consesp.com.br/",
    "Valespe (Castilho)": "https://www.valespe.com.br/",
    "FCC (certames estaduais de SP)": "https://www.concursosfcc.com.br/",
}

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36"
TIMEOUT = 60


def baixar(url: str, decodificar: str = "utf-8") -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read().decode(decodificar, errors="replace")


def baixar_adaptativo(url: str) -> str:
    """Baixa decidindo o encoding pelo conteúdo, não pelo cabeçalho.

    O Instituto DOM responde ora em UTF-8, ora em ISO-8859-1, para a mesma URL — e o
    cabeçalho `Content-Type` diz ISO-8859-1 nos dois casos. Tentamos UTF-8 estrito e
    caímos para latin-1 quando ele falha, que é o único par de encodings em jogo aqui.
    """
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        bruto = resp.read()
    try:
        return bruto.decode("utf-8")
    except UnicodeDecodeError:
        return bruto.decode("iso-8859-1")


def sem_tags(fragmento: str) -> str:
    texto = html.unescape(re.sub(r"<[^>]+>", " ", fragmento))
    return re.sub(r"\s+", " ", texto).strip()


DATA_JAVA = re.compile(r"^\w{3} \w{3} \d{2} \d{2}:\d{2}:\d{2} \w+ \d{4}$")


def limpar_celulas(celulas: list[str]) -> list[str]:
    """Descarta as colunas que o portal usa como índice, não como conteúdo.

    Cada linha carrega um `Date.toString()` do Java, um blob com todos os campos
    concatenados (usado pela busca do próprio portal) e o salário repetido em
    centavos logo depois da versão formatada.
    """
    limpas = []
    for celula in celulas:
        if not celula or DATA_JAVA.match(celula) or len(celula) > 120:
            continue
        anterior = limpas[-1] if limpas else ""
        if celula.isdigit() and anterior.startswith("R$"):
            continue
        limpas.append(celula)
    return limpas


def linhas_da_tabela(pagina: str) -> list[list[str]]:
    """Extrai as células da maior tabela da página, descartando cabeçalhos."""
    tabelas = re.findall(r"<table.*?</table>", pagina, flags=re.S | re.I)
    if not tabelas:
        return []

    registros = []
    for tr in re.findall(r"<tr.*?</tr>", max(tabelas, key=len), flags=re.S | re.I):
        celulas = [
            sem_tags(c) for c in re.findall(r"<t[dh].*?</t[dh]>", tr, flags=re.S | re.I)
        ]
        # O portal repete o cabeçalho dentro do corpo da tabela.
        if celulas and celulas[0] and "Órgão" not in celulas[0]:
            limpas = limpar_celulas(celulas)
            if limpas:
                registros.append(limpas)
    return registros


def portal_sp() -> dict[str, list[list[str]]]:
    resultado = {}
    for rotulo, acao in SP_SECOES.items():
        try:
            pagina = baixar(f"{SP_PORTAL}?acao={acao}", decodificar="iso-8859-1")
        except (urllib.error.URLError, TimeoutError) as exc:
            print(f"[aviso] portal SP / {rotulo}: {exc}", file=sys.stderr)
            resultado[rotulo] = []
            continue
        resultado[rotulo] = linhas_da_tabela(pagina)
    return resultado


def cebraspe() -> dict[str, list[dict]]:
    resultado = {}
    for fase in CEBRASPE_FASES:
        try:
            dados = json.loads(baixar(f"{CEBRASPE_API}/fase/{fase}"))
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            print(f"[aviso] cebraspe / {fase}: {exc}", file=sys.stderr)
            resultado[fase] = []
            continue

        eventos = []
        for grupo in dados if isinstance(dados, list) else []:
            for evento in grupo.get("eventos", []):
                eventos.append(
                    {
                        "nome": evento.get("eventoNomeAbreviado"),
                        "vagas": evento.get("eventoTotalVagas"),
                        "salario": (evento.get("strEventoSalarioMaximo") or "").strip(),
                        "link": f"https://www.cebraspe.org.br/concursos/{evento.get('eventoURL')}",
                    }
                )
        resultado[fase] = eventos
    return resultado


def folha_dirigida() -> dict[str, list[dict]]:
    resultado = {}
    for editoria in FOLHA_EDITORIAS:
        try:
            pagina = baixar(f"{FOLHA}/{editoria}")
        except (urllib.error.URLError, TimeoutError) as exc:
            print(f"[aviso] folha dirigida / {editoria}: {exc}", file=sys.stderr)
            resultado[editoria] = []
            continue

        materias, vistos = [], set()
        for m in re.finditer(r'<a[^>]+href="(/n/[^"#?]+)"[^>]*>(.*?)</a>', pagina, flags=re.S):
            caminho, titulo = m.group(1), sem_tags(m.group(2))
            # Cartões curtos são navegação ("leia mais", tags); os de notícia trazem
            # título e chamada concatenados.
            if len(titulo) < 40 or caminho in vistos:
                continue
            vistos.add(caminho)
            materias.append({"titulo": titulo, "link": f"https://folha.qconcursos.com{caminho}"})
        resultado[editoria] = materias
    return resultado


CARTAO_DOM = re.compile(
    r"(?P<tipo>Concurso Público|Processo Seletivo)\s*-\s*(?P<titulo>.*?)\s*"
    r"Edital n[º°]?\s*(?P<edital>[\w./-]+)\s+"
    r"Inscrições de\s*(?P<inicio>\d{2}/\d{2}/\d{4})\s*a\s*(?P<fim>\d{2}/\d{2}/\d{4})"
    r"(?P<aberto>\s*Inscrições Abertas!)?",
    flags=re.S,
)


def instituto_dom() -> list[dict]:
    """Lê a página inicial do Instituto DOM, que lista os certames em cartões.

    A página não tem tabela nem JSON: cada certame é um bloco de divs com o órgão, o
    número do edital, o período de inscrição e — só quando ainda dá para se inscrever —
    o selo "Inscrições Abertas!". Achatamos tudo em texto e lemos os cartões por
    expressão regular, que é frágil por natureza mas sobrevive a mudança de CSS.
    """
    try:
        pagina = baixar_adaptativo(INSTITUTO_DOM)
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"[aviso] instituto dom: {exc}", file=sys.stderr)
        return []

    texto = sem_tags(re.sub(r"<(script|style).*?</\1>", " ", pagina, flags=re.S | re.I))

    certames, vistos = [], set()
    for m in CARTAO_DOM.finditer(texto):
        edital = m.group("edital")
        titulo = re.sub(r"\s+", " ", m.group("titulo")).strip(" -")
        # O cartão repete o número do edital no início do título; a parte útil é o que
        # sobra depois dele (o órgão e, às vezes, um qualificador como "ACS" ou "Educação").
        titulo = re.sub(rf"^{re.escape(edital)}\s*-\s*", "", titulo).strip(" -")
        chave = (titulo, edital)
        if chave in vistos:
            continue
        vistos.add(chave)
        certames.append(
            {
                "tipo": m.group("tipo"),
                "titulo": titulo,
                "edital": edital,
                "inscricoes": f"{m.group('inicio')} a {m.group('fim')}",
                "aberto": bool(m.group("aberto")),
            }
        )
    return certames


def varrer(fontes: set[str]) -> dict:
    resultado = {}
    if "sp" in fontes:
        resultado["portal_sp"] = portal_sp()
    if "cebraspe" in fontes:
        resultado["cebraspe"] = cebraspe()
    if "folha" in fontes:
        resultado["folha"] = folha_dirigida()
    if "vunesp" in fontes:
        resultado["vunesp"] = VUNESP_BUSCA
    if "bancas" in fontes:
        resultado["instituto_dom"] = instituto_dom()
        resultado["bancas_manuais"] = BANCAS_MANUAIS
    return resultado


def formatar(resultado: dict) -> str:
    linhas = ["# Portais e bancas", ""]

    if "portal_sp" in resultado:
        linhas += ["## Portal de Concursos do Estado de São Paulo", ""]
        for rotulo, registros in resultado["portal_sp"].items():
            if not registros:
                linhas += [f"### {rotulo.capitalize()}", "", "Nada publicado.", ""]
                continue
            linhas += [f"### {rotulo.capitalize()} ({len(registros)})", ""]
            for celulas in registros:
                linhas.append("- " + " | ".join(celulas))
            linhas.append("")

    if "cebraspe" in resultado:
        linhas += ["## Cebraspe", ""]
        for fase, eventos in resultado["cebraspe"].items():
            if not eventos:
                linhas += [f"### {fase}", "", "Nada no momento.", ""]
                continue
            linhas += [f"### {fase} ({len(eventos)})", ""]
            for e in eventos:
                vagas = f"{e['vagas']} vagas" if e["vagas"] else "vagas a definir"
                linhas.append(f"- **{e['nome']}** — {vagas}, até {e['salario']}\n  {e['link']}")
            linhas.append("")

    if "folha" in resultado:
        linhas += ["## Folha Dirigida (Qconcursos)", ""]
        for editoria, materias in resultado["folha"].items():
            linhas += [f"### {editoria} ({len(materias)})", ""]
            for m in materias:
                linhas.append(f"- {m['titulo']}\n  {m['link']}")
            linhas.append("")

    if "vunesp" in resultado:
        linhas += [
            "## VUNESP",
            "",
            "O site responde 403 fora do navegador — **conferir via busca web**:",
            f"{resultado['vunesp']}",
            "",
        ]

    if "instituto_dom" in resultado:
        certames = resultado["instituto_dom"]
        abertos = [c for c in certames if c["aberto"]]
        linhas += [
            "## Instituto DOM (banca de Andradina)",
            "",
            f"{len(abertos)} com inscrição aberta, de {len(certames)} listados.",
            "",
        ]
        for c in certames:
            selo = "**ABERTO**" if c["aberto"] else "encerrado"
            linhas.append(
                f"- [{selo}] {c['tipo']} {c['edital']} — {c['titulo']} "
                f"(inscrições {c['inscricoes']})"
            )
        linhas.append("")

    if "bancas_manuais" in resultado:
        linhas += [
            "## Bancas regionais — conferir à mão",
            "",
            "Atendem municípios do raio, mas respondem 403 ou montam a lista por JavaScript:",
            "",
        ]
        for nome, url in resultado["bancas_manuais"].items():
            linhas.append(f"- {nome}: {url}")
        linhas.append("")

    return "\n".join(linhas)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="imprime o resultado bruto")
    parser.add_argument(
        "--fonte",
        action="append",
        choices=["sp", "cebraspe", "folha", "vunesp", "bancas"],
        help="limita a varredura (pode repetir)",
    )
    args = parser.parse_args()

    fontes = set(args.fonte) if args.fonte else {"sp", "cebraspe", "folha", "vunesp", "bancas"}
    resultado = varrer(fontes)

    if args.json:
        print(json.dumps(resultado, ensure_ascii=False, indent=2))
    else:
        print(formatar(resultado))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
