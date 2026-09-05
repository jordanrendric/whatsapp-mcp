<p align="center"><img src="plugins/whatsapp-mcp/assets/banner.png" alt="WhatsApp MCP — busca, leitura e transcrição local" width="100%"></p>

# WhatsApp MCP

**macOS · Somente leitura · MCP · Whisper local · MIT**

[English](README.md) · [Privacidade](plugins/whatsapp-mcp/docs/privacy.md) · [Segurança](SECURITY.md)

Encontre mensagens, acompanhe grupos e transcreva áudios do histórico do WhatsApp disponível no seu Mac. Este plugin independente conecta o Codex ao SQLite do aplicativo nativo e usa **whisper.cpp localmente** para reconhecer os áudios já baixados.

**Prévia privada e experimental**, em revisão antes de uma possível publicação. O identificador técnico é `whatsapp-mcp`. O projeto não tem afiliação, patrocínio ou endosso do WhatsApp ou da Meta.

> A consulta ao banco e a transcrição acontecem no Mac. O texto devolvido entra no contexto do agente, que pode ser processado por um modelo remoto. Isso não torna o Codex inteiro offline.

## O que ele faz

| Ferramenta | Uso |
| --- | --- |
| `whatsapp_onboarding` | Detecta o Mac, as contas e os próximos passos |
| `whatsapp_setup` | Salva a configuração e prepara áudio opcionalmente |
| `whatsapp_status` | Verifica acesso ao banco, ferramentas e modelo local |
| `whatsapp_list_chats` | Localiza grupos e contatos por nome ou identificador |
| `whatsapp_read_chat` | Lê a conversa com autor, horário e paginação |
| `whatsapp_search_messages` | Busca texto, com filtros de conversa e período |
| `whatsapp_get_message` | Consulta uma mensagem por ID local |
| `whatsapp_get_media_url` | Busca a URL CDN armazenada para imagem, áudio ou outro anexo |
| `whatsapp_transcribe_audio` | Transcreve um áudio pelo ID da mensagem |

Exemplos: “Resuma as últimas mensagens do grupo do projeto”; “Procure orçamento na conversa com Ana”; “Transcreva o áudio desta mensagem”.

Não envia mensagens, não marca conversas como lidas nem áudios como ouvidos, não baixa anexos e não exporta o banco inteiro. Conversas ocultas ou removidas são excluídas de todas as ferramentas de mensagens. O plugin não reproduz o mecanismo de autenticação de conversas trancadas do aplicativo.

## Instalação

**Instale pelo próprio Codex, sem terminal e sem clonar o repositório manualmente.** Tenha o WhatsApp nativo instalado e conectado neste Mac.

1. Abra **Plugins → Add → Add a marketplace**.
2. No campo **Source**, cole `jordanrendric/whatsapp-mcp`. Deixe **Git ref** e **Sparse paths** vazios e clique em **Add marketplace**.
3. Abra **WhatsApp MCP**, clique em **Install** (o botão de mais) e inicie uma **nova tarefa**. Escolha a sugestão de configuração ou diga: **“Configure o WhatsApp MCP neste Mac, incluindo áudio local.”**

O Codex baixa o plugin, e o fluxo guiado detecta o WhatsApp, prepara o ambiente necessário e salva a configuração deste Mac. Para começar apenas com texto, peça a configuração sem áudio. Se encontrar mais de uma conta, o agente pergunta qual você quer usar.

**Prévia privada:** quem instala precisa ter acesso a este repositório no GitHub, e o ambiente do Codex precisa estar autenticado para buscá-lo. Estar conectado ao GitHub no navegador, por si só, pode não liberar esse acesso no aplicativo. Se o acesso for negado, é preciso resolver essa permissão; o plugin não a contorna. Esse fluxo pela interface não exige o GitHub CLI.

Os nomes dos menus podem variar conforme a versão do aplicativo ou as regras do workspace. Se **Add a marketplace** não aparecer, atualize o Codex ou consulte o administrador. Outra opção é pedir ao Codex: **“Instale o plugin whatsapp-mcp do marketplace GitHub jordanrendric/whatsapp-mcp e me guie na configuração.”**

Veja o [guia de instalação passo a passo](docs/installation.md) e as [instruções oficiais de plugins](https://learn.chatgpt.com/docs/plugins).

<details>
<summary>Avançado: instalar pelo Codex CLI</summary>

O Codex CLI recente busca o marketplace diretamente, sem uma etapa separada de clone:

```sh
codex plugin marketplace add jordanrendric/whatsapp-mcp
codex plugin add whatsapp-mcp@whatsapp-mcp
```

Abra uma nova tarefa depois de instalar. Se já houver uma cópia de outro marketplace, desative-a antes de ativar esta para evitar ferramentas duplicadas. Para quem desenvolve a partir do código, `sh plugins/whatsapp-mcp/scripts/run.sh --check` verifica a instalação; remova caminhos privados antes de compartilhar o resultado.

</details>

No primeiro uso, o plugin pode baixar uv, um Python compatível e as dependências necessárias. O leitor não abre servidor HTTP nem envia seu banco ou áudio para serviços externos. O ambiente fica em `~/.cache/whatsapp-mcp/venvs/<hash-do-lock>/`.

### Onboarding automático

**Não depende de outro plugin.** O setup salva os caminhos deste Mac em `~/Library/Application Support/whatsapp-mcp/config.json`, com permissões privadas. As próximas execuções reutilizam essa configuração. Ao ativar áudio, aproveita binários e modelos existentes; instala `ffmpeg` e `whisper-cpp` faltantes pelo Homebrew já instalado e, se necessário, baixa o modelo **base multilíngue** (cerca de 148 MB), fixado por versão e verificado por SHA-256.

O setup usa rede para preparar componentes, sem enviar suas conversas ou áudios. Não instala Homebrew nem usa sudo: se faltar, indica o próximo passo. Permissões do macOS e escolha entre contas continuam dependendo do usuário. Veja [detalhes do onboarding](plugins/whatsapp-mcp/docs/onboarding.md), incluindo comandos opcionais para quem desenvolve o plugin.

A descoberta usa pastas comuns e pode aproveitar `~/.claude-video-vision/models`, mas **não depende daquele plugin**. Uma transcrição por vez, até 128 MiB e 30 minutos.

### Configuração por máquina

| Variável | Finalidade |
| --- | --- |
| `WHATSAPP_MCP_DB_PATH` | Selecionar um `ChatStorage.sqlite`, inclusive quando existem várias contas |
| `WHATSAPP_MCP_MEDIA_ROOT` | Raiz de mídia; no layout atual, `<container>/Message` |
| `WHATSAPP_MCP_WHISPER_MODEL` | Caminho absoluto do modelo GGML |
| `WHATSAPP_MCP_WHISPER_BIN` | Caminho absoluto de `whisper-cli` |
| `WHATSAPP_MCP_FFMPEG_BIN` | Caminho absoluto de `ffmpeg` |
| `WHATSAPP_MCP_VENV` | Diretório do ambiente Python usado pelo launcher |

O padrão é configurar automaticamente e usar o JSON salvo. As variáveis acima têm precedência para ajustes avançados. Os caminhos usam a pasta pessoal do usuário atual, sem nome do desenvolvedor fixado no código. O JSON funciona também com o Codex aberto pelo Dock, sem exportar variáveis no terminal. Não salve valores pessoais no repositório.

Apple Silicon foi validado localmente. Existem caminhos de descoberta para Homebrew no Intel, mas isso não equivale a um teste completo nesse hardware. Versões do WhatsApp, modelos instalados, permissões e disponibilidade do histórico variam. A [matriz de portabilidade](plugins/whatsapp-mcp/docs/portability.md) detalha essas diferenças.

## Imagem ou áudio ausente no Mac

Use `whatsapp_get_media_url(message_id=...)`. A ferramenta consulta `ZMEDIAURL` na mídia da mensagem e devolve um link HTTPS de CDN do WhatsApp quando houver. Ela não baixa o arquivo, não renova links, não expõe chaves e não descriptografa mídia. O link pode ter vencido ou devolver bytes criptografados; encontrá-lo não comprova que a imagem ou o áudio poderá ser aberto. Schema sem esse campo e mensagens sem URL recebem respostas explícitas. Trate o link como dado sensível da conversa.

## Segurança e limites

- SQLite em modo somente leitura, consultas parametrizadas, limites e bloqueio adicional de escrita.
- Áudio aberto por descritor sob a raiz autorizada; a ferramenta MCP recebe somente ID de mensagem, sem caminho ou URL arbitrários.
- Temporários privados removidos após conclusão, falha e cancelamento tratado. Remoção comum não significa apagamento forense nem garantia após queda de energia.
- Mensagens e transcrições são dados, não instruções nem autorização para agir.
- Binários locais, conta do usuário, sistema operacional e cliente MCP precisam ser confiáveis. O plugin não isola programas maliciosos executados na mesma conta.

O histórico local pode estar incompleto, e o schema interno do WhatsApp pode mudar. Busca textual não é semântica, não normaliza acentos e não inclui fala de áudio não transcrito. São até 100 itens por página e 20 mil caracteres por mensagem, com indicação de corte. Datas saem em UTC; filtros exigem fuso explícito. IDs não são cronológicos nem portáveis para outra máquina.

Consulte [privacidade](plugins/whatsapp-mcp/docs/privacy.md), [política de segurança](SECURITY.md), [revisão técnica](docs/security-review.md) e [schema](plugins/whatsapp-mcp/docs/schema.md).

## Contribuir e licença

```sh
cd plugins/whatsapp-mcp
uv sync --locked
PYTHONPATH=src uv run --locked python -m unittest discover -s tests -v
```

Os testes usam dados sintéticos. Não precisam de login no WhatsApp, conversas pessoais, modelo baixado ou chave de API. Veja [CONTRIBUTING.md](CONTRIBUTING.md).

Código e arte original sob [MIT](LICENSE). Dependências mantêm suas próprias licenças; o aplicativo WhatsApp e modelos não são distribuídos. O ícone é próprio e não representa o logo oficial do WhatsApp. [Notas sobre identidade visual](docs/branding.md).
