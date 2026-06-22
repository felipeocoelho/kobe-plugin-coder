# Security Baseline — checklist universal

Lista binária de itens que toda aplicação exposta a entrada externa deve cobrir. Cada item tem estado **[aberto]** ou **[fechado]**. Item só vira **[fechado]** quando o agente pode mostrar evidência (código, config, teste) — não basta dizer "está ok".

Como usar:
- Em projeto novo: clonar essa lista no CLAUDE.md (ou em `docs/security.md`) marcando tudo como [aberto].
- A cada feature: revisar itens afetados; reabrir o que regrediu.
- Em revisão periódica (SPR — ver `spr.md`): varredura completa.

Convenção: substituir `[aberto]` por `[fechado]` quando garantido. Não apagar item.

---

## 1. Identidade e sessão

### 1.1 [aberto] Senha hashada com algoritmo moderno

**tu cobra:** se vazar o banco amanhã, quanto tempo o atacante leva pra ter as senhas em texto plano?
**eu garanto:** uso bcrypt (cost ≥ 12) ou argon2id; nunca SHA-família crua; senha nunca aparece em log nem em URL.

### 1.2 [aberto] Sessão com expiração e revogação

**tu cobra:** se um funcionário sair hoje, em quanto tempo o acesso dele cai sem eu tocar no banco?
**eu garanto:** sessão expira em janela definida (máx 30 dias absoluto, 7 dias inativa); logout invalida no servidor; existe rota administrativa pra revogar sessão de usuário específico.

### 1.3 [aberto] Cookie de sessão protegido

**tu cobra:** se alguém abrir o devtools no navegador da vítima ou interceptar a request, ele consegue clonar a sessão?
**eu garanto:** cookie com `HttpOnly`, `Secure`, `SameSite=Lax` ou `Strict`; sem token de sessão em localStorage; sem token em query string.

### 1.4 [aberto] OAuth com escopo mínimo e redirect URI fixo

**tu cobra:** se um phisher montar uma página fake usando nosso client_id, ele consegue redirecionar o code de volta pra ele?
**eu garanto:** redirect URI registrado é exato (sem wildcard); escopos pedidos são os mínimos; `state` validado em todo callback; PKCE em cliente público.

### 1.5 [aberto] Proteção contra força bruta no login

**tu cobra:** se eu deixar um script atacando /login a noite toda, o que acontece?
**eu garanto:** rate limit por IP + por usuário no login; lockout temporário após N falhas consecutivas; resposta uniforme pra "usuário não existe" e "senha errada" (não vaza enumeração).

### 1.6 [aberto] MFA disponível pra contas privilegiadas

**tu cobra:** se a senha do admin vazar num dump de outro site, qual a próxima barreira?
**eu garanto:** MFA (TOTP ou WebAuthn) habilitável; obrigatório pra papéis admin/sysadmin; recovery codes gerados e exibidos uma vez só.

### 1.7 [aberto] Reset de senha sem janela explorável

**tu cobra:** se eu pedir reset pro e-mail de um usuário que existe e outro que não existe, dá pra saber qual é qual pelo tempo de resposta ou pela mensagem?
**eu garanto:** mensagem idêntica nos dois casos; token de reset assinado, com TTL curto (≤ 1h), uso único; após reset todas as sessões anteriores são invalidadas.

### 1.8 [aberto] CSRF coberto em forms e server actions

**tu cobra:** se um site malicioso fizer uma request POST autenticada contra nossa API enquanto o usuário está logado, ele consegue executar ação em nome dele?
**eu garanto:** token CSRF emitido e validado em forms tradicionais; em SPA, cookie `SameSite=Strict` ou validação de Origin/Referer; framework com proteção nativa (NextAuth, Django) ativa.

---

## 2. Autorização

### 2.1 [aberto] Checagem de permissão em toda rota

**tu cobra:** se eu trocar o id na URL pelo id de outro usuário, eu vejo os dados dele?
**eu garanto:** toda rota não-pública passa por middleware de autenticação; toda operação sobre recurso checa ownership ou role antes do acesso ao dado; teste automatizado de IDOR em rotas críticas.

### 2.2 [aberto] Princípio do menor privilégio em papéis

**tu cobra:** o usuário comum consegue chegar em endpoint administrativo só trocando URL?
**eu garanto:** papéis declarados explicitamente; default deny (rota nova nasce inacessível até ser autorizada); admin checado por role, não por flag booleana solta.

### 2.3 [aberto] Separação entre papel de aplicação e papel de banco

**tu cobra:** se o app for comprometido, o atacante tem permissão pra dropar tabela?
**eu garanto:** credencial do app no banco tem só DML necessário (SELECT/INSERT/UPDATE/DELETE em tabelas próprias); DDL e operações administrativas usam credencial separada, fora do runtime.

### 2.4 [aberto] Ausência de IDOR em listagens

**tu cobra:** se eu chamar a API de "minhas faturas" passando user_id de outro, retorna as dele?
**eu garanto:** queries de listagem sempre filtram por user_id da sessão (não do parâmetro); parâmetro de entrada não dita o filtro principal; revisão manual em endpoints que retornam listas.

### 2.5 [aberto] RLS habilitado quando o banco suporta

**tu cobra:** se eu esquecer um filtro `where user_id = ?` numa query, o banco te salva?
**eu garanto:** Row Level Security ativa em toda tabela com dado de usuário (Supabase/Postgres); política por operação (SELECT/INSERT/UPDATE/DELETE); uso de service role documentado em `docs/decisoes.md` com checagem dupla no backend.

### 2.6 [aberto] Operações destrutivas com confirmação e auditoria

**tu cobra:** quem apagou a conta do cliente X mês passado?
**eu garanto:** delete/disable de recurso crítico gera registro em tabela de auditoria (quem, quando, o quê, IP); operação requer confirmação explícita na UI; soft delete preferido pra recursos recuperáveis.

---

## 3. Entrada hostil

### 3.1 [aberto] Validação no servidor sempre

**tu cobra:** se eu desligar o JS do navegador e mandar payload direto, o servidor aceita lixo?
**eu garanto:** toda entrada externa passa por schema (zod, pydantic, equivalente) no servidor; validação no cliente é UX, não segurança; payload inválido retorna 400 sem stack trace.

### 3.2 [aberto] SQL parametrizado em todo query

**tu cobra:** se um campo de busca tiver `'; DROP TABLE users; --`, o que acontece?
**eu garanto:** nenhum query usa concatenação de string com input; ORM ou prepared statement em 100% dos casos; grep no repo por `f"SELECT` ou `+ user_input` zera.

### 3.3 [aberto] Escape contextual em renderização

**tu cobra:** se o usuário colocar `<script>alert(1)</script>` no nome, ele aparece executando pra outros usuários?
**eu garanto:** framework de view com escape padrão ligado (React, Jinja autoescape, etc); `innerHTML`/`dangerouslySetInnerHTML` só com input sanitizado por allowlist; CSP bloqueia inline script.

### 3.4 [aberto] Escape em comandos shell

**tu cobra:** se um campo virar parâmetro de comando shell, o usuário consegue rodar comando arbitrário no servidor?
**eu garanto:** nunca passo input direto pra shell; uso array de argumentos (`subprocess.run([...])`) sem `shell=True`; quando shell é inevitável, escape via `shlex.quote` ou equivalente.

### 3.5 [aberto] Limite de tamanho de payload

**tu cobra:** se eu mandar um upload de 10GB, o servidor cai?
**eu garanto:** limite de body request configurado no edge (nginx, framework); upload de arquivo com limite explícito e tipo MIME validado; multipart com cap por campo.

### 3.6 [aberto] Deserialização segura

**tu cobra:** se o app recebe um pickle/yaml/xml de fora, dá pra executar código?
**eu garanto:** nunca uso `pickle.load` em dado externo; YAML com `safe_load`; XML com parser sem DTD/entidade externa; JSON é o default pra interchange.

### 3.7 [aberto] Path traversal em acesso a arquivo

**tu cobra:** se um nome de arquivo vier do usuário e tiver `../../etc/passwd`, o app serve?
**eu garanto:** nome de arquivo do usuário passa por `basename`; path final validado contra diretório base (resolved path tem que começar com base); allowlist de extensões em upload.

### 3.8 [aberto] Upload com whitelist de tipo real

**tu cobra:** se o usuário renomear `virus.exe` pra `foto.jpg`, o app aceita?
**eu garanto:** extensão validada por allowlist; magic bytes do arquivo conferidos contra MIME declarado; arquivo armazenado fora do webroot ou servido com `Content-Disposition: attachment`.

---

## 4. Segredos

### 4.1 [aberto] `.env` no `.gitignore` antes do primeiro commit

**tu cobra:** se eu rodar `git log -p | grep -i password`, aparece coisa?
**eu garanto:** `.env`, `.env.*` (exceto `.env.example`), `*.pem`, `*.key` no `.gitignore` desde o init; `.env.example` versionado com chaves vazias; varredura com `gitleaks` ou `trufflehog` no fim de cada feature significativa.

### 4.2 [aberto] Sem credencial em código

**tu cobra:** se eu grep no repo por `sk-`, `AKIA`, `Bearer `, aparece chave hardcoded?
**eu garanto:** todas as credenciais vêm de env var ou secret manager; constantes de teste claramente fakes; revisão antes de push.

### 4.3 [aberto] Sem credencial em log

**tu cobra:** se vazar o arquivo de log, dá pra logar como qualquer usuário?
**eu garanto:** logger nunca recebe objeto request bruto; headers de `Authorization` e cookies filtrados; payload de login não logado; estrutura de log tem allowlist de campos.

### 4.4 [aberto] Rotação documentada

**tu cobra:** se essa credencial vazar hoje, em quanto tempo eu giro?
**eu garanto:** `docs/runbooks/rotacao.md` lista cada credencial, onde ela está armazenada, e o procedimento de rotação; rotação periódica agendada pra credenciais long-lived.

### 4.5 [aberto] Secret manager em produção quando há mais de 1 ambiente

**tu cobra:** dev e prod compartilham a mesma senha de banco?
**eu garanto:** ambientes têm credenciais distintas; prod usa secret manager (Vault, AWS SM, Doppler, ou env injetada via systemd) — não `.env` no disco editável; rotação não exige redeploy de código.

---

## 5. Transporte

### 5.1 [aberto] TLS em todo endpoint exposto

**tu cobra:** se eu sniffar a rede num café, vejo as requests da aplicação?
**eu garanto:** HTTPS em 100% dos endpoints externos; certificado válido (Let's Encrypt ou equivalente); TLS 1.2+ com ciphers modernos; TLS 1.0/1.1 desligados.

### 5.2 [aberto] HSTS ativado

**tu cobra:** se o atacante forçar a vítima a clicar num link http://, o navegador downgrade?
**eu garanto:** header `Strict-Transport-Security: max-age=31536000; includeSubDomains`; preload list considerado em domínio público; redirect HTTP→HTTPS no edge antes da app.

### 5.3 [aberto] Renovação automática de certificado

**tu cobra:** o que acontece quando esse cert vencer em 80 dias?
**eu garanto:** certbot/cron configurado pra renovar 30 dias antes do vencimento; alerta se renovação falhar; runbook de fallback documentado.

### 5.4 [aberto] Comunicação interna entre serviços protegida

**tu cobra:** se alguém ganhar acesso à rede interna, ele lê tráfego entre serviços?
**eu garanto:** serviços em rede privada ou comunicação via TLS mútuo; portas internas não expostas no firewall; banco não acessível de fora.

---

## 6. Cabeçalhos de segurança

### 6.1 [aberto] CSP restritiva

**tu cobra:** se um XSS passar pelo escape, o script consegue carregar payload externo?
**eu garanto:** `Content-Security-Policy` com `default-src 'self'`; sem `'unsafe-inline'` em script-src (usa nonce ou hash); `connect-src` restrito a domínios conhecidos; relatório de violação coletado.

### 6.2 [aberto] X-Content-Type-Options: nosniff

**tu cobra:** se eu fizer upload de arquivo .txt com conteúdo HTML, o navegador executa?
**eu garanto:** header `X-Content-Type-Options: nosniff` em todas as respostas; `Content-Type` correto setado pelo servidor.

### 6.3 [aberto] Proteção contra clickjacking

**tu cobra:** o app pode ser embedado num iframe de site malicioso pra capturar cliques?
**eu garanto:** `frame-ancestors 'none'` (ou `'self'`) no CSP; fallback `X-Frame-Options: DENY` pra navegadores antigos.

### 6.4 [aberto] Referrer-Policy restritivo

**tu cobra:** quando o usuário clica num link de saída, o URL completo (com tokens) vaza pro outro site?
**eu garanto:** `Referrer-Policy: strict-origin-when-cross-origin` ou mais restrito; tokens nunca em query string mesmo assim.

### 6.5 [aberto] Permissions-Policy declarado

**tu cobra:** o app tem permissão pra ligar microfone/câmera/geolocation que não usa?
**eu garanto:** `Permissions-Policy` desliga explicitamente APIs não usadas (camera, microphone, geolocation, payment); reativa só em rotas que precisam.

---

## 7. Dependências

### 7.1 [aberto] Audit no CI ou a cada feature

**tu cobra:** quando foi a última vez que olhei se tem CVE nas deps?
**eu garanto:** `npm audit` / `pip-audit` / `cargo audit` roda no CI em PR e em main; severity ≥ high quebra build; ao fim de feature significativa, audit roda local.

### 7.2 [aberto] Lockfile commitado

**tu cobra:** se eu rodar o build em outra máquina hoje, dá o mesmo binário?
**eu garanto:** `package-lock.json` / `poetry.lock` / `Cargo.lock` versionado; CI usa instalação determinística (`npm ci`, `poetry install --no-update`).

### 7.3 [aberto] Versão pinada em deps críticas

**tu cobra:** se uma dep crítica fizer release ruim amanhã, o app pega sozinho?
**eu garanto:** deps de runtime com versão pinada (sem `^` aberto em libs sensíveis); upgrade é decisão consciente, não automática; Dependabot/Renovate com revisão manual.

### 7.4 [aberto] Versão de runtime fixada

**tu cobra:** se a VPS atualizar Node/Python amanhã, o app continua rodando igual?
**eu garanto:** `.nvmrc` / `.python-version` / equivalente versionado; CI roda na mesma versão major.minor; upgrade de runtime é PR isolado com teste.

### 7.5 [aberto] Alerta de CVE em deps usadas

**tu cobra:** se sair uma CVE crítica numa dep nossa amanhã, em quanto tempo eu fico sabendo?
**eu garanto:** GitHub Security Advisories ativo no repo; e-mail/webhook chega no operador; revisão manual mesmo sem alerta novo.

---

## 8. Logs

### 8.1 [aberto] Sem dado sensível em log

**tu cobra:** se eu abrir o arquivo de log agora, vejo CPF, senha, token, ou e-mail completo?
**eu garanto:** logger com filtro de campos sensíveis; PII parcialmente mascarado (`***@dominio.com`); auditoria periódica de amostra de logs.

### 8.2 [aberto] Nível adequado por evento

**tu cobra:** o log de erro está afogado em DEBUG ou eu consigo achar a falha rápido?
**eu garanto:** uso INFO pra fluxo normal, WARNING pra anomalia recuperável, ERROR pra falha que exige atenção, CRITICAL pra incidente; DEBUG só em dev ou com flag.

### 8.3 [aberto] Formato estruturado (JSON)

**tu cobra:** se eu precisar buscar todos os logins de IP X na última semana, é fácil ou caça-fantasma?
**eu garanto:** logs em JSON com `timestamp`, `level`, `event`, `user_id`, `ip`, `correlation_id`; consumível por ferramenta de busca (Loki, ELK, grep+jq).

### 8.4 [aberto] Rotação ativa e acesso restrito

**tu cobra:** o disco do servidor enche de log e mata o serviço? E qualquer usuário do servidor pode ler os logs?
**eu garanto:** logrotate (ou journald) com política de tamanho e retenção; compressão de logs antigos; alerta de disco > 80%; arquivos com `chmod 640` ou mais restrito; export via ferramenta autenticada, não filesystem aberto.

### 8.5 [aberto] Auditoria de evento sensível

**tu cobra:** se um admin mudar o papel de um usuário, fica registrado quem fez e quando?
**eu garanto:** tabela de auditoria pra login admin, mudança de papel, delete de recurso, exportação de dados; registro imutável (append-only); retenção definida.

---

## 9. Rate limiting

### 9.1 [aberto] Limite por IP em endpoints sensíveis

**tu cobra:** se eu jogar 10k req/s no /login, o app aguenta ou cai junto?
**eu garanto:** rate limit no edge (nginx, Cloudflare) ou no app pra `/login`, `/signup`, `/password-reset`, `/api/*`; resposta 429 com `Retry-After`; janela de tempo configurável.

### 9.2 [aberto] Limite por usuário em operações caras

**tu cobra:** um usuário pode disparar 1000 chamadas a LLM e me dar prejuízo de R$ X num dia?
**eu garanto:** quota por usuário em endpoint que consome recurso externo (LLM, e-mail, SMS); contador em Redis ou banco; corte gracioso com mensagem clara.

### 9.3 [aberto] Alerta quando limite é atingido

**tu cobra:** se um ataque distribuído ultrapassar o limite por IP somando, eu fico sabendo?
**eu garanto:** métrica de 429/s monitorada; alerta no Telegram/e-mail quando excede baseline; runbook de resposta a abuso.

### 9.4 [aberto] Proteção contra signup em massa

**tu cobra:** alguém pode criar 10k contas fakes pra abusar do free tier?
**eu garanto:** signup com captcha (hCaptcha/Turnstile) ou prova de trabalho leve; validação de e-mail antes de liberar features pagas; rate limit por IP no signup.

---

## 10. Backup e restore

### 10.1 [aberto] Backup automatizado em produção

**tu cobra:** se o banco morrer agora, quando foi o último snapshot?
**eu garanto:** dump diário automatizado (`pg_dump`, `mysqldump`, ou snapshot do provedor); job monitorado, falha gera alerta; backup armazenado fora do mesmo host.

### 10.2 [aberto] Teste de restore documentado e executado

**tu cobra:** quando foi a última vez que você restaurou o backup pra ver se funciona?
**eu garanto:** runbook `docs/runbooks/restore.md` com passos verificados; teste de restore feito a cada N meses em ambiente isolado; tempo de restore (RTO) medido e registrado.

### 10.3 [aberto] Retenção definida

**tu cobra:** se eu precisar do estado de 3 meses atrás, ainda tenho?
**eu garanto:** política de retenção declarada (ex: 7 diários, 4 semanais, 12 mensais); rotação automática; backups antigos descartados de forma controlada (não acumulam pra sempre).

### 10.4 [aberto] Backup criptografado em repouso

**tu cobra:** se vazar o bucket de backup, o atacante tem o banco em texto plano?
**eu garanto:** backup criptografado antes de sair do host (`gpg`, `age`, ou criptografia do provedor); chave de descriptografia armazenada separada do backup; recuperação testada com a chave real.

### 10.5 [aberto] Backup de configuração e segredos

**tu cobra:** se a VPS sumir hoje, eu reconstrói o ambiente todo amanhã?
**eu garanto:** configs de systemd, nginx, cron, scripts de bootstrap versionados em repo separado ou em `infra/`; lista de credenciais necessárias documentada (sem os valores); procedimento de bootstrap testado.
