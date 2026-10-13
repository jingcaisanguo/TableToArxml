#!/usr/bin/env bash
# 用法（Git Bash）：source ./init_venv.sh
# 在工程根目录创建或复用 Python 3.9.6 的 .venv 并激活；可用 PYTHON_3_9_6 指定解释器路径。

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

_init_venv_find_python() {
    local required="$1" candidate default_python=""

    if [ -n "${PYTHON_3_9_6:-}" ]; then
        if [ "$(_init_venv_version "$PYTHON_3_9_6")" = "$required" ]; then
            python_cmd=("$PYTHON_3_9_6")
            return 0
        fi
        echo "[ERROR] PYTHON_3_9_6 指向的不是 Python $required：$PYTHON_3_9_6" >&2
        return 1
    fi

    if command -v py >/dev/null 2>&1 && [ "$(_init_venv_version py -3.9)" = "$required" ]; then
        python_cmd=(py -3.9)
        return 0
    fi

    if [ -n "${LOCALAPPDATA:-}" ] && command -v cygpath >/dev/null 2>&1; then
        default_python="$(cygpath -u "$LOCALAPPDATA")/Programs/Python/Python39/python.exe"
    fi
    for candidate in python3.9 python python3 "$default_python"; do
        [ -n "$candidate" ] || continue
        command -v "$candidate" >/dev/null 2>&1 || [ -x "$candidate" ] || continue
        if [ "$(_init_venv_version "$candidate")" = "$required" ]; then
            python_cmd=("$candidate")
            return 0
        fi
    done

    echo "[ERROR] 未找到 Python $required，请先安装，或设置 PYTHON_3_9_6=<python.exe 路径> 后重试" >&2
    return 1
}

_init_venv() {
    local required="3.9.6"
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
        if [ "$version" != "$required" ]; then
            echo "[ERROR] 现有 .venv 的 Python 版本为 ${version:-未知}，不是 $required；请确认后删除 $venv_dir 再重试" >&2
            return 1
        fi
        echo "[INFO] 复用已有虚拟环境：$venv_dir"
    else
        _init_venv_find_python "$required" || return 1
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
    unset -f _init_venv _init_venv_find_python _init_venv_version
    return 0
else
    unset -f _init_venv _init_venv_find_python _init_venv_version
    return 1
fi
