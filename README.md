# Automação de demandas por e-mail

Worker local que monitora uma caixa Gmail, pede ao Codex para analisar um ou mais repositórios permitidos e gerar uma especificação Markdown. Também oferece um formulário web local que envia a demanda para a própria conta Gmail. A implementação só começa depois da aprovação explícita por e-mail.

## Como funciona

1. Envie um e-mail da conta Gmail configurada para ela mesma com `Projeto` ou `Projetos`, `Tipo` e `Título` no corpo, ou use a interface web local.
2. O worker analisa todos os repositórios selecionados em modo somente leitura e envia a especificação como anexo Markdown.
3. Responda com `ANALISE <ID>` e novos pontos para pedir uma revisão, ou `APROVAR <ID>` para iniciar a implementação.
4. Após a aprovação, cada branch selecionada é usada no respectivo repositório; bases `main`/`master` criam uma branch exclusiva da demanda. O agente implementa, executa as verificações e cria commits locais em cada checkout alterado. O worker não faz push, merge ou deploy.

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

### MCPs no Codex não interativo

O worker usa a configuração MCP global do mesmo usuário do Codex (`CODEX_HOME` ou `~/.codex`) e aguarda a inicialização dos servidores opcionais até o timeout configurado por servidor. Essa espera se aplica genericamente a todos os MCPs habilitados, incluindo servidores HTTP e STDIO. Nas análises, o agente deve consultar MCPs relevantes a integrações externas e registrar o servidor se a conexão falhar, em vez de inferir indisponibilidade apenas pela ausência de arquivos no repositório.

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

`config.json` controla quais projetos podem ser escolhidos nos e-mails e na interface web. Ele é local e ignorado pelo Git. Crie-o copiando o exemplo correspondente ao sistema:

#### Linux

```sh
cp config.example.json config.json
```

Edite `projects_root` e os caminhos dos projetos. Os repositórios devem estar diretamente dentro da pasta indicada por `projects_root`. O worker pode receber N projetos na mesma demanda. Exemplo fictício:

```json
{
  "projects_root": "/home/usuario/Projetos",
  "projects": {
    "meu-projeto": "/home/usuario/Projetos/meu-projeto",
    "meus-workflows": "/home/usuario/Projetos/meus-workflows"
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
    "meu-projeto": "C:\\Users\\Usuario\\Projects\\meu-projeto",
    "meus-workflows": "C:\\Users\\Usuario\\Projects\\meus-workflows"
  },
  "poll_seconds": 120,
  "max_email_bytes": 200000
}
```

Cada projeto precisa existir e conter um diretório `.git`. Os exemplos `config.example.json` e `config.example.windows.json` usam caminhos e nomes fictícios; não os use sem ajustá-los. Para adicionar um projeto, inclua uma chave em `projects` e use-a no campo `Projetos` do e-mail ou na interface web.

### Interface web local

Inicie o formulário com:

```sh
python3 worker.py web
```

Abra `http://127.0.0.1:8765`, selecione um ou mais repositórios, informe tipo, título, descrição e, opcionalmente, uma branch base para cada repositório. O formulário envia o pedido para a própria conta Gmail configurada; o worker o processará no próximo ciclo. O servidor escuta somente em `127.0.0.1` e não expõe a senha SMTP no navegador. Para acessá-lo de outro computador, use um túnel SSH para a máquina onde o worker está instalado; não exponha a porta diretamente à Internet.

No Windows, inicie com `py -3 worker.py web`. Mantenha também `python3 worker.py run` (Linux) ou `py -3 worker.py run` (Windows) ativo em outro terminal para que o worker receba e processe o e-mail.

### Publicação do formulário no VPS

O deploy de produção publica somente o formulário de envio. O worker de e-mail, Codex, MCPs e clones de desenvolvimento continuam na máquina local; o VPS não recebe os arquivos de autenticação do Codex nem os repositórios. O formulário manda o pedido para a conta Gmail configurada, e o worker local continua processando a mensagem.

O stack de produção usa containers e rede próprios: um container para o formulário e outro Nginx interno com autenticação Basic. Nenhum deles publica portas no host. O Nginx existente do VPS funciona como gateway HTTPS e encaminha `https://demandas.guandalini.uk` para o Nginx isolado. A rede externa `app_app-network` é usada somente para essa conexão entre os dois Nginx. No Cloudflare, crie `CNAME demandas -> agente.guandalini.uk` com proxy habilitado; o certificado Cloudflare Origin já instalado no VPS cobre `*.guandalini.uk`.

O GitHub Actions constrói e valida as duas imagens em pull requests. Após merge em `main`, publica `ghcr.io/g-guandalini/demandas-email-worker` e `ghcr.io/g-guandalini/demandas-email-worker-nginx` e solicita ao VPS que atualize o stack. O token temporário de leitura do GHCR é enviado pelo canal SSH e removido depois do pull; não é salvo no VPS. Os dados de runtime ficam fora das imagens em `/opt/demandas/.env` e `/opt/demandas/config.json`; `.env` também guarda a senha de acesso à página e nunca deve ser versionado.

Para inicializar o VPS uma vez, crie `/opt/demandas`, copie para lá `compose.production.yaml`, `.env`, `config.json` (somente as chaves permitidas, sem caminhos locais) e `deploy/deploy.sh`, configure a chave SSH de deploy restrita ao script e instale `deploy/edge-server.conf` em `/root/infra_agente/nginx/conf.d/demandas.conf`. Valide a configuração e recarregue o Nginx existente. O Actions usa os secrets `DEPLOY_SSH_KEY` e `DEPLOY_KNOWN_HOSTS`. O usuário e a senha da página são `DEMANDAS_WEB_USER` e `DEMANDAS_WEB_PASSWORD`. O workflow e a configuração ficam nos arquivos `compose.production.yaml`, `Dockerfile`, `Dockerfile.nginx` e `.github/workflows/deploy-production.yml`.

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

Para trabalhar em vários repositórios na mesma demanda, use `Projetos` com as chaves separadas por vírgula. `Branch base` define uma base comum; para branches distintas use `Branch base [chave]`:

```text
Projetos: agente-ai, meus-workflows
Tipo: feature
Título: Isolar o processamento por conexão
Branch base [agente-ai]: feature/minha-branch
Branch base [meus-workflows]: main

Atualizar o backend e os workflows para manter cada conversa associada à conexão correta.
```

As chaves precisam estar em `config.json`. Uma branch diferente de `main`/`master` é usada diretamente no respectivo repositório; `main`, `master` ou uma base em branco criam a branch da demanda naquele repositório.

`Branch base` é opcional. Em pedidos com vários repositórios, `Branch base [chave]` permite escolher uma branch por repositório; um `Branch base` sem chave é aplicado a todos. Sem base informada, o worker resolve `main` ou `master` de cada repositório. Antes da análise, cria uma worktree somente para leitura por repositório e atualiza cada uma com `git pull --ff-only origin <branch>` quando a branch existe no remoto.

Cada branch informada deve corresponder a uma branch **local existente** no respectivo repositório permitido. Se houver uma branch correspondente em `origin`, a análise usa a versão atualizada; se ela existir somente localmente, usa o commit local e informa a origem no e-mail. Alterações ainda não commitadas não fazem parte da análise. Na aprovação, branches diferentes de `main`/`master` são usadas diretamente em seus respectivos checkouts; branches principais ou bases vazias geram a branch da demanda naquele repositório.

Na implementação, o agente recebe acesso de escrita somente aos checkouts selecionados na demanda, cada um em sua pasta e branch. O worker verifica todos os checkouts antes de trocar qualquer branch e interrompe a demanda se encontrar alterações locais não relacionadas. Depois, cada checkout permanece na branch utilizada para você inspecionar o resultado. Uma demanda que falhou pode ser retomada em suas branches; outros trabalhos ficam bloqueados nos checkouts que ainda tenham alterações sem commit.

Quando a implementação incluir migrations, o agente pode aplicá-las para validação usando as credenciais já configuradas no `.env` de um repositório selecionado, somente após confirmar que o banco é local de desenvolvimento/teste. Pode ler apenas as variáveis de conexão necessárias; não deve mostrar nem registrar seus valores. Migrations nunca são executadas em banco remoto, de produção ou de destino incerto.

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
- O analista trabalha em worktrees temporárias somente para leitura. O desenvolvedor altera os checkouts selecionados após a aprovação; o worker exige que todos estejam limpos antes de iniciar outra demanda.
- O agente pode criar um commit local na branch de trabalho escolhida pelo fluxo, depois das verificações e somente se houver alterações. Nunca faça commit em `main`/`master`; não faça push, merge, deploy ou alterações em dados de produção.
- Migrations são permitidas somente em bancos locais de desenvolvimento/teste, conforme descrito acima.
- Não inclua segredos nos pedidos. O conteúdo dos e-mails é tratado como entrada não confiável.
- MCPs configurados podem ser usados conforme as instruções locais do fluxo; leituras de banco devem se limitar a schema e metadados.
- O Gmail IMAP/SMTP com senha de app foi escolhido por simplicidade. OAuth pela Gmail API é uma evolução recomendada para reduzir o uso de senhas de app.
- A Brevo não é necessária para este fluxo. Ela pode ser considerada para um endereço dedicado com domínio próprio e recebimento por webhook.
