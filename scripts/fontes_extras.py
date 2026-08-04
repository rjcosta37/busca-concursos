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
- **Centro Paula Souza (CEETEPS)** — tem Etec em Ilha Solteira, Andradina, Jales e
  Santa Fé do Sul, e os editais docentes saem por unidade, um por componente
  curricular. O componente de Sociologia aceita nominalmente "Ciências Sociais",
  então esta é a única fonte em que formação e cidade se encontram.
- **Bancas regionais** — quem publica os editais de dentro do raio não são as bancas
  grandes, e sim institutos pequenos: o Instituto DOM (Andradina), o IBAM (Ilha
  Solteira), o Instituto Avalia (Três Lagoas), a CONSESP (Santa Fé do Sul) e a
  Valespe (Castilho). Vigiar a banca pega o edital no dia da publicação, antes de
  qualquer portal indexar. O Instituto DOM e o Instituto Avalia entregam HTML
  raspável; os outros respondem 403 ou montam a lista por JavaScript e ficam na
  conferência manual.

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
import ssl
import sys
import unicodedata
import datetime
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
PERFIL = RAIZ / "config" / "perfil.json"

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

# O caminho é `/dgsdad/`. Com `/DeptRH/` as quatro URLs respondem 200 com uma página
# vazia, o que parece "nada aberto" em vez de erro.
CPS_BASE = "https://urhsistemas.cps.sp.gov.br/dgsdad/SelecaoPublica"
CPS_LISTAS = {
    "PSS docente de Etec": f"{CPS_BASE}/ETEC/PSS/Abertos.aspx",
    "Concurso público docente de Etec": f"{CPS_BASE}/ETEC/CPD/Abertos.aspx",
    "PSS de auxiliar de docente": f"{CPS_BASE}/PSSAD/Abertos.aspx",
    "PSS docente de Fatec": f"{CPS_BASE}/FATEC/PSS/inscricoesabertas.aspx",
}
CPS_VAZIO = "NÃO HÁ SELEÇÃO PÚBLICA COM INSCRIÇÕES ABERTAS"

# Repositório de documentos (AEM DAM) onde o SEBRAE-SP publica os comunicados oficiais
# de processo seletivo. O `.1.json` de qualquer pasta lista os filhos, o que torna a
# série de comunicados do ano enumerável sem depender da página "Trabalhe Conosco"
# (montada por JavaScript) nem dos portais de notícia.
SEBRAE_DAM = (
    "https://sebrae.com.br/content/dam/portal-sebrae/sp/midias/documentos/pdfs"
    "/trabalhe-conosco/efetivas"
)
# A listagem da banca não traz o requisito de formação, e é ele que decide a
# elegibilidade. Estes termos servem só para ordenar a leitura: o requisito real está no
# Anexo I do comunicado, que precisa ser aberto.
SEBRAE_TERMOS = (
    "marketing",
    "comunicacao",
    "publicidade",
    "institucional",
    "educacao",
    "cultura empreendedora",
    "atendimento",
    "negocios",
    "projetos",
    "ouvidoria",
    "psicossoc",
)

INSTITUTO_DOM = "https://www.institutodom.com/"
# `www.avalia.org.br` monta a lista por JavaScript, mas o `www2` serve o HTML pronto.
AVALIA = "https://www2.avalia.org.br/concursos.jsp"
# Bancas que atendem os municípios do raio mas não podem ser raspadas: 403 no caso do
# IBAM, da CONSESP e da RBO, lista montada por JavaScript no caso da Valespe.
BANCAS_MANUAIS = {
    "IBAM (Ilha Solteira)": "https://www.ibamsp-concursos.org.br/",
    "CONSESP (Santa Fé do Sul)": "https://www.consesp.com.br/",
    "Valespe (Castilho)": "https://www.valespe.com.br/",
    "FCC (certames estaduais de SP)": "https://www.concursosfcc.com.br/",
    # O SEBRAE-SP contrata pela RBO em fluxo quase semanal, um edital por vaga, e
    # distribui as vagas por Escritório Regional — o ALI Rural 2026 teve polos em
    # Andradina, Araçatuba e Penápolis. Cada edital fica aberto ~6 dias, então a
    # listagem precisa ser lida inteira, e não só as manchetes dos portais.
    "RBO / SEBRAE-SP (vagas em Escritórios Regionais, prazo de ~6 dias)": "https://rboconcursos.selecao.net.br/",
    "SEBRAE-SP Trabalhe Conosco": "https://sebrae.com.br/sites/PortalSebrae/ufs/sp/trabalhe_conosco",
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


def baixar_sem_verificar_tls(url: str) -> str:
    """Baixa ignorando a cadeia de certificados, equivalente ao `curl -k`.

    O `urhsistemas.cps.sp.gov.br` serve uma cadeia TLS incompleta e falha na validação
    padrão. É o mesmo servidor onde o Centro Paula Souza publica os editais, então não
    há alternativa a não ser dispensar a verificação.
    """
    contexto = ssl.create_default_context()
    contexto.check_hostname = False
    contexto.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=contexto) as resp:
        return resp.read().decode("utf-8", errors="replace")


def sem_tags(fragmento: str) -> str:
    texto = html.unescape(re.sub(r"<[^>]+>", " ", fragmento))
    return re.sub(r"\s+", " ", texto).strip()


def normalizar(texto: str) -> str:
    """Reduz a forma comparável: sem acento, sem caixa, sem espaço sobrando."""
    sem_acento = unicodedata.normalize("NFKD", texto)
    sem_acento = "".join(c for c in sem_acento if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", sem_acento).strip().lower()


def municipios_do_raio() -> set[str]:
    try:
        perfil = json.loads(PERFIL.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"[aviso] perfil.json: {exc}", file=sys.stderr)
        return set()
    return {normalizar(m["cidade"]) for m in perfil.get("municipios", [])}


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


def centro_paula_souza() -> dict[str, dict]:
    """Lê as quatro listagens de seleção pública do Centro Paula Souza.

    O CEETEPS tem 3.145 vagas autorizadas, das quais 1.657 de Professor de Ensino Médio
    e Técnico, e mantém Etec em Ilha Solteira, Andradina, Jales e Santa Fé do Sul. Como
    os editais saem descentralizados — um por unidade e por componente curricular —
    não há um edital único para acompanhar: o jeito é ler a listagem todo dia e filtrar
    pela coluna CIDADE.

    As quatro páginas são tabelas HTML. Extraímos `<tr>` e, dentro deles, `<td>/<th>`;
    achatar a página inteira em texto não funciona, porque o CSS inline vem antes do
    conteúdo e domina o resultado.
    """
    raio = municipios_do_raio()
    resultado = {}
    for rotulo, url in CPS_LISTAS.items():
        try:
            pagina = baixar_sem_verificar_tls(url)
        except (urllib.error.URLError, TimeoutError, ssl.SSLError) as exc:
            print(f"[aviso] centro paula souza / {rotulo}: {exc}", file=sys.stderr)
            resultado[rotulo] = {"linhas": [], "no_raio": [], "erro": str(exc)}
            continue

        linhas = []
        for tr in re.findall(r"<tr.*?</tr>", pagina, flags=re.S | re.I):
            celulas = [
                sem_tags(c) for c in re.findall(r"<t[dh].*?</t[dh]>", tr, flags=re.S | re.I)
            ]
            celulas = [c for c in celulas if c and c != "INSCREVA-SE"]
            # O cabeçalho se repete no corpo, e a linha única de aviso é o "nada aberto".
            if not celulas or CPS_VAZIO in " ".join(celulas).upper():
                continue
            if any(normalizar(c).startswith("cod da unidade") for c in celulas):
                continue
            linhas.append(celulas)

        no_raio = [c for c in linhas if any(normalizar(campo) in raio for campo in c)]
        resultado[rotulo] = {"linhas": linhas, "no_raio": no_raio}
    return resultado


NUMERO_COMUNICADO = re.compile(r"^(\d{3})[-_]")


def _dam_filhos(caminho: str) -> dict[str, dict]:
    """Lista os filhos de uma pasta do DAM do SEBRAE via `.1.json`.

    O caminho pode ter acento e espaço (as pastas do SEBRAE têm), então precisa ser
    escapado antes de virar URL — mas sem escapar as barras.
    """
    url = urllib.parse.quote(caminho, safe=":/") + ".1.json"
    dados = json.loads(baixar(url))
    return {
        nome: valor
        for nome, valor in dados.items()
        if isinstance(valor, dict) and not nome.startswith(("jcr:", "sling:"))
    }


def sebrae_sp(ano: int | None = None, limite: int = 8) -> list[dict]:
    """Enumera os comunicados de processo seletivo do SEBRAE-SP do ano.

    O SEBRAE-SP é o trilho de maior frequência do radar — cerca de um edital por semana,
    CLT, e as vagas saem por Escritório Regional (um deles é Andradina). O gargalo nunca
    foi achar o edital, e sim descobrir o requisito de formação antes de o prazo de ~5
    dias fechar: nem a listagem da RBO nem os portais de notícia trazem esse dado.

    Cada comunicado vive numa pasta própria do DAM, cujo nome já revela o número, o cargo
    e a unidade, e contém um único PDF. Devolvemos o link direto desse PDF — é no
    **Anexo I – Requisitos Exigidos e Desejáveis** que está a resposta, e ele precisa ser
    lido (o comunicado 030/2026, por exemplo, exigia Administração/Contábeis/Economia,
    o que exclui Ciências Sociais).
    """
    if ano is None:
        ano = datetime.date.today().year
    raio = municipios_do_raio()
    try:
        pastas = _dam_filhos(f"{SEBRAE_DAM}/{ano}")
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        print(f"[aviso] sebrae-sp: {exc}", file=sys.stderr)
        return []

    comunicados = []
    for nome, meta in pastas.items():
        achado = NUMERO_COMUNICADO.match(nome)
        if not achado:  # descarta a pasta "manuais" e PDFs soltos de aviso
            continue
        rotulo = normalizar(nome.replace("-", " "))
        try:
            arquivos = [
                f
                for f in _dam_filhos(f"{SEBRAE_DAM}/{ano}/{nome}")
                if f.lower().endswith(".pdf")
            ]
        except (urllib.error.URLError, TimeoutError, ValueError):
            arquivos = []
        # A pasta acumula os PDFs de todas as fases (relação de inscritos, habilitados,
        # cronograma retificado). Só o comunicado traz o Anexo I com os requisitos.
        comunicado = [a for a in arquivos if "comunicado" in normalizar(a)]
        arquivos = comunicado or arquivos
        comunicados.append(
            {
                "numero": achado.group(1),
                "pasta": nome,
                "criado": meta.get("jcr:created", ""),
                "no_raio": [m for m in raio if m in rotulo],
                "termos": [t for t in SEBRAE_TERMOS if t in rotulo],
                "comunicados": [
                    urllib.parse.quote(f"{SEBRAE_DAM}/{ano}/{nome}/{a}", safe=":/")
                    for a in arquivos
                ],
            }
        )
    comunicados.sort(key=lambda c: c["numero"], reverse=True)
    return comunicados[:limite]


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


CARTAO_AVALIA = re.compile(
    r'isotope-item[^>]*data-filter="(?P<situacao>[^"]+)"'
    r'.*?concurso\.jsp\?id=(?P<id>\d+)[^>]*>(?P<orgao>.*?)</a>'
    r'.*?<p[^>]*>(?P<andamento>.*?)</p>',
    re.S | re.I,
)
# Rótulos que o filtro da própria página usa; "novos" são os certames já cadastrados mas
# ainda sem inscrição, que é justamente o estado em que um edital novo aparece primeiro.
SITUACOES_AVALIA = {
    "novos": "próximos",
    "inscricao": "inscrição aberta",
    "andamento": "em andamento",
    "resultado": "com resultado",
    "finalizado": "finalizado",
}


def instituto_avalia() -> list[dict]:
    """Lê a listagem do Instituto Avalia, banca de Três Lagoas e do Detran-SP.

    A banca entrou no radar por atender Três Lagoas, mas ganhou peso em 22/07/2026, ao
    ser contratada para o concurso do Detran-SP (145 vagas de Agente Estadual de
    Trânsito, superior em qualquer área, com CIRETRAN em Ilha Solteira). Cada certame é
    um `isotope-item` cujo `data-filter` já traz a situação, então não precisamos
    interpretar o texto para saber se a inscrição está aberta.
    """
    try:
        pagina = baixar(AVALIA, decodificar="iso-8859-1")
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"[aviso] instituto avalia: {exc}", file=sys.stderr)
        return []

    certames, vistos = [], set()
    for m in CARTAO_AVALIA.finditer(pagina):
        orgao = sem_tags(m.group("orgao"))
        if not orgao or m.group("id") in vistos:
            continue
        vistos.add(m.group("id"))
        situacao = m.group("situacao").strip()
        certames.append(
            {
                "orgao": orgao,
                "situacao": SITUACOES_AVALIA.get(situacao, situacao),
                "andamento": sem_tags(m.group("andamento")),
                "link": f"https://www2.avalia.org.br/concurso.jsp?id={m.group('id')}",
                "aberto": situacao == "inscricao",
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
    if "cps" in fontes:
        resultado["centro_paula_souza"] = centro_paula_souza()
    if "sebrae" in fontes:
        resultado["sebrae_sp"] = sebrae_sp()
    if "bancas" in fontes:
        resultado["instituto_dom"] = instituto_dom()
        resultado["instituto_avalia"] = instituto_avalia()
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

    if "sebrae_sp" in resultado:
        comunicados = resultado["sebrae_sp"]
        linhas += [
            "## SEBRAE-SP — comunicados oficiais (repositório do próprio órgão)",
            "",
            "Um edital por vaga, em ritmo quase semanal, e cada um fica aberto ~5 dias. "
            "A listagem da banca não diz qual formação é exigida: **abrir o PDF do "
            "comunicado e ler o Anexo I – Requisitos Exigidos** antes de decidir. "
            "As vagas saem por Escritório Regional, e um deles é Andradina.",
            "",
        ]
        if not comunicados:
            linhas += ["Não foi possível ler o repositório.", ""]
        for c in comunicados:
            marca = " **← DENTRO DO RAIO**" if c["no_raio"] else ""
            if c["termos"]:
                marca += f" (termos: {', '.join(c['termos'])})"
            # A data de criação da pasta é o sinal de "novo desde a última execução" —
            # mais confiável que a listagem da banca, que não datava nada.
            criado = f" · publicado em {c['criado'][:16]}" if c["criado"] else ""
            linhas.append(f"- **{c['numero']}** — {c['pasta']}{criado}{marca}")
            for link in c["comunicados"]:
                linhas.append(f"  {link}")
        linhas.append("")

    if "centro_paula_souza" in resultado:
        linhas += [
            "## Centro Paula Souza (Etec e Fatec)",
            "",
            "Etec em Ilha Solteira, Andradina, Jales e Santa Fé do Sul. O componente "
            "**1029 – Sociologia** aceita \"Ciências Sociais (LP)\"; o **1990 – Filosofia** "
            "aceita \"Ciências Sociais com Habilitação em Filosofia (LP)\". Confirmar a "
            "titulação exigida na página de detalhe de cada edital.",
            "",
        ]
        for rotulo, dados in resultado["centro_paula_souza"].items():
            if dados.get("erro"):
                linhas += [f"### {rotulo}", "", f"Falhou: {dados['erro']}", ""]
                continue
            no_raio, todas = dados["no_raio"], dados["linhas"]
            linhas += [f"### {rotulo} ({len(todas)} aberto(s))", ""]
            if no_raio:
                linhas.append(f"**{len(no_raio)} DENTRO DO RAIO:**")
                for celulas in no_raio:
                    linhas.append("- " + " | ".join(celulas))
                linhas.append("")
            elif todas:
                linhas += ["Nada dentro do raio. Abertos no estado:", ""]
                for celulas in todas:
                    linhas.append("- " + " | ".join(celulas))
                linhas.append("")
            else:
                linhas += ["Nada aberto.", ""]

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

    if "instituto_avalia" in resultado:
        certames = resultado["instituto_avalia"]
        abertos = [c for c in certames if c["aberto"]]
        linhas += [
            "## Instituto Avalia (banca de Três Lagoas e do Detran-SP)",
            "",
            f"{len(abertos)} com inscrição aberta, de {len(certames)} listados.",
            "",
        ]
        for c in certames:
            selo = "**ABERTO**" if c["aberto"] else c["situacao"]
            linhas.append(f"- [{selo}] {c['orgao']} — {c['andamento']}\n  {c['link']}")
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
        choices=["sp", "cebraspe", "folha", "vunesp", "cps", "sebrae", "bancas"],
        help="limita a varredura (pode repetir)",
    )
    args = parser.parse_args()

    todas = {"sp", "cebraspe", "folha", "vunesp", "cps", "sebrae", "bancas"}
    fontes = set(args.fonte) if args.fonte else todas
    resultado = varrer(fontes)

    if args.json:
        print(json.dumps(resultado, ensure_ascii=False, indent=2))
    else:
        print(formatar(resultado))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
