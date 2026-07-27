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
scripts/buscar_concursos.py  varre os municípios e monta o rascunho do relatório
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
```

Os passos avulsos continuam disponíveis:

```bash
python3 scripts/buscar_concursos.py            # imprime o rascunho do dia
python3 scripts/buscar_concursos.py --salvar   # grava em relatorios/AAAA-MM-DD.md
python3 scripts/buscar_concursos.py --json     # resultado bruto, para depuração
python3 scripts/enviar_email.py relatorios/2026-07-27.md --dry-run
```

`rodar_diario.py` sai com código 3 quando grava o relatório mas não encontra canal de
e-mail. O código é diferente de 0 de propósito: sinaliza que o relatório existe e precisa
ser entregue por outro caminho, em vez de mascarar a falha de envio.

## O que a varredura automática cobre (e o que não cobre)

`buscar_concursos.py` consulta o conector MCP oficial da PCI Concursos
(`https://mcp.pciconcursos.com.br/mcp`), que indexa **apenas concursos com inscrição
aberta**. Ele não enxerga certames autorizados ou previstos, nem cobre integralmente
editais de bancas regionais pequenas.

Por isso o script produz um *rascunho*. Antes de enviar o relatório, é preciso conferir
manualmente as fontes listadas em `config/perfil.json > fontes_extras`, entre elas o
portal URH do Centro Paula Souza (Etecs de Ilha Solteira, Andradina, Jales e
Santa Fé do Sul), UFMS, IFMS, os portais das prefeituras da região e os diários oficiais
da União, de São Paulo e de Mato Grosso do Sul.

A triagem de elegibilidade também é aproximada: a PCI informa a escolaridade no nível do
certame, nunca por cargo, então um cargo marcado com "aderência alta" ainda precisa ser
confirmado no edital.

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
