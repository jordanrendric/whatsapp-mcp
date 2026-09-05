# Onboarding / Configuração inicial

[English](#english) · [Português](#português)

## Português

Depois de instalar o plugin, abra uma nova tarefa no Codex e diga: **“Configure o whatsapp-mcp neste Mac, incluindo áudio local.”**

1. O launcher procura `uv` instalado. Se faltar, baixa uma versão fixa oficial para a pasta privada do próprio plugin e verifica SHA-256 antes de executá-la. O uv prepara um Python compatível e as dependências fixadas no lockfile.
2. `whatsapp_onboarding` verifica caminhos, contas e componentes. Não instala nada e não retorna mensagens.
3. `whatsapp_setup()` detecta uma conta, valida o schema e salva os caminhos. Havendo várias contas, o usuário escolhe um índice da lista (começa em 1). O plugin não escolhe a conta arbitrariamente.
4. `whatsapp_setup(enable_audio=True)` também prepara o áudio. Reutiliza os binários/modelos disponíveis. Se faltar `ffmpeg` ou `whisper-cli`, usa o Homebrew já instalado. Se faltar modelo, baixa o base multilíngue de uma revisão fixa do repositório oficial do whisper.cpp no Hugging Face, com tamanho e SHA-256 verificados.

A configuração fica em `~/Library/Application Support/whatsapp-mcp/config.json`, fora do código, com permissões privadas. Funciona com o Codex aberto pelo Dock. Variáveis `WHATSAPP_MCP_*` têm precedência para uso avançado. Não há dependência de plugins de vídeo ou áudio: modelos encontrados neles são apenas uma opção de reutilização.

O modelo base tem aproximadamente 148 MB. O tamanho dos binários e suas dependências varia. O setup pode usar rede e levar alguns minutos. Conversas e áudios não são enviados para baixar componentes. A transcrição usa o modelo local depois da preparação.

Se o Homebrew não estiver instalado e forem necessários binários de áudio, o fluxo informa o passo pendente. O plugin não instala Homebrew, não usa sudo, não autentica contas, não muda permissões do macOS e não modifica o banco do WhatsApp. Texto pode ser usado enquanto o áudio está pendente.

Pelo terminal, a partir da pasta deste plugin:

```sh
sh scripts/run.sh --onboarding
sh scripts/run.sh --setup --audio
# Somente se houver mais de uma conta, use o índice mostrado:
sh scripts/run.sh --setup --database-index 1
```

Saídas de diagnóstico contêm caminhos privados; remova-os antes de compartilhar. Erros preservam dados do WhatsApp. Arquivos temporários de download incompleto são descartados; uma nova execução pode tentar novamente. Uma instalação de pacote interrompida pode deixar caches administrados pelo Homebrew.

O download do modelo verifica cancelamento e orçamento de 600 segundos entre leituras, com timeout de socket de 10 segundos. Resolução de DNS e operações internas da rede podem atrasar a resposta ao cancelamento; não há promessa de encerramento instantâneo. Pastas de configuração com ancestrais simbólicos são recusadas para evitar gravar em outro local.

## English

After installing, start a new Codex task and ask: **“Set up whatsapp-mcp on this Mac, including local audio.”**

The launcher reuses an installed uv or bootstraps its own pinned, SHA-256-verified official binary. uv prepares compatible Python and locked packages. `whatsapp_onboarding` diagnoses available accounts and dependencies without installing or returning messages. `whatsapp_setup()` detects a single account, checks the schema and saves its paths; multiple accounts require a user-selected, 1-based index.

With `enable_audio=True`, setup reuses installed ffmpeg, whisper-cli and models. Missing binaries are installed through existing Homebrew. If no model is available, setup downloads the multilingual base model (about 148 MB) from a fixed revision of the official whisper.cpp model repository on Hugging Face and verifies its size and SHA-256. No other plugin is required. Setup can access the network; it does not upload conversations or audio.

Configuration is stored privately at `~/Library/Application Support/whatsapp-mcp/config.json`, outside the source tree, and works for Codex launched from the Dock. Advanced `WHATSAPP_MCP_*` environment variables take precedence. Model and bootstrap binaries also belong to this plugin's per-user application directory. Removing the plugin does not automatically remove these files.

If audio needs Homebrew and it is absent, the tool reports the next step. It never installs Homebrew, uses sudo, logs into WhatsApp, changes macOS permissions or writes to the WhatsApp database. Text reading remains independent of audio setup. Runtime setup and optional audio preparation may take several minutes; account choice and macOS permission prompts cannot be automated safely.

Use `sh scripts/run.sh --onboarding` to inspect and `sh scripts/run.sh --setup --audio` to configure from this plugin directory. Diagnostics include private local paths; redact them before sharing. Interrupted downloads are cleaned up; package-manager caches may remain after a cancelled installation.

Model downloads check cancellation and a 600-second budget between reads, with a 10-second socket timeout. DNS resolution and network internals may delay cancellation; this is not an instantaneous-stop guarantee. Configuration paths with symbolic-link ancestors are rejected to prevent writes outside the intended directory.
