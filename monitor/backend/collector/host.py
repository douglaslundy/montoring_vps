import os
import time
import shutil
from pathlib import Path

_prev_cpu = None
_prev_net = None
_prev_time = None

PROC_BASE = os.environ.get("PROC_BASE", "/host/proc")
SYS_BASE = os.environ.get("SYS_BASE", "/host/sys")


def _read_cpu_raw(proc_base):
    with open(f"{proc_base}/stat") as f:
        line = f.readline()
    values = [int(x) for x in line.split()[1:]]
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    total = sum(values)
    with open(f"{proc_base}/loadavg") as f:
        parts = f.read().split()
    load = [float(parts[0]), float(parts[1]), float(parts[2])]
    return {"idle": idle, "total": total}, load


def _read_cpu_info(proc_base):
    cores, model = 0, "Unknown"
    with open(f"{proc_base}/cpuinfo") as f:
        for line in f:
            if line.startswith("processor"):
                cores += 1
            elif line.startswith("model name") and model == "Unknown":
                model = line.split(":", 1)[1].strip()
    return cores, model


def _read_ram(proc_base):
    mem = {}
    keys = {"MemTotal", "MemFree", "MemAvailable"}
    with open(f"{proc_base}/meminfo") as f:
        for line in f:
            parts = line.split()
            key = parts[0].rstrip(":")
            if key in keys:
                mem[key] = int(parts[1])
    total_mb = mem.get("MemTotal", 0) / 1024
    avail_mb = mem.get("MemAvailable", 0) / 1024
    used_mb = total_mb - avail_mb
    pct = round(used_mb / total_mb * 100, 1) if total_mb else 0
    return {
        "total_mb": round(total_mb, 1),
        "used_mb": round(used_mb, 1),
        "available_mb": round(avail_mb, 1),
        "percent": pct,
    }


def _read_disk():
    candidates = ["/", "/var", "/opt", "/home", "/data"]
    best = {"total_gb": 0.0, "used_gb": 0.0, "available_gb": 0.0, "percent": 0.0, "mountpoint": "/"}
    for mount in candidates:
        try:
            if hasattr(os, "statvfs"):
                st = os.statvfs(mount)
                total = st.f_blocks * st.f_frsize
                free = st.f_bavail * st.f_frsize
            else:
                # os.statvfs disponível apenas em Unix; shutil.disk_usage como fallback para dev
                usage = shutil.disk_usage(mount)
                total = usage.total
                free = usage.free
            used = total - free
            if total > best["total_gb"] * 1024 ** 3:
                pct = round(used / total * 100, 1) if total else 0
                best = {
                    "total_gb": round(total / 1024 ** 3, 1),
                    "used_gb": round(used / 1024 ** 3, 1),
                    "available_gb": round(free / 1024 ** 3, 1),
                    "percent": pct,
                    "mountpoint": mount,
                }
        except (FileNotFoundError, PermissionError, OSError):
            continue
    return best


def _read_net(proc_base):
    global _prev_net, _prev_time
    now = time.monotonic()
    iface_data = {}
    with open(f"{proc_base}/net/dev") as f:
        for line in f:
            line = line.strip()
            if ":" not in line:
                continue
            iface, rest = line.split(":", 1)
            iface = iface.strip()
            if iface == "lo":
                continue
            vals = rest.split()
            iface_data[iface] = {"rx": int(vals[0]), "tx": int(vals[8])}
    result = {"rx_bytes_s": 0, "tx_bytes_s": 0, "interface": "unknown"}
    if iface_data:
        iface = next(iter(iface_data))
        curr = iface_data[iface]
        if _prev_net and _prev_time:
            elapsed = now - _prev_time
            if elapsed > 0:
                rx_s = int((curr["rx"] - _prev_net.get("rx", curr["rx"])) / elapsed)
                tx_s = int((curr["tx"] - _prev_net.get("tx", curr["tx"])) / elapsed)
                result = {"rx_bytes_s": max(0, rx_s), "tx_bytes_s": max(0, tx_s), "interface": iface}
        _prev_net = curr
        _prev_time = now
    return result


def _read_uptime(proc_base):
    with open(f"{proc_base}/uptime") as f:
        secs = int(float(f.read().split()[0]))
    return {
        "days": secs // 86400,
        "hours": (secs % 86400) // 3600,
        "minutes": (secs % 3600) // 60,
        "seconds": secs,
    }


def _read_temperature(sys_base):
    thermal = Path(f"{sys_base}/class/thermal")
    if not thermal.exists():
        return None
    temps = []
    for zone in thermal.glob("thermal_zone*/temp"):
        try:
            temps.append(int(zone.read_text().strip()) / 1000.0)
        except (ValueError, OSError):
            continue
    return round(max(temps), 1) if temps else None


def collect_host_metrics(proc_base=PROC_BASE, sys_base=SYS_BASE):
    global _prev_cpu
    cpu_raw, load = _read_cpu_raw(proc_base)
    cpu_percent = None
    if _prev_cpu:
        d_idle = cpu_raw["idle"] - _prev_cpu["idle"]
        d_total = cpu_raw["total"] - _prev_cpu["total"]
        if d_total > 0:
            cpu_percent = round(100.0 * (1 - d_idle / d_total), 1)
    _prev_cpu = cpu_raw
    cores, model = _read_cpu_info(proc_base)
    return {
        "cpu": {"percent": cpu_percent, "load": load, "cores": cores, "model": model},
        "ram": _read_ram(proc_base),
        "disk": _read_disk(),
        "net": _read_net(proc_base),
        "uptime": _read_uptime(proc_base),
        "temperature_c": _read_temperature(sys_base),
    }
