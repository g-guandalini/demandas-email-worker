# Automação de demandas por e-mail

Worker local que monitora uma caixa Gmail, pede ao Codex para analisar um repositório permitido e gerar uma especificação Markdown. A implementação só começa depois da aprovação explícita por e-mail.

## Como funciona

1. Envie um e-mail da conta Gmail configurada para ela mesma com `Projeto`, `Tipo` e `Título` no corpo.
2. O worker analisa o repositório permitido em modo somente leitura e envia a especificação como anexo Markdown.
3. Responda com `ANALISE <ID>` e novos pontos para pedir uma revisão, ou `APROVAR <ID>` para iniciar a implementação.
4. Após a aprovação, o worker usa diretamente a branch informada, exceto se ela for `main` ou `master`; nesses casos, cria uma branch exclusiva da demanda. O agente implementa, executa as verificações e cria um commit local na branch de trabalho quando houver alterações. O worker não faz push, merge ou deploy.

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

### Docker local para o agente desenvolvedor

No Linux, o agente desenvolvedor pode usar o daemon local pelo socket Unix. Configure `CODEX_DOCKER_SOCKET=/var/run/docker.sock` no `.env` (ou ajuste para o caminho absoluto do socket local). O usuário que inicia o worker precisa ter acesso ao socket, normalmente pelo grupo `docker`. O worker libera esse único socket somente para a execução de implementação, mantém o sandbox `workspace-write` e remove configurações de Docker TCP/SSH do ambiente do agente. O agente de análise não recebe acesso ao Docker.

O socket Docker permite controlar o daemon e os containers locais. Use somente com Docker de desenvolvimento/teste, conforme as restrições de `AGENTS.md`; não aponte essa configuração para daemon remoto ou de produção. No Windows, esta opção não libera Docker Desktop automaticamente.

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

Durante uma tarefa, o terminal registra o ID e a etapa iniciada, informa quando o Codex começou e imprime `em processamento há N minuto(s)` a cada dois minutos. Ao terminar, mostra conclusão ou falha. Essas mensagens também ficam disponíveis no log do terminal ou do serviço que iniciou o worker. Após atualizar o código do worker, reinicie o processo para carregar a versão nova.

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
Branch base: feature/nova-interface

Quero consultar partidas anteriores com filtros por data e resultado.
Observações:
- Deve funcionar em telas pequenas.
- Não deve alterar partidas encerradas.
```

Tipos aceitos: `feature`/`funcionalidade`, `bug`/`erro`, `task`/`tarefa`, `chore`, `docs`/`documentacao` e `refactor`.

`Branch base` é opcional. Sem esse campo, o worker localiza `main` ou `master`. Antes de cada análise, ele cria uma worktree temporária somente para leitura e executa `git pull --ff-only origin <branch>` nela; a análise usa o resultado atualizado. A branch padrão precisa estar disponível localmente e no remoto `origin`. Se não for possível atualizar em avanço linear, o worker para e registra o erro.

Quando `Branch base` é informado, deve corresponder a uma branch **local existente** no repositório permitido. Se houver uma branch correspondente em `origin`, o worker executa `git pull --ff-only origin <branch>` na worktree temporária e analisa a versão atualizada. Se ela existir somente localmente, o worker analisa o commit local e informa essa origem no e-mail; falhas ao consultar o remoto continuam sendo tratadas como erro. Alterações ainda não commitadas não fazem parte da análise. Na aprovação, se a branch for diferente de `main`/`master`, o worker seleciona essa mesma branch no checkout clonado e atualiza por `origin` quando houver branch remota; se for somente local, continua a partir do commit local. O agente trabalha diretamente nela. Se a branch informada for `main` ou `master`, o worker exige a atualização remota e cria uma branch nova com o ID da demanda.

Na implementação, o agente trabalha diretamente na pasta configurada para o projeto em `config.json`. Antes de trocar de branch, o worker verifica se o checkout está limpo. Se houver alterações locais, ele interrompe a demanda sem trocar de branch ou tocar nesses arquivos. Depois da conclusão, o checkout permanece na branch utilizada para que você possa inspecionar e executar o projeto nesse mesmo diretório. Uma demanda que falhou e deixou alterações sem commit pode ser retomada nessa branch; outras demandas ficam bloqueadas até que o checkout esteja limpo.

Quando a implementação incluir migrations, o agente pode aplicá-las para validação usando as credenciais já configuradas no `.env` do projeto, somente após confirmar que o banco é local de desenvolvimento/teste. Para isso, pode ler apenas as variáveis de conexão necessárias no `.env` do checkout clonado; não deve mostrar nem registrar seus valores. Migrations nunca são executadas em banco remoto, de produção ou de destino incerto. Se o ambiente local não puder ser confirmado, o agente não executa a migration e registra essa limitação no resultado.

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

A implementação acontece no diretório clonado indicado em `config.json`. Se `Branch base` não for informada ou for `main`/`master`, o worker cria uma branch local nomeada exclusivamente com o ID gerado para a demanda em minúsculas, por exemplo `dem-20260924-c76eeed1`. Se for informada outra branch, o agente trabalha diretamente nela. O agente cria commit local na branch utilizada somente quando houver alterações; tarefas apenas de validação podem terminar sem commit. O checkout permanece na branch utilizada. O worker não faz push, merge ou deploy.

Quando a implementação termina, o e-mail de conclusão traz apenas instruções para os próximos passos. O relatório do agente é enviado como anexo `resultado.md`. Para encadear outra demanda sobre a implementação, informe a branch utilizada no campo `Branch base`; ela será reutilizada diretamente, exceto se for `main`/`master`.

Comandos úteis:

```sh
python3 worker.py status DEM-AAAAMMDD-XXXXXXXX
python3 worker.py resend-spec DEM-AAAAMMDD-XXXXXXXX
python3 worker.py reanalyze DEM-AAAAMMDD-XXXXXXXX
python3 worker.py resend-result DEM-AAAAMMDD-XXXXXXXX
```

`resend-spec` reenvia o anexo da especificação que aguarda aprovação.
`reanalyze` tenta novamente uma solicitação com status `erro_analise`; branches existentes apenas localmente são analisadas sem exigir publicação em `origin`.
O status da implementação registra o envio do e-mail de conclusão. Se o SMTP falhar, o relatório continua salvo e pode ser reenviado com `resend-result`.

## Dados e segurança

- `.env`, `config.json`, `data/` e `solicitacoes/` são ignorados pelo Git. O estado `data/gmail-uid.json` deve ser preservado entre reinicializações para que mensagens antigas não sejam redetectadas.
- Apenas mensagens enviadas pela conta configurada para ela mesma são processadas. Outros e-mails não são marcados como lidos.
- O analista trabalha em modo somente leitura numa worktree temporária. O desenvolvedor altera o checkout clonado do projeto após a aprovação; o worker exige um checkout limpo antes de iniciar outra demanda.
- O agente pode criar um commit local na branch de trabalho escolhida pelo fluxo, depois das verificações e somente se houver alterações. Nunca faça commit em `main`/`master`; não faça push, merge, deploy ou alterações em dados de produção.
- Migrations são permitidas somente em bancos locais de desenvolvimento/teste, conforme descrito acima.
- Não inclua segredos nos pedidos. O conteúdo dos e-mails é tratado como entrada não confiável.
- MCPs configurados podem ser usados conforme as instruções locais do fluxo; leituras de banco devem se limitar a schema e metadados.
- O Gmail IMAP/SMTP com senha de app foi escolhido por simplicidade. OAuth pela Gmail API é uma evolução recomendada para reduzir o uso de senhas de app.
- A Brevo não é necessária para este fluxo. Ela pode ser considerada para um endereço dedicado com domínio próprio e recebimento por webhook.
