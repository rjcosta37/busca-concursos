# Busca de concursos

Automação diária de busca de concursos públicos para um perfil de nível superior
com formação em Ciências Sociais e pós em Publicidade, residente em Ilha Solteira/SP.
A varredura cobre um raio de aproximadamente 100 km, incluindo o lado sul-mato-grossense
(Três Lagoas, Selvíria, Aparecida do Taboado, Paranaíba) e o noroeste paulista
(Andradina, Pereira Barreto, Castilho, Santa Fé do Sul, Jales).

## Estrutura

```
config/perfil.json           perfil do candidato, municípios do raio e regras de elegibilidade
scripts/pci.py               cliente do servidor MCP público da PCI Concursos
scripts/buscar_concursos.py  varre por município e por cargo, e monta o rascunho do relatório
scripts/diarios.py           busca atos de concurso nos diários oficiais
scripts/fontes_extras.py     consulta portais e bancas (portal SP, Cebraspe, Folha Dirigida, bancas regionais)
scripts/enviar_email.py      envia um relatório por SMTP
scripts/rodar_diario.py      rotina completa: varre, grava e envia
relatorios/AAAA-MM-DD.md     relatório de cada dia (é também o corpo do e-mail)
.env.example                 quais secrets cadastrar
```

Tudo roda com a biblioteca padrão do Python 3.10+ — não há dependências para instalar.

## Uso

```bash
python3 scripts/rodar_diario.py --diagnostico  # o canal de e-mail está configurado?
python3 scripts/rodar_diario.py --dry-run      # rotina completa, sem enviar de verdade
python3 scripts/rodar_diario.py                # rotina completa
python3 scripts/rodar_diario.py --sem-diarios  # pula os diários (varredura rápida)
python3 scripts/rodar_diario.py --sem-portais  # pula os portais e bancas
```

A rotina completa leva cerca de 40 segundos: 20 na PCI, 18 nos diários e 3 nos portais.

Os passos avulsos continuam disponíveis:

```bash
python3 scripts/buscar_concursos.py            # imprime o rascunho do dia
python3 scripts/buscar_concursos.py --salvar   # grava em relatorios/AAAA-MM-DD.md
python3 scripts/buscar_concursos.py --json     # resultado bruto, para depuração
python3 scripts/diarios.py --dias 30           # só os diários, janela de 30 dias
python3 scripts/diarios.py --tudo              # inclui atos de andamento sem corte
python3 scripts/fontes_extras.py               # só os portais e bancas
python3 scripts/fontes_extras.py --fonte sp    # limita a uma fonte (pode repetir)
python3 scripts/enviar_email.py relatorios/2026-07-27.md --dry-run
```

`rodar_diario.py` sai com código 3 quando grava o relatório mas não encontra canal de
e-mail. O código é diferente de 0 de propósito: sinaliza que o relatório existe e precisa
ser entregue por outro caminho, em vez de mascarar a falha de envio.

## O que a varredura automática cobre (e o que não cobre)

### Portais de concurso

`buscar_concursos.py` consulta o conector MCP oficial da PCI Concursos
(`https://mcp.pciconcursos.com.br/mcp`), que indexa **apenas concursos com inscrição
aberta**. Ele não enxerga certames autorizados ou previstos, nem cobre integralmente
editais de bancas regionais pequenas.

A varredura tem duas passadas. A primeira busca por município, percorrendo os 43
municípios do raio. A segunda busca pelos termos de formação do candidato
(`config/perfil.json > busca_por_cargo`) em SP e MS, sem filtro de localidade — é ela
que alcança concursos estaduais, federais e de universidades, cuja lotação a PCI não
associa a nenhum dos municípios do raio. Os achados dessa segunda passada saem em uma
seção própria do relatório, marcados como fora do raio, porque a lotação real só
aparece no edital.

A triagem de elegibilidade é aproximada: a PCI informa a escolaridade no nível do
certame, nunca por cargo, então um cargo marcado com "aderência alta" ainda precisa ser
confirmado no edital.

### Diários oficiais

`diarios.py` cobre duas fontes com API utilizável sem autenticação:

| Diário | Como | Situação |
| --- | --- | --- |
| **DOU** (União) | busca do `in.gov.br`, lendo o JSON embutido na página | automatizado |
| **DO municipais** | API do Querido Diário (Open Knowledge Brasil) | automatizado, 11 dos 43 municípios |
| **DOE-SP** | — | manual: a API devolve lista vazia para consulta anônima |
| **DOE-MS** | — | manual: app Next.js sem endpoint público estável |
| **DO dos municípios de MS** (Assomasul) | — | manual: busca exige sessão autenticada |

Os três últimos aparecem no relatório como uma lista "Conferir manualmente", com a URL e
o período sugerido já montados.

Duas limitações valem ser conhecidas. A cobertura municipal do Querido Diário é parcial —
Andradina, Guaraçaí, Lavínia, Valparaíso, General Salgado, Dirce Reis e Inocência têm
busca full-text; Ilha Solteira, Três Lagoas, Selvíria, Santa Fé do Sul e Jales ainda não
são coletados (veja `qd_nivel` em `config/perfil.json`). E a classificação entre
"abertura" e "andamento" roda sobre o trecho que a busca devolve, não sobre o documento
inteiro, então um edital de abertura pode cair em "andamento" quando o recorte pega o
sumário do diário. Por isso os dois grupos vão no relatório, com link para o PDF.

### Portais e bancas

`fontes_extras.py` cobre o que a PCI e os diários deixam de fora, sobretudo certames
**autorizados e previstos**:

| Fonte | Como | O que entrega |
| --- | --- | --- |
| **Portal de Concursos do Estado de SP** | HTML servido pelo servidor (ISO-8859-1) | inscrições abertas, **autorizados** e próximos concursos do Estado |
| **Cebraspe** | API JSON em `apis.cebraspe.org.br`, sem autenticação | eventos agrupados por fase (novos, inscrições abertas) |
| **Folha Dirigida** (Qconcursos) | HTML renderizado no servidor | editorias de concursos abertos, previstos, SP e MS |
| **VUNESP** | — | manual: o site responde 403 a qualquer requisição fora do navegador |
| **Instituto DOM** | HTML na página inicial, lido por regex | certames da banca de Andradina, com o selo de inscrição aberta |
| **Bancas regionais** | — | manual: IBAM, Instituto Avalia, CONSESP, Valespe e FCC |

O portal do Estado de SP é o mais valioso dos quatro: é a fonte oficial e a única
automatizável para concursos autorizados em São Paulo, já que a API do DOE-SP devolve
lista vazia para consulta anônima. A seção de autorizados mostra apenas as autorizações
vigentes no momento, não o histórico, então vale conferir todo dia.

A página da VUNESP entra no relatório como um link para conferência, porque o site
bloqueia `curl` e `urllib` — inclusive na raiz. Ele funciona por busca web.

#### Por que vigiar bancas pequenas

Os editais que interessam de verdade — os de dentro do raio — não saem pelas bancas
grandes. Cada prefeitura da região tem a sua: Instituto DOM em Andradina, IBAM em Ilha
Solteira, Instituto Avalia em Três Lagoas, CONSESP em Santa Fé do Sul e Valespe em
Castilho. A banca publica o edital antes de qualquer portal indexar, então ela é a
fonte mais rápida que existe para a região.

Só o Instituto DOM entrega HTML raspável, e com uma peculiaridade: a mesma URL responde
ora em UTF-8, ora em ISO-8859-1, sempre com `Content-Type: ISO-8859-1` no cabeçalho.
Por isso `baixar_adaptativo()` tenta UTF-8 estrito e cai para latin-1 quando falha, em
vez de confiar no cabeçalho. Os demais respondem 403 ou montam a lista por JavaScript e
saem no relatório como lista de conferência manual.

A FCC entra nessa lista por um motivo diferente: a página inicial dela fica meses
desatualizada (em 28/07/2026 ainda listava certames de 2025), mas é a banca dos órgãos
ambientais paulistas, que são os que abrem vaga pedindo bacharelado em Ciências Sociais
nominalmente — foi o caso do concurso da Fundação Florestal encerrado em 19/07/2026.

### Conferência manual

O relatório é um *rascunho*. Antes de enviar, vale conferir as fontes listadas em
`config/perfil.json > fontes_extras`, entre elas o portal URH do Centro Paula Souza
(Etecs de Ilha Solteira, Andradina, Jales e Santa Fé do Sul), UFMS, IFMS e os portais
das prefeituras da região.

## Envio do e-mail

Há dois caminhos. O preferencial é o conector Composio/Gmail da automação, usado
diretamente pelo agente. O fallback é o SMTP deste repositório, que funciona sozinho e
não depende de nenhum conector.

### Cadastro do SMTP (dois secrets)

Em Cursor Dashboard > Cloud Agents > Secrets, cadastre:

| Variável | Exemplo |
| --- | --- |
| `GMAIL_USER` | `voce@gmail.com` |
| `GMAIL_APP_PASSWORD` | Senha de App de 16 caracteres |

A Senha de App é gerada em https://myaccount.google.com/apppasswords e exige verificação
em duas etapas ativa. A senha normal da conta não funciona: o Gmail bloqueia login SMTP
com ela. Host e porta são inferidos (`smtp.gmail.com:587`), então não precisam ser
cadastrados.

Para um remetente que não seja Gmail, use `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER` e
`SMTP_PASSWORD`. `EMAIL_FROM` assume o usuário autenticado e `EMAIL_TO` já aponta para
`ricardojc011@gmail.com`. Veja `.env.example`.

Os secrets são injetados na VM no início de cada execução, então passam a valer a partir
da execução seguinte ao cadastro. Para conferir se chegaram:

```bash
python3 scripts/rodar_diario.py --diagnostico
```

O assunto é extraído do primeiro cabeçalho `#` do relatório, o que mantém o formato
`Concursos — DD/MM/AAAA — N encontrados`.
