import asyncio
import re

_JAIL_LIST_RE = re.compile(r"Jail list:\s*(.*)")
_CURRENTLY_BANNED_RE = re.compile(r"Currently banned:\s*(\d+)")
_TOTAL_BANNED_RE = re.compile(r"Total banned:\s*(\d+)")
_BANNED_IP_LIST_RE = re.compile(r"Banned IP list:\s*(.*)")
_CURRENTLY_FAILED_RE = re.compile(r"Currently failed:\s*(\d+)")
_LINES_SUMMARY_RE = re.compile(r"Lines:\s*\d+\s*lines,\s*\d+\s*ignored,\s*(\d+)\s*matched,\s*\d+\s*missed")


async def _run(binario: str, *args: str) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        binario, *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode, stdout.decode(errors="replace"), stderr.decode(errors="replace")


def _extract_int(pattern: re.Pattern, texto: str) -> int:
    m = pattern.search(texto)
    return int(m.group(1)) if m else 0


async def status_all() -> list[dict]:
    _, out, _ = await _run("fail2ban-client", "status")
    m = _JAIL_LIST_RE.search(out)
    nomes = [n.strip() for n in m.group(1).split(",") if n.strip()] if m else []

    jails = []
    for nome in nomes:
        _, jail_out, _ = await _run("fail2ban-client", "status", nome)
        ip_match = _BANNED_IP_LIST_RE.search(jail_out)
        banned_ips = ip_match.group(1).split() if ip_match and ip_match.group(1).strip() else []
        jails.append({
            "nome": nome,
            "managed": nome.startswith("vps-monitor-"),
            "currently_banned": _extract_int(_CURRENTLY_BANNED_RE, jail_out),
            "total_banned": _extract_int(_TOTAL_BANNED_RE, jail_out),
            "currently_failed": _extract_int(_CURRENTLY_FAILED_RE, jail_out),
            "banned_ips": banned_ips,
        })
    return jails


async def dry_run_regex(sample_line: str, filter_path: str) -> tuple[bool, str]:
    _, out, _ = await _run("fail2ban-regex", sample_line, filter_path)
    m = _LINES_SUMMARY_RE.search(out)
    matched = int(m.group(1)) > 0 if m else False
    return matched, out


def _raise_if_failed(code: int, out: str, err: str) -> None:
    if code != 0:
        raise RuntimeError((err or out).strip())


async def reload_jail(nome: str) -> None:
    code, out, err = await _run("fail2ban-client", "reload", nome)
    _raise_if_failed(code, out, err)


async def reload_all() -> None:
    code, out, err = await _run("fail2ban-client", "reload")
    _raise_if_failed(code, out, err)


async def stop_jail(nome: str) -> None:
    code, out, err = await _run("fail2ban-client", "stop", nome)
    _raise_if_failed(code, out, err)


async def unban_ip(nome: str, ip: str) -> None:
    code, out, err = await _run("fail2ban-client", "set", nome, "unbanip", ip)
    _raise_if_failed(code, out, err)
