import asyncio
import httpx
from typing import Optional


def calculate_cpu_percent(stats: dict) -> float:
    try:
        cpu_delta = (
            stats["cpu_stats"]["cpu_usage"]["total_usage"]
            - stats["precpu_stats"]["cpu_usage"]["total_usage"]
        )
        sys_delta = (
            stats["cpu_stats"]["system_cpu_usage"]
            - stats["precpu_stats"]["system_cpu_usage"]
        )
        ncpus = stats["cpu_stats"].get("online_cpus") or len(
            stats["cpu_stats"]["cpu_usage"].get("percpu_usage", [1])
        )
        if sys_delta > 0 and cpu_delta >= 0:
            return round((cpu_delta / sys_delta) * ncpus * 100.0, 2)
    except (KeyError, ZeroDivisionError, TypeError):
        pass
    return 0.0


class DockerClient:
    def __init__(self, socket_path: str = "/var/run/docker.sock"):
        self._socket = socket_path

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(uds=self._socket),
            base_url="http://localhost",
            timeout=10.0,
        )

    async def list_containers(self) -> list[dict]:
        async with self._client() as c:
            r = await c.get("/containers/json", params={"all": True})
            r.raise_for_status()
            return r.json()

    async def container_stats(self, container_id: str) -> Optional[dict]:
        try:
            async with self._client() as c:
                r = await c.get(
                    f"/containers/{container_id}/stats",
                    params={"stream": "false"},
                    timeout=5.0,
                )
                r.raise_for_status()
                stats = r.json()

            cpu_percent = calculate_cpu_percent(stats)

            ms = stats.get("memory_stats", {})
            cache = ms.get("stats", {}).get("cache", 0)
            raw_used = ms.get("usage", 0) - cache
            lim = ms.get("limit", 1) or 1
            mem_usage_mb = round(raw_used / 1024 ** 2, 1)
            mem_limit_mb = round(lim / 1024 ** 2, 1)
            mem_percent = round(raw_used / lim * 100, 1)

            net_rx_mb = 0.0
            net_tx_mb = 0.0
            for iface in stats.get("networks", {}).values():
                net_rx_mb += iface.get("rx_bytes", 0) / (1024 * 1024)
                net_tx_mb += iface.get("tx_bytes", 0) / (1024 * 1024)

            block_read_mb = 0.0
            block_write_mb = 0.0
            blkio = stats.get("blkio_stats", {})
            io_service = blkio.get("io_service_bytes_recursive") or []
            for entry in io_service:
                op = entry.get("op", "").lower()
                value = entry.get("value", 0)
                if op == "read":
                    block_read_mb += value / (1024 * 1024)
                elif op == "write":
                    block_write_mb += value / (1024 * 1024)

            return {
                "cpu_percent": cpu_percent,
                "mem_usage_mb": mem_usage_mb,
                "mem_limit_mb": mem_limit_mb,
                "mem_percent": mem_percent,
                "net_rx_mb": net_rx_mb,
                "net_tx_mb": net_tx_mb,
                "block_read_mb": block_read_mb,
                "block_write_mb": block_write_mb,
            }
        except Exception:
            return None

    async def container_inspect(self, container_id: str) -> dict:
        async with self._client() as c:
            r = await c.get(f"/containers/{container_id}/json")
            r.raise_for_status()
            return r.json()

    async def _post_action(self, container_id: str, action: str, params: Optional[dict] = None) -> None:
        async with self._client() as c:
            r = await c.post(f"/containers/{container_id}/{action}", params=params or {})
            if r.status_code == 304:
                return
            r.raise_for_status()

    async def start_container(self, container_id: str) -> None:
        await self._post_action(container_id, "start")

    async def stop_container(self, container_id: str, timeout: int = 10) -> None:
        await self._post_action(container_id, "stop", {"t": timeout})

    async def restart_container(self, container_id: str, timeout: int = 10) -> None:
        await self._post_action(container_id, "restart", {"t": timeout})

    async def list_containers_with_size(self) -> list[dict]:
        async with self._client() as c:
            r = await c.get("/containers/json", params={"all": True, "size": True})
            r.raise_for_status()
            return r.json()

    async def get_logs(self, container_id: str, tail: int = 50) -> list[str]:
        try:
            async with self._client() as c:
                r = await c.get(
                    f"/containers/{container_id}/logs",
                    params={"tail": tail, "stdout": True, "stderr": True, "timestamps": True},
                    timeout=5.0,
                )
                r.raise_for_status()
                raw = r.content
                lines: list[str] = []
                i = 0
                while i + 8 <= len(raw):
                    size = int.from_bytes(raw[i + 4:i + 8], "big")
                    msg = raw[i + 8:i + 8 + size].decode("utf-8", errors="replace").rstrip("\n")
                    if msg:
                        lines.append(msg)
                    i += 8 + size
                return lines[-tail:]
        except Exception:
            return []

    async def collect_all(self) -> list[dict]:
        containers = await self.list_containers()
        if not containers:
            return []

        stats_list = await asyncio.gather(
            *[self.container_stats(c["Id"]) for c in containers]
        )

        result = []
        for container, stats in zip(containers, stats_list):
            name = (container["Names"][0].lstrip("/") if container["Names"]
                    else container["Id"][:12])

            cpu_pct = mem_usage = mem_limit = mem_pct = 0.0
            net_rx = net_tx = block_read = block_write = 0.0

            if stats:
                cpu_pct = stats.get("cpu_percent", 0.0)
                mem_usage = stats.get("mem_usage_mb", 0.0)
                mem_limit = stats.get("mem_limit_mb", 0.0)
                mem_pct = stats.get("mem_percent", 0.0)
                net_rx = stats.get("net_rx_mb", 0.0)
                net_tx = stats.get("net_tx_mb", 0.0)
                block_read = stats.get("block_read_mb", 0.0)
                block_write = stats.get("block_write_mb", 0.0)

            result.append({
                "id": container["Id"][:12],
                "id_full": container["Id"],
                "name": name,
                "image": container.get("Image", ""),
                "status": container.get("State", "unknown"),
                "status_text": container.get("Status", ""),
                "cpu_percent": cpu_pct,
                "mem_usage_mb": mem_usage,
                "mem_limit_mb": mem_limit,
                "mem_percent": mem_pct,
                "net_rx_mb": net_rx,
                "net_tx_mb": net_tx,
                "block_read_mb": block_read,
                "block_write_mb": block_write,
                "restart_count": container.get("HostConfig", {}).get("RestartCount", 0),
            })
        return result
