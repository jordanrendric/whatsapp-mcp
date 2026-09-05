# Instalar pelo Codex / Install from Codex

[Português](#português) · [English](#english)

## Português

Você precisa do aplicativo Codex com suporte a plugins e do WhatsApp nativo conectado neste Mac. A instalação pode ser feita pela interface, sem abrir o Terminal ou copiar arquivos do projeto.

### 1. Adicione a origem

Abra **Plugins**, clique em **Add** e escolha **Add a marketplace**. No campo **Source**, cole:

```text
jordanrendric/whatsapp-mcp
```

Deixe os campos opcionais **Git ref** e **Sparse paths** vazios. Clique em **Add marketplace**. O Codex obtém a cópia necessária automaticamente.

### 2. Instale o plugin

Na lista de plugins do marketplace adicionado, abra **WhatsApp MCP** e clique em **Install** ou no botão **+** de instalação. Confirme os avisos de instalação apresentados pelo próprio Codex.

### 3. Prepare este Mac

Abra uma **nova tarefa** e escolha a sugestão **“Configure o WhatsApp MCP neste Mac, incluindo áudio local.”** Se a sugestão não aparecer, escreva essa mesma frase.

O agente detecta a conta e salva a configuração. O ambiente Python é preparado automaticamente. Se você pediu áudio, reutiliza o que já existe e prepara componentes faltantes conforme o [onboarding](../plugins/whatsapp-mcp/docs/onboarding.md). Se preferir começar com texto, diga **“Configure o WhatsApp MCP apenas para ler mensagens.”**

### Quando algo precisa de você

| Situação | Próximo passo |
| --- | --- |
| O repositório está privado | A pessoa precisa ter acesso no GitHub, e o Codex precisa conseguir autenticar o acesso ao repositório. Login no navegador pode não ser suficiente |
| O menu de adicionar marketplace não aparece | Atualize o Codex ou consulte as regras do workspace; você também pode pedir a instalação ao agente pela conversa |
| Há mais de uma conta do WhatsApp | Escolha a conta apresentada pelo agente |
| O macOS bloqueia os arquivos | Revise a permissão do aplicativo que executa o plugin, conforme o erro retornado |
| Faltam ferramentas de áudio e Homebrew | O agente informa o passo necessário; a leitura de texto continua independente |

O plugin permanece somente de leitura para o WhatsApp. A instalação não o publica no catálogo público, não muda a visibilidade do GitHub e não conecta uma conta automaticamente.

## English

Use a recent Codex app with plugin support and the native WhatsApp app signed in on this Mac.

1. Open **Plugins → Add → Add a marketplace**. Paste `jordanrendric/whatsapp-mcp` into **Source**, leave **Git ref** and **Sparse paths** empty, then select **Add marketplace**.
2. Open **WhatsApp MCP** in the added marketplace and select **Install**, or its **+** button. Review the app's installation prompts.
3. Start a **new task** and ask **“Set up WhatsApp MCP on this Mac, including local audio.”** The agent detects the account and saves local configuration. Ask for text-only setup if you prefer to prepare audio later.

Codex obtains the plugin automatically; there is no manual clone or terminal step. While the repository is private, both repository access and authentication from the Codex host are required. Being signed in to GitHub in a browser may not be sufficient. Menu availability can vary by app version and workspace policy. If the menu is unavailable, update the app, check with an administrator, or ask Codex to install the plugin from that GitHub marketplace.

Account selection, macOS file-access permission and missing Homebrew for audio can still require user action. See [onboarding](../plugins/whatsapp-mcp/docs/onboarding.md). The plugin stays read-only toward WhatsApp, and installing it does not publish it to a public directory or change repository visibility.

## Verification / Verificação

The menu names, source field and `marketplace/add` path were checked in the installed app's distributed code on 2026-09-04. The Codex CLI help also confirms direct GitHub repository sources. This was not an end-to-end UI installation test. General installation behavior is documented in the [official plugin guide](https://learn.chatgpt.com/docs/plugins).

Local `codex://` links containing an absolute `marketplacePath` are specific to that machine. They are not portable installation buttons for a GitHub README. Use the repository identifier above for another Mac.
