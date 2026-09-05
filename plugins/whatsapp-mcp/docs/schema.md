# Compatibilidade com o armazenamento local

Mapeamento verificado no Mac de desenvolvimento em 04/09/2026. O banco do WhatsApp é um detalhe interno do aplicativo, sem contrato público estável. Não são incluídos dados de conversas no plugin.

## Arquivos

- Banco atual: `~/Library/Group Containers/group.net.whatsapp.WhatsApp.shared/ChatStorage.sqlite`.
- Abertura com URI `mode=ro`, `uri=True`, `query_only=ON`, `trusted_schema=OFF` e authorizer que permite apenas leituras e funções necessárias. Cada consulta mantém uma transação de leitura, com limite de tempo.
- Não usar `immutable=1`: o WAL pode conter mensagens recentes. Não copiar o banco isoladamente nem executar checkpoint/journal_mode.
- `ZMEDIALOCALPATH` é relativo, normalmente `Media/...`. A resolução neste layout é `<container>/Message/<ZMEDIALOCALPATH>`.
- Caminhos absolutos, URLs, `..` e links que escapem da raiz de mídia são recusados. O usuário pode configurar outra raiz explicitamente no ambiente; a ferramenta não aceita caminhos enviados pelo modelo.
- A descoberta também verifica candidatos Business e legado, mas esses layouts não foram validados neste Mac. Havendo múltiplos bancos, exige escolha explícita por `WHATSAPP_MCP_DB_PATH`.

## Tabelas e relações

| Tabela | Campos usados |
| --- | --- |
| `ZWACHATSESSION` | `Z_PK`, `ZCONTACTJID`, `ZPARTNERNAME`, `ZLASTMESSAGEDATE`, `ZSESSIONTYPE`, `ZARCHIVED`, `ZHIDDEN`, `ZREMOVED`, `ZUNREADCOUNT` |
| `ZWAMESSAGE` | `Z_PK`, `ZCHATSESSION`, `ZSORT`, `ZMESSAGEDATE`, `ZISFROMME`, `ZMESSAGETYPE`, `ZTEXT`, `ZFROMJID`, `ZTOJID`, `ZPUSHNAME`, `ZGROUPMEMBER`, `ZMEDIAITEM` |
| `ZWAGROUPMEMBER` | `Z_PK`, `ZMEMBERJID`, `ZCONTACTNAME` |
| `ZWAMEDIAITEM` | `Z_PK`, `ZMEDIALOCALPATH`, `ZFILESIZE`, `ZMOVIEDURATION`, `ZTITLE` |

Mensagem → conversa por `ZCHATSESSION`; mensagem → participante por `ZGROUPMEMBER`; mensagem → mídia por `ZMEDIAITEM`. O schema é conferido antes de consultar. Tabelas ausentes ou substituídas por views, e colunas obrigatórias ausentes, geram erro explícito.

## URL CDN opcional

A inspeção somente do schema deste Mac confirmou `ZWAMEDIAITEM.ZMEDIAURL` (`VARCHAR`) e `ZMEDIAURLDATE` (`TIMESTAMP`). Essas colunas são opcionais no adaptador: sua ausência desativa a consulta de CDN, mantendo as leituras de texto compatíveis. `whatsapp_get_media_url` usa o vínculo da mensagem com a mídia, os mesmos filtros de visibilidade e um snapshot de leitura. Não procura links dentro do texto da mensagem.

Somente essa ferramenta seleciona a URL, limitada a 8.192 bytes e validada como HTTPS em um subdomínio do WhatsApp (`*.whatsapp.net`). Userinfo, porta não padrão, fragmento e caracteres de controle são recusados. O campo de data é retornado como metadado de origem; não é interpretado como prazo de validade. `ZMEDIAKEY` e `ZMETADATA` não são consultados nem devolvidos.

A URL não é testada na rede, renovada ou descriptografada. `available` indica somente uma URL armazenada que passou na validação sintática. Disponibilidade remota, vencimento e criptografia continuam como `unverified`; ausência local não comprova disponibilidade no CDN.

## Autoria e ordenação

`ZISFROMME` identifica mensagem própria. Em grupo, **`ZFROMJID` pode ser o identificador do grupo**, não do remetente. O remetente é lido de `ZWAGROUPMEMBER.ZMEMBERJID` e `ZCONTACTNAME`; sem participante conhecido, o plugin informa a lacuna. Contatos diretos podem usar identificadores `@lid` diferentes do identificador da conversa.

Datas Core Data usam segundos desde 01/01/2001; a conversão para Unix soma `978307200`. Saídas usam UTC. `since` é inclusivo e `until` exclusivo, ambos com fuso explícito.

Na conversa, ordenar por `(ZSORT, Z_PK)`: `ZSORT` pode ser negativo ou repetido, e `Z_PK` não é cronológico. `before_id`/`after_id` são âncoras resolvidas no mesmo chat. Na busca global, ordenar por `(ZMESSAGEDATE, Z_PK)` decrescente. A ausência de chave de ordenação gera erro quando encontrada, sem inventar ordem. A lista de chats usa offset e pode mudar se novas mensagens chegarem entre páginas.

## Tipos

Tipos de sessão `0` são contatos (`@s.whatsapp.net`/`@lid`); `1` e `4` são grupos (`@g.us`). Outros tipos preservam `session_type` e retornam `kind=other`, sem presumir semântica interna. O tipo `4` é tratado como grupo, sem presumir uma função de comunidade. `kind=all` lista os tipos locais visíveis e não removidos.

Tipos de mensagem principais: `0` texto, `1` imagem, `2` vídeo, `3` áudio, `8` documento. Outros valores conservam `message_type`; o plugin não tenta reconstruir o conteúdo de mensagens de sistema. Áudio do tipo `3` e documentos do tipo `8` com extensão de áudio suportada podem ser transcritos, desde que o arquivo esteja disponível localmente.

O limite de texto por mensagem é 20.000 caracteres, indicado por `text_truncated`. Metadados não comprovam que um anexo foi baixado: `local_available` verifica o arquivo. Leitura não dispara download nem muda recibos.
