import random
import zlib

from .security import clean_display_name

_LEVELS = [
    (
        "\u4e0a\u4e0a\u7b7e",
        5,
        "\u4f9d\u7b7e\u5230\uff0c\u4f9d\u62bd\u5956\uff0c\u4eca\u65e5\u9e3f\u8fd0\u5f53\u5934\uff01",
    ),
    (
        "\u4e0a\u7b7e",
        10,
        "\u8fd0\u52bf\u4e0d\u9519\uff0c\u9002\u5408\u5927\u80c6\u4e00\u640f\uff01",
    ),
    (
        "\u4e2d\u5409",
        15,
        "\u5e73\u7a33\u4e2d\u6709\u60ca\u559c\uff0c\u503c\u5f97\u671f\u5f85\u3002",
    ),
    (
        "\u4e2d\u5e73",
        25,
        "\u5e73\u6de1\u662f\u771f\uff0c\u7a33\u624e\u7a33\u6253\u3002",
    ),
    (
        "\u672b\u5409",
        25,
        "\u7a0d\u5b89\u52ff\u8e81\uff0c\u597d\u8fd0\u5728\u8def\u4e0a\u3002",
    ),
    (
        "\u672b\u7b7e",
        15,
        "\u8bf8\u4e8b\u4e0d\u5b9c\uff1f\u7761\u5927\u89c9\u624d\u662f\u6b63\u9053\u3002",
    ),
    (
        "\u5927\u51f6",
        5,
        "\u975e\u8457\u9644\u4f53\uff0c\u5efa\u8bae\u7b7e\u5230\u8f6c\u8fd0\u3002",
    ),
]

_ADVICE = [
    "\u4eca\u65e5\u5b9c\u5927\u80c6\u4e00\u640f\uff0c\u4e0d\u5b9c\u4fdd\u5b88\u3002",
    "\u5b9c\u4e0e\u670b\u53cb\u804a\u5929\uff0c\u4e0d\u5b9c\u72ec\u81ea\u5fe7\u4f24\u3002",
    "\u5b9c\u5403\u996d\uff0c\u4e0d\u5b9c\u8282\u98df\u3002",
    "\u5b9c\u65e9\u7761\uff0c\u4e0d\u5b9c\u718a\u591c\u3002",
    "\u5b9c\u5f00\u5fc3\uff0c\u4e0d\u5b9c\u751f\u6c14\u3002",
    "\u4eca\u65e5\u8fd0\u52bf\u5e73\u5e73\uff0c\u4fdd\u6301\u5e73\u5e38\u5fc3\u5c31\u597d\u3002",
    "\u591a\u559d\u70ed\u6c34\uff0c\u8eab\u4f53\u5065\u5eb7\u624d\u662f\u6700\u5927\u7684\u8fd0\u6c14\u3002",
    "\u5b9c\u8ffd\u5267\uff0c\u4e0d\u5b9c\u52a0\u73ed\u3002",
    "\u4eca\u5929\u662f\u4f60\u7684\u597d\u65e5\u5b50\uff0c\u53bb\u505a\u70b9\u6709\u610f\u601d\u7684\u4e8b\u5427\uff01",
    "\u4e0d\u5b9c\u51b2\u52a8\u6d88\u8d39\uff0c\u7406\u6027\u4e00\u70b9\u66f4\u597d\u3002",
    "\u5b9c\u7b7e\u5230\u8f6c\u8fd0\uff0c\u4e0d\u5b9c\u8003\u8651\u592a\u591a\u3002",
    "\u4eca\u65e5\u8fd0\u52bf\u4e0d\u9519\uff0c\u53ef\u4ee5\u8bd5\u8bd5\u62bd\u5956\u3002",
]


def get_fortune(qq: str, date_str: str) -> dict:
    seed_val = zlib.crc32(f"{qq}_{date_str}".encode())
    rng = random.Random(seed_val)
    level_name, _, _ = rng.choices(_LEVELS, weights=[w for _, w, _ in _LEVELS])[0]
    lucky_num = rng.randint(1, 99)
    advice = rng.choice(_ADVICE)
    return {
        "level": level_name,
        "lucky_number": lucky_num,
        "advice": advice,
    }


def format_fortune(qq: str, date_str: str, user_name: str) -> str:
    f = get_fortune(qq, date_str)
    # 昵称可能含换行/控制字符，剥离后再拼入文案，防止构造多行伪造消息
    clean_name = clean_display_name(user_name)
    return (
        "\u2501" * 20 + "\n"
        f"\U0001f4ae {clean_name} \u7684\u4eca\u65e5\u8fd0\u52bf\n"
        f"\U0001f340 {f['level']}\n"
        f"\U0001f4dd {f['advice']}\n"
        f"\U0001f522 \u5e78\u8fd0\u6570\u5b57: {f['lucky_number']}"
    )
