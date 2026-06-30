import pytest
from pathlib import Path

@pytest.fixture
def proc_dir(tmp_path):
    p = tmp_path / "proc"
    p.mkdir()
    (p / "stat").write_text(
        "cpu  100 0 50 850 0 0 0 0 0 0\n"
        "cpu0 50 0 25 425 0 0 0 0 0 0\n"
    )
    (p / "loadavg").write_text("1.50 1.20 0.90 2/100 1234\n")
    (p / "cpuinfo").write_text(
        "processor\t: 0\nmodel name\t: AMD EPYC 7B13\n"
        "processor\t: 1\nmodel name\t: AMD EPYC 7B13\n"
    )
    (p / "meminfo").write_text(
        "MemTotal:       8192000 kB\n"
        "MemFree:        2048000 kB\n"
        "MemAvailable:   4096000 kB\n"
        "Buffers:         512000 kB\n"
        "Cached:         1024000 kB\n"
    )
    (p / "uptime").write_text("443742.12 1234567.89\n")
    net = p / "net"
    net.mkdir()
    (net / "dev").write_text(
        "Inter-|   Receive                                                |  Transmit\n"
        " face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed\n"
        "    lo:    1000       10    0    0    0     0          0         0     1000      10    0    0    0     0       0          0\n"
        "  eth0: 1048576     1000    0    0    0     0          0         0   524288     500    0    0    0     0       0          0\n"
    )
    return str(p)

@pytest.fixture
def sys_dir(tmp_path):
    s = tmp_path / "sys" / "class" / "thermal" / "thermal_zone0"
    s.mkdir(parents=True)
    (s / "temp").write_text("45000\n")
    return str(tmp_path / "sys")

def test_cpu_load(proc_dir, sys_dir):
    import collector.host as h
    h._prev_cpu = None
    h._prev_net = None
    result = h.collect_host_metrics(proc_base=proc_dir, sys_base=sys_dir)
    assert result["cpu"]["load"] == [1.5, 1.2, 0.9]
    assert result["cpu"]["cores"] == 2
    assert result["cpu"]["model"] == "AMD EPYC 7B13"

def test_ram(proc_dir, sys_dir):
    import collector.host as h
    result = h.collect_host_metrics(proc_base=proc_dir, sys_base=sys_dir)
    assert result["ram"]["total_mb"] == pytest.approx(8000.0, abs=1)
    assert result["ram"]["available_mb"] == pytest.approx(4000.0, abs=1)
    assert 0 < result["ram"]["percent"] < 100

def test_uptime(proc_dir, sys_dir):
    import collector.host as h
    result = h.collect_host_metrics(proc_base=proc_dir, sys_base=sys_dir)
    assert result["uptime"]["days"] == 5
    assert result["uptime"]["hours"] == 3

def test_temperature(proc_dir, sys_dir):
    import collector.host as h
    result = h.collect_host_metrics(proc_base=proc_dir, sys_base=sys_dir)
    assert result["temperature_c"] == 45.0

def test_temperature_ausente(proc_dir, tmp_path):
    import collector.host as h
    sys_empty = str(tmp_path / "sys_empty")
    Path(sys_empty).mkdir()
    result = h.collect_host_metrics(proc_base=proc_dir, sys_base=sys_empty)
    assert result["temperature_c"] is None

def test_cpu_percent_segunda_leitura(proc_dir, sys_dir):
    import collector.host as h
    h._prev_cpu = None
    h._prev_net = None
    h.collect_host_metrics(proc_base=proc_dir, sys_base=sys_dir)
    stat = Path(proc_dir) / "stat"
    stat.write_text("cpu  200 0 100 1500 0 0 0 0 0 0\ncpu0 100 0 50 750 0 0 0 0 0 0\n")
    result = h.collect_host_metrics(proc_base=proc_dir, sys_base=sys_dir)
    assert result["cpu"]["percent"] is not None
    assert 0 <= result["cpu"]["percent"] <= 100

def test_disk(proc_dir, sys_dir):
    import collector.host as h
    h._prev_cpu = None
    h._prev_net = None
    result = h.collect_host_metrics(proc_base=proc_dir, sys_base=sys_dir)
    disk = result["disk"]
    assert "total_gb" in disk
    assert "used_gb" in disk
    assert "available_gb" in disk
    assert "percent" in disk
    assert disk["total_gb"] >= 0
    assert 0 <= disk["percent"] <= 100
