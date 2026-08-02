"""统一测试 runner：逐套执行 s1~s5，输出通过/失败/耗时/性能指标。

用法（在插件根目录）：
    python -m tests.run_all
或：
    python tests/run_all.py

安全声明：所有测试仅使用 tempfile 创建的临时 SQLite 库，
绝不触碰生产数据库（data/plugin_data/ 下的 points_system.db）。
"""

import asyncio
import importlib
import sys
import time

# Windows 终端默认 GBK 代码页：交互式终端保持系统编码（中文正常显示），
# 仅当输出被重定向/管道（非 TTY）时统一为 UTF-8，避免乱码。
for stream in (sys.stdout, sys.stderr):
    try:
        if not stream.isatty():
            stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

TIMEOUT = 300  # 单测试超时秒数（防挂死）

SUITES = [
    ("s1_functional", "功能正确性"),
    ("s2_concurrency", "并发/竞态"),
    ("s3_security", "安全测试"),
    ("s4_performance", "性能压测"),
    ("s5_stability", "稳定性/故障注入"),
    ("s6_easter_date_reward", "彩蛋/日期奖励"),
    ("s7_birthday", "生日系统"),
    ("s8_active_reward", "活跃奖励"),
    ("s9_admin_success", "管理指令成功路径"),
    ("s10_handler_layer", "用户侧 Handler/Main 路由"),
    ("s11_services_dao", "服务边界/积分/DAO"),
    ("s12_utils_config", "工具/配置解析"),
    ("s13_rate_limiter", "限流器"),
    ("s14_backup_restore", "备份恢复"),
    ("s15_migration", "schema/配置迁移"),
    ("s16_stress", "压力/浸泡/随机化"),
    ("s17_command_map", "指令图"),
    ("s18_cross_group", "跨群共享"),
]


async def _run_one(fn, name):
    t0 = time.perf_counter()
    try:
        detail = await asyncio.wait_for(fn(), timeout=TIMEOUT)
        return True, detail, time.perf_counter() - t0
    except asyncio.TimeoutError:
        return False, "超时（挂死）", time.perf_counter() - t0
    except Exception as e:
        import traceback

        return (
            False,
            f"{type(e).__name__}: {e}\n{traceback.format_exc()}",
            time.perf_counter() - t0,
        )


def main() -> int:
    total_pass = total_fail = 0
    perf_rows = []

    for module_name, label in SUITES:
        print(f"\n{'=' * 60}\n[套件] {label} ({module_name})\n{'=' * 60}")
        try:
            mod = importlib.import_module(f"tests.{module_name}")
        except Exception as e:
            import traceback

            print(f"  套件导入失败: {e}\n{traceback.format_exc()}")
            total_fail += 1
            continue

        for name, fn in getattr(mod, "TESTS", []):
            ok, detail, elapsed = asyncio.run(_run_one(fn, name))
            status = "PASS" if ok else "FAIL"
            print(f"  [{status}] {name} ({elapsed:.2f}s)")
            if not ok:
                print("        " + str(detail).replace("\n", "\n        "))
                total_fail += 1
            else:
                total_pass += 1
                if str(detail) != "None":
                    print(f"        {detail}")

    # 性能套件独立输出指标
    print(f"\n{'=' * 60}\n[性能指标]\n{'=' * 60}")
    try:
        s4 = importlib.import_module("tests.s4_performance")
        rows = asyncio.run(asyncio.wait_for(s4.main(), timeout=600))
        for name, metrics in rows:
            print(f"[{name}]")
            for k, v in metrics.items():
                print(f"  {k}: {v}")
            perf_rows.append((name, metrics))
    except Exception as e:
        print(f"  性能指标输出失败: {e}")

    print(f"\n{'=' * 60}\n结果汇总: {total_pass} 通过 / {total_fail} 失败\n{'=' * 60}")
    return 1 if total_fail else 0


if __name__ == "__main__":
    sys.path.insert(
        0,
        __import__("os").path.dirname(
            __import__("os").path.dirname(__import__("os").path.abspath(__file__))
        ),
    )
    sys.exit(main())
