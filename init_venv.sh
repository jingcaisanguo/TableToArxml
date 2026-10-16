#!/usr/bin/env bash
# 用法（Git Bash）：source ./init_venv.sh
# 创建或复用 Python >=3.9 的 .venv 并激活；新环境优先 Python 3.13，可用 TTA_PYTHON 指定解释器。

if [ -z "${BASH_VERSION:-}" ]; then
    echo "[ERROR] 请在 Git Bash 中执行：source ./init_venv.sh" >&2
    return 1 2>/dev/null || exit 1
fi

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    echo "[ERROR] 请通过 source 执行，否则激活不会保留在当前窗口：source ${BASH_SOURCE[0]}" >&2
    exit 1
fi

_init_venv_version() {
    "$@" -c 'import platform; print(platform.python_version())' 2>/dev/null | tr -d '\r'
}

_init_venv_supported() {
    "$@" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1
}

_init_venv_find_python() {
    local candidate requested="${TTA_PYTHON:-${PYTHON_3_9_6:-}}"

    if [ -n "${PYTHON_3_9_6:-}" ] && [ -z "${TTA_PYTHON:-}" ]; then
        echo "[WARNING] PYTHON_3_9_6 已废弃，请改用 TTA_PYTHON" >&2
    fi
    if [ -n "$requested" ]; then
        if _init_venv_supported "$requested"; then
            python_cmd=("$requested")
            return 0
        fi
        echo "[ERROR] 指定解释器不可用或版本低于 Python 3.9：$requested" >&2
        return 1
    fi
    if command -v py >/dev/null 2>&1; then
        for candidate in -3.13 -3; do
            if _init_venv_supported py "$candidate"; then
                python_cmd=(py "$candidate")
                return 0
            fi
        done
    fi
    for candidate in python3.13 python3 python; do
        command -v "$candidate" >/dev/null 2>&1 || continue
        if _init_venv_supported "$candidate"; then
            python_cmd=("$candidate")
            return 0
        fi
    done
    echo "[ERROR] 未找到 Python >=3.9，请先安装，或设置 TTA_PYTHON=<解释器路径> 后重试" >&2
    return 1
}

_init_venv() {
    local root venv_dir bin_dir venv_python version
    local -a python_cmd=()

    root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" || return 1
    venv_dir="$root/.venv"

    if [ -e "$venv_dir" ]; then
        if [ -x "$venv_dir/Scripts/python.exe" ]; then
            venv_python="$venv_dir/Scripts/python.exe"
        elif [ -x "$venv_dir/bin/python" ]; then
            venv_python="$venv_dir/bin/python"
        else
            echo "[ERROR] $venv_dir 已存在但不是有效的虚拟环境，请确认后删除再重试" >&2
            return 1
        fi
        version="$(_init_venv_version "$venv_python")"
        if ! _init_venv_supported "$venv_python"; then
            echo "[ERROR] 现有 .venv 的 Python 版本为 ${version:-未知}，要求 >=3.9；请确认后重新创建 $venv_dir" >&2
            return 1
        fi
        echo "[INFO] 复用已有虚拟环境：$venv_dir"
    else
        _init_venv_find_python || return 1
        echo "[INFO] 使用 ${python_cmd[*]} 创建虚拟环境：$venv_dir"
        "${python_cmd[@]}" -m venv "$venv_dir" || {
            echo "[ERROR] 创建虚拟环境失败：$venv_dir" >&2
            return 1
        }
    fi

    if [ -f "$venv_dir/Scripts/activate" ]; then
        bin_dir="$venv_dir/Scripts"
    else
        bin_dir="$venv_dir/bin"
    fi

    if declare -F deactivate >/dev/null; then
        deactivate
    fi
    # shellcheck disable=SC1091
    source "$bin_dir/activate" || return 1
    # Python 3.9 在 Windows 生成的 activate 写入 d:\ 形式路径，Git Bash 的 PATH 无法识别。
    VIRTUAL_ENV="$venv_dir"
    PATH="$bin_dir:$_OLD_VIRTUAL_PATH"
    export VIRTUAL_ENV PATH
    hash -r 2>/dev/null

    echo "[INFO] 已激活 Python $(_init_venv_version python)：$VIRTUAL_ENV"
}

if _init_venv; then
    unset -f _init_venv _init_venv_find_python _init_venv_version _init_venv_supported
    return 0
else
    unset -f _init_venv _init_venv_find_python _init_venv_version _init_venv_supported
    return 1
fi
