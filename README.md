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
relatorios/AAAA-MM-DD.md     relatório de cada dia (é também o corpo do e-mail)
```

Tudo roda com a biblioteca padrão do Python 3.10+ — não há dependências para instalar.

## Uso

```bash
python3 scripts/buscar_concursos.py            # imprime o rascunho do dia
python3 scripts/buscar_concursos.py --salvar   # grava em relatorios/AAAA-MM-DD.md
python3 scripts/buscar_concursos.py --json     # resultado bruto, para depuração

python3 scripts/enviar_email.py relatorios/2026-07-27.md --dry-run
python3 scripts/enviar_email.py relatorios/2026-07-27.md
```

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

O caminho preferencial é o conector Composio/Gmail configurado na automação. Quando ele
não está disponível, `scripts/enviar_email.py` envia por SMTP usando estes secrets
(Cursor Dashboard > Cloud Agents > Secrets):

| Variável | Exemplo | Observação |
| --- | --- | --- |
| `SMTP_HOST` | `smtp.gmail.com` | obrigatória |
| `SMTP_PORT` | `587` | 587 usa STARTTLS, 465 usa SSL; padrão 587 |
| `SMTP_USER` | `voce@gmail.com` | obrigatória |
| `SMTP_PASSWORD` | senha de app | obrigatória — no Gmail, use uma Senha de App |
| `EMAIL_FROM` | `voce@gmail.com` | opcional, padrão `SMTP_USER` |
| `EMAIL_TO` | `ricardojc011@gmail.com` | opcional, já é o padrão |

O assunto é extraído do primeiro cabeçalho `#` do relatório, o que mantém o formato
`Concursos — DD/MM/AAAA — N encontrados`.
