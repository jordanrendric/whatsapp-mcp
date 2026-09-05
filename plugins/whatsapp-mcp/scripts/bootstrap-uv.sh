#!/bin/sh
# Install only the pinned official uv binary, into this plugin's private runtime.
set -eu
umask 077

fail() {
  printf 'whatsapp-mcp: %s\n' "$1" >&2
  exit 1
}

[ "$(/usr/bin/uname -s)" = Darwin ] || fail 'o bootstrap automático suporta apenas macOS.'

uv_bin=$(command -v uv 2>/dev/null || true)
case "$uv_bin" in /*) ;; *) uv_bin= ;; esac
if [ -n "$uv_bin" ] && [ -f "$uv_bin" ] && [ -x "$uv_bin" ]; then
  printf '%s\n' "$uv_bin"
  exit 0
fi
for uv_bin in /opt/homebrew/bin/uv /usr/local/bin/uv; do
  if [ -f "$uv_bin" ] && [ -x "$uv_bin" ]; then
    printf '%s\n' "$uv_bin"
    exit 0
  fi
done

case "${HOME:-}" in /*) ;; *) fail 'não foi possível identificar um diretório pessoal absoluto.' ;; esac
[ "$HOME" != / ] || fail 'o diretório pessoal não pode ser a raiz do sistema.'

# Reject redirected ancestors before making any directory or trusting our copy.
ancestor=$HOME
while [ "$ancestor" != / ]; do
  [ ! -L "$ancestor" ] || fail 'o diretório pessoal contém um link simbólico; bootstrap interrompido.'
  ancestor=$(/usr/bin/dirname "$ancestor")
done

owner_uid=$(/usr/bin/id -u)
ensure_directory() {
  [ ! -L "$1" ] || fail 'um diretório do runtime é um link simbólico; bootstrap interrompido.'
  if [ ! -e "$1" ]; then
    /bin/mkdir -m 700 "$1" || fail 'não foi possível criar o diretório privado do runtime.'
  fi
  [ -d "$1" ] || fail 'o caminho do runtime não é um diretório.'
  [ "$(/usr/bin/stat -f '%u' "$1")" = "$owner_uid" ] || fail 'o diretório do runtime pertence a outro usuário.'
}

ensure_directory "$HOME"
ensure_directory "$HOME/Library"
ensure_directory "$HOME/Library/Application Support"
runtime_dir="$HOME/Library/Application Support/whatsapp-mcp"
ensure_directory "$runtime_dir"
/bin/chmod 700 "$runtime_dir"
bin_dir="$runtime_dir/bin"
ensure_directory "$bin_dir"
/bin/chmod 700 "$bin_dir"
uv_bin="$bin_dir/uv"
[ ! -L "$uv_bin" ] || fail 'o binário privado do runtime é um link simbólico; bootstrap interrompido.'
if [ -e "$uv_bin" ]; then
  [ -f "$uv_bin" ] && [ -x "$uv_bin" ] || fail 'o binário privado do runtime não é um executável regular.'
  [ "$(/usr/bin/stat -f '%u' "$uv_bin")" = "$owner_uid" ] || fail 'o binário privado pertence a outro usuário.'
  printf '%s\n' "$uv_bin"
  exit 0
fi

case "$(/usr/bin/uname -m)" in
  arm64)
    archive_name=uv-aarch64-apple-darwin.tar.gz
    archive_member=uv-aarch64-apple-darwin/uv
    archive_sha256=546f7f8a6c70ff13a3a9d2bc958db3427298cebf3e0cb756f9177133b7068843
    ;;
  x86_64)
    archive_name=uv-x86_64-apple-darwin.tar.gz
    archive_member=uv-x86_64-apple-darwin/uv
    archive_sha256=4c9f52262a14da336e4a42ed24992d12d0c956acde87619e4611d321dffa602b
    ;;
  *) fail 'a arquitetura deste Mac não possui um runtime automático compatível.' ;;
esac

stage_dir=$(/usr/bin/mktemp -d "$runtime_dir/.uv-download.XXXXXX") || fail 'não foi possível preparar um diretório temporário privado.'
trap '/bin/rm -rf -- "$stage_dir"' 0
trap 'exit 1' 1 2 15
archive_path="$stage_dir/uv.tar.gz"
download_url="https://github.com/astral-sh/uv/releases/download/0.12.3/$archive_name"
printf 'whatsapp-mcp: preparando uv 0.12.3 para o primeiro uso…\n' >&2
# macOS /bin/sh uses KiB for RLIMIT_FSIZE; this also bounds downloads when an
# older curl cannot enforce --max-filesize on responses without Content-Length.
if ! (
  ulimit -f 65536
  /usr/bin/curl --disable --proto '=https' --proto-redir '=https' --location --fail --silent --show-error \
    --connect-timeout 15 --max-time 180 --max-filesize 67108864 \
    --output "$archive_path" --url "$download_url" >&2
); then
  fail 'não foi possível baixar o runtime; confira a conexão e reinicie o plugin.'
fi
[ -f "$archive_path" ] && [ ! -L "$archive_path" ] || fail 'o download não produziu um arquivo regular.'
[ "$(/usr/bin/stat -f '%z' "$archive_path")" -le 67108864 ] || fail 'o download ultrapassou o limite de tamanho.'
actual_sha256=$(/usr/bin/shasum -a 256 "$archive_path" | /usr/bin/cut -d ' ' -f 1)
[ "$actual_sha256" = "$archive_sha256" ] || fail 'a verificação SHA-256 do runtime falhou; nenhum binário foi instalado.'

# Stream exactly the known binary member; no archive paths are extracted to disk.
/usr/bin/tar -xOf "$archive_path" "$archive_member" > "$stage_dir/uv" || fail 'não foi possível extrair o binário verificado.'
[ -s "$stage_dir/uv" ] || fail 'o arquivo verificado não contém o binário esperado.'
/bin/chmod 700 "$stage_dir/uv"
[ ! -L "$uv_bin" ] || fail 'o destino do runtime mudou; instalação interrompida.'
[ ! -e "$uv_bin" ] || [ -f "$uv_bin" ] || fail 'o destino do runtime não é um arquivo regular.'
/bin/mv -f "$stage_dir/uv" "$uv_bin" || fail 'não foi possível concluir a instalação privada do runtime.'
printf '%s\n' "$uv_bin"
