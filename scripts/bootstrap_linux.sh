#!/usr/bin/env bash
# Sourced by all Linux launchers. No system Python or shell activation required.
# Keep these pins in sync with bootstrap_versions.json (checked in CPU tests).
FV_UV_VERSION=0.9.0
FV_UV_URL=https://github.com/astral-sh/uv/releases/download/0.9.0/uv-x86_64-unknown-linux-gnu.tar.gz
FV_UV_SHA256=4dadaa5ff5009ccd6a0a43f6ccfa32bf36ed2eff18df7011275a9b1d81950e7b
FV_PYTHON_VERSION=3.12.3

fv_note() { printf 'FreeVideo: %s\n' "$*" >&2; }
fv_fail() { fv_note "$*"; return 1; }

fv_python_ok() {
    [[ -n "$1" ]] && command -v -- "$1" >/dev/null 2>&1 &&
        "$1" -I -B -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1
}

fv_find_python() {
    local candidate
    if [[ -n "${FREEVIDEO_PYTHON:-}" ]]; then
        fv_python_ok "$FREEVIDEO_PYTHON" || { fv_fail 'FREEVIDEO_PYTHON must point to a working Python 3.9+ executable.'; return 2; }
        FREEVIDEO_BOOTSTRAP_PYTHON="$FREEVIDEO_PYTHON"
        return 0
    fi
    for candidate in "$fv_root/envs/unified/bin/python" "$fv_root/envs/engine/bin/python" \
        "$FREEVIDEO_BOOTSTRAP_ROOT/python/cpython-$FV_PYTHON_VERSION-linux-x86_64-gnu/bin/python3.12" \
        python3 python3.12 python; do
        if fv_python_ok "$candidate"; then
            FREEVIDEO_BOOTSTRAP_PYTHON="$(command -v -- "$candidate")"
            return 0
        fi
    done
    return 1
}

fv_help() {
    cat <<'EOF'
FreeVideo Linux launcher

  ./setup.sh              Detect missing tools, prepare Python, then review installation
  ./test.sh               Generate and retain videos, timings and memory reports
  ./freevideo optimize    Validate lightweight tuning from the previous test
  ./freevideo diagnose    Export a small local diagnostic ZIP

Setup launcher options:
  --root PATH             Engine/model installation directory
  --plan                  Check only; may prepare private Python if none is available
  --verbose               Show full setup details and per-step logs
  --install-system-deps   Allow missing OS packages without the interactive prompt
  --bootstrap-only        Prepare basic tools/Python only; no CUDA, models or GPU needed
  --network official      Upstream sources only; compare proxy/direct connections
  --model-downloader xet   Require HF Xet for large weights; stop instead of falling back
  --reuse-models PATH      Verify and reuse compatible models in an existing folder
  --video-models LIST      h3 (default), prism (Prism preview) or h3,prism

No Python, pip, virtual environment or CUDA toolkit installation is needed beforehand.
Default installation: this checkout's directory (independent of the current directory).
Private Python bootstrap: <installation>/.freevideo/bootstrap.
Missing OS packages require root/sudo approval. Full setup reviews GPU/RAM/disk needs
and model licenses separately. A working NVIDIA driver 580+ is required for the engine.
Rerun --help after bootstrap for the full command's options.
EOF
}

fv_missing_tools() {
    fv_missing=()
    local name
    for name in "$@"; do
        command -v "$name" >/dev/null 2>&1 || fv_missing+=("$name")
    done
}

fv_system_deps() {
    ((${#fv_missing[@]})) || return 0
    fv_note "Missing basic tools: ${fv_missing[*]}"
    local manager name package reply
    local -a packages=() privilege=() install=() refresh=()
    if command -v apt-get >/dev/null 2>&1; then manager=apt-get
    elif command -v dnf >/dev/null 2>&1; then manager=dnf
    elif command -v yum >/dev/null 2>&1; then manager=yum
    elif command -v zypper >/dev/null 2>&1; then manager=zypper
    else
        fv_fail 'No supported package manager found (apt-get/dnf/yum/zypper). Ask your administrator to install the listed tools, then rerun this command.'
        return 1
    fi
    for name in "${fv_missing[@]}"; do
        package="$name"
        case "$name" in
            g++|make) if [[ "$manager" == apt-get ]]; then package=build-essential; else package=gcc-c++; fi ;;
            sha256sum) package=coreutils ;;
            flock) package=util-linux ;;
        esac
        [[ " ${packages[*]} " == *" $package "* ]] || packages+=("$package")
        if [[ "$name" == make && "$manager" != apt-get ]]; then packages+=(make); fi
    done
    # Minimal OS images may also lack the TLS trust store.
    packages+=(ca-certificates)
    case "$manager" in
        apt-get) refresh=(apt-get -o Acquire::Retries=2 -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30 update)
                 install=(apt-get -o Acquire::Retries=2 -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30 install -y --no-install-recommends "${packages[@]}") ;;
        dnf|yum) install=("$manager" --setopt=timeout=30 --setopt=retries=2 install -y "${packages[@]}") ;;
        zypper) install=(zypper --non-interactive install --no-recommends "${packages[@]}") ;;
    esac
    if (( EUID != 0 )); then privilege=(sudo); fi
    fv_note 'System package step (existing Python and drivers are not replaced):'
    ((${#refresh[@]} == 0)) || { printf '  '; printf '%q ' "${privilege[@]}" "${refresh[@]}"; printf '\n'; } >&2
    { printf '  '; printf '%q ' "${privilege[@]}" "${install[@]}"; printf '\n'; } >&2
    if (( fv_readonly )); then
        fv_fail 'Check only: no system packages installed. Run ./setup.sh to install the missing tools.'
        return 1
    fi
    if (( ! fv_allow_system )); then
        if [[ ! -t 0 ]]; then
            fv_fail 'System packages need approval. Run interactively, or add --install-system-deps to a reviewed unattended setup.'
            return 1
        fi
        printf 'Install these missing system packages? [Y/n] / 回车安装以上基础工具，n 取消： ' >&2
        read -r reply || reply=n
        case "$reply" in ''|y|Y|yes|YES) ;; *) fv_fail 'Cancelled. No system packages installed.'; return 1 ;; esac
    fi
    if (( EUID != 0 )) && ! command -v sudo >/dev/null 2>&1; then
        fv_fail 'sudo is unavailable. Ask your administrator to run the package commands above, then rerun setup as your normal user.'
        return 1
    fi
    mkdir -p -- "$FREEVIDEO_BOOTSTRAP_ROOT"
    fv_note "System package log: $FREEVIDEO_BOOTSTRAP_ROOT/bootstrap.log"
    # Preserve proxy variables through sudo without passing proxy secrets as arguments.
    if ((${#privilege[@]})); then
        privilege+=("--preserve-env=http_proxy,https_proxy,all_proxy,no_proxy,HTTP_PROXY,HTTPS_PROXY,ALL_PROXY,NO_PROXY")
    fi
    if ((${#refresh[@]})); then
        fv_bootstrap_network "${privilege[@]}" "${refresh[@]}" 2>&1 | tee -a "$FREEVIDEO_BOOTSTRAP_ROOT/bootstrap.log" >&2 || return 1
    fi
    fv_bootstrap_network "${privilege[@]}" "${install[@]}" 2>&1 | tee -a "$FREEVIDEO_BOOTSTRAP_ROOT/bootstrap.log" >&2 || return 1
    fv_missing_tools "${fv_missing[@]}"
    ((${#fv_missing[@]} == 0)) || { fv_fail "Tools still missing after package installation: ${fv_missing[*]}"; return 1; }
}

fv_proxy_routes() {
    case "${FREEVIDEO_PROXY_MODE:-auto}" in
        proxy) fv_routes=(inherited); return ;;
        direct) fv_routes=(direct); return ;;
        auto) ;;
        *) fv_fail 'Invalid download connection mode.'; return 1 ;;
    esac
    fv_routes=(inherited)
    if [[ -n "${http_proxy:-}${https_proxy:-}${all_proxy:-}${HTTP_PROXY:-}${HTTPS_PROXY:-}${ALL_PROXY:-}" ]]; then
        fv_routes+=(direct)
    fi
}

fv_with_route() (
    local route="$1"
    shift
    if [[ "$route" == direct ]]; then
        unset http_proxy https_proxy all_proxy no_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY
        export no_proxy='*' NO_PROXY='*'
    fi
    "$@"
)

fv_bootstrap_network() {
    local -a fv_routes=()
    fv_proxy_routes
    local route
    for route in "${fv_routes[@]}"; do
        if fv_with_route "$route" "$@"; then return 0; fi
    done
    return 1
}

fv_download_uv() {
    local url route attempt=0 archive="$FREEVIDEO_BOOTSTRAP_ROOT/uv-$FV_UV_VERSION.tar.gz" part
    local -a sources=("$FV_UV_URL")
    local -a fv_routes=()
    fv_proxy_routes
    if [[ "$fv_network" != official ]]; then
        if [[ -n "${FREEVIDEO_GITHUB_MIRROR:-}" ]]; then
            sources=("${FREEVIDEO_GITHUB_MIRROR%/}/${FV_UV_URL#https://github.com/}" "${sources[@]}")
        fi
        sources+=("https://ghfast.top/$FV_UV_URL")
    fi
    if [[ -f "$archive" ]] && printf '%s  %s\n' "$FV_UV_SHA256" "$archive" | sha256sum --check --status; then
        fv_note 'Reusing verified bootstrap archive.'
    else
        if [[ -e "$archive" ]]; then mv -- "$archive" "$archive.rejected.$(date +%s).$$"; fi
        for url in "${sources[@]}"; do
          for route in "${fv_routes[@]}"; do
            ((attempt+=1))
            fv_note "Downloading bootstrap tool, attempt $attempt ($route connection)…"
            # Do not put credentials from a custom URL in argv or progress output.
            [[ "$url" != *$'\n'* && "$url" != *$'\r'* && "$url" != *'"'* && "$url" != *"\\"* ]] || { fv_fail 'Invalid mirror URL.'; return 1; }
            part="$archive.partial.$(date +%s).$$.$attempt"
            if printf 'url = "%s"\n' "$url" | fv_with_route "$route" curl --disable --config - --fail --location --silent --show-error \
                --connect-timeout 5 --max-time 120 --speed-limit 1024 --speed-time 20 --retry 1 \
                --retry-max-time 130 --output "$part" >>"$FREEVIDEO_BOOTSTRAP_ROOT/bootstrap.log" 2>&1; then
                if printf '%s  %s\n' "$FV_UV_SHA256" "$part" | sha256sum --check --status; then
                    mv -- "$part" "$archive"
                    break 2
                fi
                fv_note 'SHA256 mismatch; rejected download retained. Trying the next source.'
                printf 'uv source %s: SHA256 mismatch\n' "$attempt" >>"$FREEVIDEO_BOOTSTRAP_ROOT/bootstrap.log"
            else
                fv_note 'Transfer failed; partial download and log retained. Trying the next source.'
            fi
          done
        done
        [[ -f "$archive" ]] || { fv_fail "All bootstrap sources failed. Retry the same command; log: $FREEVIDEO_BOOTSTRAP_ROOT/bootstrap.log"; return 1; }
    fi
    mkdir -p -- "$FREEVIDEO_BOOTSTRAP_ROOT/tools"
    tar -xzf "$archive" -C "$FREEVIDEO_BOOTSTRAP_ROOT/tools" --no-same-owner
}

fv_install_python() (
    # The subshell owns this lock: never pass it into setup/testing workers.
    mkdir -p -- "$FREEVIDEO_BOOTSTRAP_ROOT"
    exec 9>"$FREEVIDEO_BOOTSTRAP_ROOT/bootstrap.lock"
    flock -n 9 || { fv_fail 'Another command is preparing Python. Wait for it to finish and retry.'; exit 1; }
    if fv_python_ok "$FREEVIDEO_BOOTSTRAP_ROOT/python/cpython-$FV_PYTHON_VERSION-linux-x86_64-gnu/bin/python3.12"; then exit 0; fi
    fv_note "Preparing private Python $FV_PYTHON_VERSION (allow ~400 MiB including retained bootstrap downloads; no Torch or models yet)."
    fv_note "Files and log: $FREEVIDEO_BOOTSTRAP_ROOT"
    fv_download_uv || exit 1
    local uv="$FREEVIDEO_BOOTSTRAP_ROOT/tools/uv-x86_64-unknown-linux-gnu/uv" source route attempt=0
    local -a fv_routes=()
    fv_proxy_routes
    local -a sources=(https://github.com/astral-sh/python-build-standalone/releases/download)
    if [[ "$fv_network" != official ]]; then
        if [[ -n "${UV_PYTHON_INSTALL_MIRROR:-}" ]]; then sources=("$UV_PYTHON_INSTALL_MIRROR" "${sources[@]}"); fi
        if [[ -n "${FREEVIDEO_GITHUB_MIRROR:-}" ]]; then sources+=("${FREEVIDEO_GITHUB_MIRROR%/}/astral-sh/python-build-standalone/releases/download"); fi
        sources+=(https://ghfast.top/https://github.com/astral-sh/python-build-standalone/releases/download)
    fi
    export UV_PYTHON_INSTALL_DIR="$FREEVIDEO_BOOTSTRAP_ROOT/python" UV_CACHE_DIR="$FREEVIDEO_BOOTSTRAP_ROOT/cache"
    export UV_HTTP_TIMEOUT=20 UV_HTTP_RETRIES=1
    for source in "${sources[@]}"; do
      for route in "${fv_routes[@]}"; do
        ((attempt+=1))
        fv_note "Installing private Python, attempt $attempt ($route connection)…"
        printf 'Python source %s\n' "$attempt" >>"$FREEVIDEO_BOOTSTRAP_ROOT/bootstrap.log"
        if UV_PYTHON_INSTALL_MIRROR="$source" fv_with_route "$route" "$uv" --no-config python install --no-bin --no-registry \
            --no-progress --color never "$FV_PYTHON_VERSION" >>"$FREEVIDEO_BOOTSTRAP_ROOT/bootstrap.log" 2>&1; then
            fv_python_ok "$UV_PYTHON_INSTALL_DIR/cpython-$FV_PYTHON_VERSION-linux-x86_64-gnu/bin/python3.12" && exit 0
        fi
        fv_note 'Python preparation failed; retrying the next source. Existing files retained.'
      done
    done
    fv_fail "Could not prepare Python. Check $FREEVIDEO_BOOTSTRAP_ROOT/bootstrap.log and retry the same command."
    exit 1
)

freevideo_bootstrap_linux() {
    local mode="$1" arg command_name='' help=0 bootstrap_only=0 found=1
    shift
    local fv_source="${FREEVIDEO_SOURCE_DIR:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)}"
    local fv_root="${FREEVIDEO_HOME:-$fv_source}"
    local fv_readonly=0 fv_allow_system=0 fv_network=auto
    local -a fv_missing=()
    FREEVIDEO_BOOTSTRAP_DONE=0
    FREEVIDEO_BOOTSTRAP_ARGS=()
    while (($#)); do
        arg="$1"; shift
        case "$arg" in
            --root|--config|--network|--model-downloader|--reuse-models|--reuse-models-manifest)
                (($#)) || { fv_fail "$arg requires a value."; return 1; }
                [[ "$arg" != --root ]] || fv_root="$1"
                [[ "$arg" != --network ]] || fv_network="$1"
                FREEVIDEO_BOOTSTRAP_ARGS+=("$arg" "$1"); shift ;;
            --root=*) fv_root="${arg#*=}"; FREEVIDEO_BOOTSTRAP_ARGS+=("$arg") ;;
            --network=*) fv_network="${arg#*=}"; FREEVIDEO_BOOTSTRAP_ARGS+=("$arg") ;;
            --install-system-deps) fv_allow_system=1 ;;
            --bootstrap-only) bootstrap_only=1 ;;
            --plan|--check) fv_readonly=1; FREEVIDEO_BOOTSTRAP_ARGS+=("$arg") ;;
            --help|-h) help=1; FREEVIDEO_BOOTSTRAP_ARGS+=("$arg") ;;
            *) FREEVIDEO_BOOTSTRAP_ARGS+=("$arg")
               if [[ "$mode" == cli && -z "$command_name" && "$arg" != -* ]]; then command_name="$arg"; fi ;;
        esac
    done
    # Resolve the selected installation before choosing Python or its download
    # directory. The same root reaches every Python launcher, even from another cwd.
    # shellcheck disable=SC2088 # Match literal user input, then expand it explicitly.
    case "$fv_root" in '~') fv_root="$HOME" ;; '~/'*) fv_root="$HOME/${fv_root:2}" ;; esac
    export FREEVIDEO_HOME="$fv_root"
    export FREEVIDEO_BOOTSTRAP_ROOT="${FREEVIDEO_BOOTSTRAP_ROOT:-$fv_root/.freevideo/bootstrap}"
    [[ "$mode" != cli || "$command_name" != setup ]] || mode=setup
    [[ "$mode" != cli || -n "$command_name" ]] || help=1
    if (( bootstrap_only || fv_allow_system )) && [[ "$mode" != setup ]]; then
        fv_fail '--bootstrap-only and --install-system-deps are setup options.'; return 1
    fi
    if (( bootstrap_only && fv_readonly )); then fv_fail '--bootstrap-only cannot be combined with --plan.'; return 1; fi
    [[ "$fv_network" == auto || "$fv_network" == official ]] || { fv_fail '--network must be auto or official.'; return 1; }
    fv_find_python && found=0 || found=$?
    (( found != 2 )) || return 1
    if (( help )); then
        if (( found )); then fv_help; FREEVIDEO_BOOTSTRAP_DONE=1
        elif [[ "$mode" == setup ]]; then fv_help; printf '\n'; fi
        return 0
    fi
    [[ "$(uname -s)" == Linux && "$(uname -m)" == x86_64 ]] || { fv_fail 'Use native Windows launchers on Windows. Linux setup currently supports x86_64.'; return 1; }
    if command -v getconf >/dev/null 2>&1 && ! getconf GNU_LIBC_VERSION >/dev/null 2>&1; then
        fv_fail 'Linux setup needs glibc (for example Ubuntu/Debian); musl/Alpine CUDA wheels are unsupported.'; return 1
    fi
    # curl and system package managers differ in uppercase proxy handling.
    [[ -n "${http_proxy:-}" || -z "${HTTP_PROXY:-}" ]] || export http_proxy="$HTTP_PROXY"
    [[ -n "${https_proxy:-}" || -z "${HTTPS_PROXY:-}" ]] || export https_proxy="$HTTPS_PROXY"
    [[ -n "${all_proxy:-}" || -z "${ALL_PROXY:-}" ]] || export all_proxy="$ALL_PROXY"
    [[ -n "${no_proxy:-}" || -z "${NO_PROXY:-}" ]] || export no_proxy="$NO_PROXY"
    if [[ "$mode" == setup ]] && (( ! fv_readonly )); then
        fv_missing_tools git curl g++ make tar gzip sha256sum flock
        fv_system_deps || return 1
    fi
    if (( found )); then
        fv_missing_tools curl tar gzip sha256sum flock
        fv_system_deps || return 1
        (( ! fv_readonly )) || fv_note 'Check only: a private Python bootstrap is needed to display the plan. No engine, model or system package installation.'
        fv_install_python || return 1
        fv_find_python || { fv_fail 'Python installed but could not start. See bootstrap.log.'; return 1; }
    fi
    if (( bootstrap_only )); then
        fv_note "Basic tools ready. Python: $FREEVIDEO_BOOTSTRAP_PYTHON"
        fv_note 'Run ./setup.sh next to review GPU/RAM/disk requirements and install the engine.'
        # shellcheck disable=SC2034 # Output read by the sourcing launchers.
        FREEVIDEO_BOOTSTRAP_DONE=1
    fi
}
