# Automação de demandas por e-mail

Worker local que monitora uma caixa Gmail, pede ao Codex para analisar um repositório permitido e gerar uma especificação Markdown. A implementação só começa depois da aprovação explícita por e-mail.

## Como funciona

1. Envie um e-mail da conta Gmail configurada para ela mesma com `Projeto`, `Tipo` e `Título` no corpo.
2. O worker analisa o repositório permitido em modo somente leitura e envia a especificação como anexo Markdown.
3. Responda com `ANALISE <ID>` e novos pontos para pedir uma revisão, ou `APROVAR <ID>` para iniciar a implementação.
4. Após a aprovação, o worker cria uma branch e uma Git worktree local. O agente implementa e executa verificações sem fazer commit, push, merge ou deploy.

Cada demanda tem sua própria pasta `solicitacoes/<ID>/`. Revisões substituem a especificação corrente daquela demanda e guardam as versões anteriores em `historico/`.

## Requisitos

- Python 3.10 ou superior.
- Git instalado.
- Codex CLI instalado e autenticado no mesmo usuário que executará o worker.
- Uma conta Gmail com IMAP habilitado e uma senha de app configurada como descrito abaixo.
- Repositórios Git locais dos projetos que o worker poderá acessar.

## Configuração

Clone o repositório e entre na pasta:

```sh
git clone https://github.com/SEU-USUARIO/demandas-email-worker.git
cd demandas-email-worker
```

### Gmail e senha de app

O worker usa IMAP e SMTP. Para essa integração, crie uma senha de app separada; nunca use a senha normal da conta Google.

1. Entre na conta Gmail que será usada pelo worker e ative a verificação em duas etapas nas configurações de segurança da Conta Google.
2. Abra [Senhas de app da Conta Google](https://myaccount.google.com/apppasswords) e autentique-se novamente se solicitado.
3. Crie uma senha de app para o worker e copie o código de 16 caracteres apresentado. O Google só o mostra na criação; se perdê-lo, crie outro.
4. Copie `.env.example` para `.env`, informe o endereço Gmail e cole a senha em `GMAIL_APP_PASSWORD`. Não compartilhe nem versione esse arquivo.

O Google pode ocultar a opção de senha de app em algumas contas, incluindo algumas contas gerenciadas, contas com Proteção Avançada ou configurações de verificação somente por chave de segurança. A senha de app é menos segura que “Fazer login com o Google”; para esse caso de uso IMAP/SMTP, mantenha uma senha exclusiva e revogue-a quando não precisar mais dela. Consulte a [ajuda oficial do Google](https://support.google.com/accounts/answer/185833?hl=pt-BR).

#### Linux

```sh
cp .env.example .env
chmod 600 .env
```

Edite `.env` e substitua `exemplo.usuario@gmail.com` pela conta autorizada. O valor em `CODEX_BIN` pode ficar `codex` se o comando estiver no `PATH`; caso contrário, use o caminho completo do executável.

#### Windows (PowerShell)

```powershell
Copy-Item .env.example .env
notepad .env
```

Substitua o endereço de exemplo e preencha `GMAIL_APP_PASSWORD`. Se `codex` não estiver no `PATH`, informe em `CODEX_BIN` o caminho completo do executável Codex CLI.

### Repositórios permitidos

`config.json` controla quais projetos podem ser escolhidos nos e-mails. Ele é local e ignorado pelo Git. Crie-o copiando o exemplo correspondente ao sistema:

#### Linux

```sh
cp config.example.json config.json
```

Edite `projects_root` e os caminhos dos projetos. Os repositórios devem estar diretamente dentro da pasta indicada por `projects_root`. Exemplo fictício:

```json
{
  "projects_root": "/home/usuario/Projetos",
  "projects": {
    "meu-projeto": "/home/usuario/Projetos/meu-projeto"
  },
  "poll_seconds": 120,
  "max_email_bytes": 200000
}
```

#### Windows

```powershell
Copy-Item config.example.windows.json config.json
notepad config.json
```

Use caminhos absolutos do Windows e ajuste `projects_root` para a pasta que contém os repositórios. Exemplo fictício:

```json
{
  "projects_root": "C:\\Users\\Usuario\\Projects",
  "projects": {
    "meu-projeto": "C:\\Users\\Usuario\\Projects\\meu-projeto"
  },
  "poll_seconds": 120,
  "max_email_bytes": 200000
}
```

Cada projeto precisa existir e conter um diretório `.git`. Os exemplos `config.example.json` e `config.example.windows.json` usam caminhos e nomes fictícios; não os use sem ajustá-los. Para adicionar um projeto, inclua uma chave em `projects` e use essa chave no campo `Projeto` do e-mail.

## Iniciar e testar

Na primeira execução, o worker grava um ponto inicial para ignorar e-mails antigos. Faça isso antes de enviar uma demanda:

```sh
python3 worker.py once
```

No Windows, use `py -3 worker.py once`. Depois, inicie o monitoramento contínuo:

```sh
python3 worker.py run
```

No Windows: `py -3 worker.py run`. O worker verifica a caixa no intervalo de `poll_seconds` (120 segundos por padrão). O computador precisa permanecer ligado e conectado à internet.

### Execução contínua no Linux com systemd de usuário

O arquivo `demandas-worker.service` assume que o repositório está em `~/Projetos/demandas-email-worker`. Ajuste os caminhos do arquivo se instalou em outro local:

```sh
mkdir -p ~/.config/systemd/user
cp demandas-worker.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now demandas-worker.service
journalctl --user -u demandas-worker.service -f
```

Para manter o serviço ativo após logout, pode ser necessário habilitar o linger do usuário: `loginctl enable-linger "$USER"`.

### Execução contínua no Windows

Para manter o worker em execução, use o Agendador de Tarefas do Windows para iniciar `py` com os argumentos `-3 worker.py run`, definindo a pasta do repositório em **Iniciar em**. Configure a tarefa para executar com a conta que tem acesso aos repositórios e ao Codex CLI.

## Formato dos e-mails

Envie o pedido da conta Gmail configurada para ela mesma. Exemplo fictício:

```text
Projeto: meu-projeto
Tipo: feature
Título: Mostrar histórico de partidas

Quero consultar partidas anteriores com filtros por data e resultado.
Observações:
- Deve funcionar em telas pequenas.
- Não deve alterar partidas encerradas.
```

Tipos aceitos: `feature`/`funcionalidade`, `bug`/`erro`, `task`/`tarefa`, `chore`, `docs`/`documentacao` e `refactor`.

Após receber o anexo `especificacao.md`, responda ao e-mail. A primeira linha não vazia deve ser um dos comandos a seguir, usando o ID real que aparece no e-mail:

Para pedir uma nova análise, acrescente seus novos pontos nas linhas seguintes:

```text
ANALISE DEM-AAAAMMDD-XXXXXXXX
Considerar também estes pontos:
- Novo requisito ou correção.
```

O analista examina novamente o repositório e envia uma especificação completa consolidada. A especificação anterior permanece em `solicitacoes/<ID>/historico/`; essa etapa não cria branch nem modifica o código.

Para aprovar a especificação atual e iniciar a implementação:

```text
APROVAR DEM-AAAAMMDD-XXXXXXXX
```

A implementação fica em `solicitacoes/<ID>/worktree/`, em uma branch local baseada no tipo da demanda. O agente deixa as mudanças para revisão, sem commit, push, merge ou deploy.

Comandos úteis:

```sh
python3 worker.py status DEM-AAAAMMDD-XXXXXXXX
python3 worker.py resend-spec DEM-AAAAMMDD-XXXXXXXX
```

`resend-spec` reenvia o anexo da especificação que aguarda aprovação.

## Dados e segurança

- `.env`, `config.json`, `data/` e `solicitacoes/` são ignorados pelo Git. O estado `data/gmail-uid.json` deve ser preservado entre reinicializações para que mensagens antigas não sejam redetectadas.
- Apenas mensagens enviadas pela conta configurada para ela mesma são processadas. Outros e-mails não são marcados como lidos.
- O analista trabalha em modo somente leitura. O desenvolvedor trabalha em uma worktree isolada após a aprovação.
- A implementação não faz commit, push, merge, deploy ou alterações em dados de produção.
- Não inclua segredos nos pedidos. O conteúdo dos e-mails é tratado como entrada não confiável.
- MCPs configurados podem ser usados conforme as instruções locais do fluxo; leituras de banco devem se limitar a schema e metadados.
- O Gmail IMAP/SMTP com senha de app foi escolhido por simplicidade. OAuth pela Gmail API é uma evolução recomendada para reduzir o uso de senhas de app.
- A Brevo não é necessária para este fluxo. Ela pode ser considerada para um endereço dedicado com domínio próprio e recebimento por webhook.
