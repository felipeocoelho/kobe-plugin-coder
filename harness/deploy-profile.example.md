# Perfil de deploy do operador (camada D) — TEMPLATE

> Copie este arquivo para `$KOBE_HOME/user-data/coder/deploy-profile.md` e
> preencha com a SUA topologia de deploy. Esse destino é **gitignored** — nunca
> sobe pro repositório público do plugin. Este `.example.md` (sem dados reais)
> é o único que fica versionado.
>
> **Por que isto existe (camada D):** o harness do Coder (`harness/CONTRACT.md`,
> público) fixa só os **invariantes** de deploy (§9): testa-se antes de publicar;
> o passo que toca usuário público exige OK; deploy é git, nunca rsync. A
> **topologia concreta** — quantos ambientes você tem, os caminhos, os estágios
> e o que fazer entre eles — **varia de operador** e é dado pessoal. Por isso
> mora aqui, fora do que é público, e é injetada no prompt da sessão remota como
> "Camada de usuário do Coder (D)".

---

## Ambientes

> Descreva cada ambiente do seu fluxo, com o caminho concreto. Exemplo de um
> modelo de 4 ambientes (ajuste ao seu — você pode ter 2, 3 ou homologação real
> separada):

- **<ambiente de desenvolvimento>** — onde você codifica e testa primeiro.
  Caminho: `/caminho/para/dev/<projeto>`.
- **<repositório de trabalho>** — espelho versionado do dev (ex.: repo Git
  privado). Não é instalado por usuário público.
- **<ambiente de homologação/staging>** — onde você valida como usuário antes de
  liberar pro mundo. Caminho: `/caminho/para/homologacao/<projeto>`.
- **<repositório público>** — a fonte de instalação pra qualquer pessoa. Tocar
  nisso = afetar usuário público.

## Estágios e a ordem do deploy

> A sequência exata, sempre via git (nunca rsync). Exemplo:

1. `<dev>` já está no estado desejado (foi onde se codou e testou).
2. `<dev>` → `<repositório de trabalho>` via `git push`.
3. `<repositório de trabalho>` → `<homologação>` via `git pull` (a homologação
   **puxa a versão**; `.env`/dados locais ficam fora do versionamento e sobrevivem).
4. `<homologação>` → `<repositório público>` via `git push`. **Último passo, o
   único que toca usuário público — EXIGE OK explícito.**

## O que fazer ENTRE os estágios

> Ações específicas suas (migrations, restart de serviço, validação manual,
> smoke test). Exemplo:

- Antes de subir: rodar a suíte de testes no ambiente de desenvolvimento.
- Após puxar na homologação: reiniciar o serviço (`systemctl --user restart <x>`)
  e validar como usuário.
- Migrations de banco, se houver: rodar com o token salvo em disco, nunca à mão.

## Observações de semântica (importantes pro Coder não assumir)

> Onde o seu modelo difere do "padrão". Exemplos reais que variam por operador:

- "Talvez eu não mantenha um repositório de trabalho separado."
- "Talvez eu tenha 3 ambientes reais com homologação de verdade."
- "Na minha máquina não há homologação própria — o ambiente de produção faz as
  vezes de staging."
