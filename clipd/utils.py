from datetime import datetime


def fmt_time(ts: float) -> str:
    dt = datetime.fromtimestamp(ts)
    diff = datetime.now() - dt
    if diff.days == 0:
        s = diff.seconds
        if s < 60:
            return f"{s}s ago"
        if s < 3600:
            return f"{s // 60}m ago"
        return f"{s // 3600}h ago"
    if diff.days == 1:
        return "yesterday"
    if diff.days < 7:
        return f"{diff.days}d ago"
    return dt.strftime("%Y-%m-%d")


def fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n}B"
    if n < 1024 ** 2:
        return f"{n / 1024:.1f}KB"
    return f"{n / 1024 ** 2:.1f}MB"
