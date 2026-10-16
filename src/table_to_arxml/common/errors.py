"""两个工具共用的异常类型。"""

from __future__ import annotations


class ConfigError(ValueError):
    """命令行参数或配置文件错误。

    入口按 argparse 惯例报告这类错误：打印用法、退出码为 2，此时尚未创建日志文件。
    运行期间发现的输入问题（文件缺失、表格内容错误等）不属于此类，退出码为 1。
    """
