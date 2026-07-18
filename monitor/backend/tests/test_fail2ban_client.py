import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_status_all_parseia_lista_de_jails_e_detalhes():
    from collector import fail2ban_client

    status_geral = "Status\n|- Number of jail:\t2\n`- Jail list:\tsshd, vps-monitor-teste\n"
    status_sshd = (
        "Status for the jail: sshd\n"
        "|- Filter\n|  |- Currently failed:\t0\n|  |- Total failed:\t3\n"
        "`- Actions\n   |- Currently banned:\t1\n   |- Total banned:\t2\n"
        "   `- Banned IP list:\t203.0.113.5\n"
    )
    status_vps_monitor = (
        "Status for the jail: vps-monitor-teste\n"
        "|- Filter\n|  |- Currently failed:\t0\n|  |- Total failed:\t0\n"
        "`- Actions\n   |- Currently banned:\t0\n   |- Total banned:\t0\n"
        "   `- Banned IP list:\n"
    )

    async def fake_run(binario, *args):
        if args == ("status",):
            return 0, status_geral, ""
        if args == ("status", "sshd"):
            return 0, status_sshd, ""
        if args == ("status", "vps-monitor-teste"):
            return 0, status_vps_monitor, ""
        raise AssertionError(f"chamada inesperada: {args}")

    with patch.object(fail2ban_client, "_run", AsyncMock(side_effect=fake_run)):
        jails = await fail2ban_client.status_all()

    assert len(jails) == 2
    sshd = next(j for j in jails if j["nome"] == "sshd")
    assert sshd["managed"] is False
    assert sshd["currently_banned"] == 1
    assert sshd["total_banned"] == 2
    assert sshd["banned_ips"] == ["203.0.113.5"]

    vps = next(j for j in jails if j["nome"] == "vps-monitor-teste")
    assert vps["managed"] is True
    assert vps["currently_banned"] == 0
    assert vps["banned_ips"] == []


@pytest.mark.asyncio
async def test_dry_run_regex_bate():
    from collector import fail2ban_client

    saida = (
        "Results\n=======\n\nFailregex: 1 total\n"
        "Ignoreregex: 0 total\n\nLines: 1 lines, 0 ignored, 1 matched, 0 missed\n"
    )
    with patch.object(fail2ban_client, "_run", AsyncMock(return_value=(0, saida, ""))):
        matched, out = await fail2ban_client.dry_run_regex("linha de exemplo", "/tmp/filtro.conf")

    assert matched is True
    assert "1 matched" in out


@pytest.mark.asyncio
async def test_dry_run_regex_nao_bate():
    from collector import fail2ban_client

    saida = "Results\n=======\n\nFailregex: 0 total\n\nLines: 1 lines, 0 ignored, 0 matched, 1 missed\n"
    with patch.object(fail2ban_client, "_run", AsyncMock(return_value=(0, saida, ""))):
        matched, out = await fail2ban_client.dry_run_regex("linha sem match", "/tmp/filtro.conf")

    assert matched is False


@pytest.mark.asyncio
async def test_reload_jail_chama_comando_correto():
    from collector import fail2ban_client

    mock_run = AsyncMock(return_value=(0, "", ""))
    with patch.object(fail2ban_client, "_run", mock_run):
        await fail2ban_client.reload_jail("vps-monitor-teste")

    mock_run.assert_awaited_once_with("fail2ban-client", "reload", "vps-monitor-teste")


@pytest.mark.asyncio
async def test_stop_jail_chama_comando_correto():
    from collector import fail2ban_client

    mock_run = AsyncMock(return_value=(0, "", ""))
    with patch.object(fail2ban_client, "_run", mock_run):
        await fail2ban_client.stop_jail("vps-monitor-teste")

    mock_run.assert_awaited_once_with("fail2ban-client", "stop", "vps-monitor-teste")


@pytest.mark.asyncio
async def test_stop_jail_lanca_excecao_em_falha():
    from collector import fail2ban_client

    mock_run = AsyncMock(return_value=(255, "", "Sorry but the jail 'x' does not exist\n"))
    with patch.object(fail2ban_client, "_run", mock_run):
        with pytest.raises(RuntimeError, match="does not exist"):
            await fail2ban_client.stop_jail("vps-monitor-inexistente")


@pytest.mark.asyncio
async def test_unban_ip_chama_comando_correto():
    from collector import fail2ban_client

    mock_run = AsyncMock(return_value=(0, "", ""))
    with patch.object(fail2ban_client, "_run", mock_run):
        await fail2ban_client.unban_ip("sshd", "203.0.113.5")

    mock_run.assert_awaited_once_with("fail2ban-client", "set", "sshd", "unbanip", "203.0.113.5")


@pytest.mark.asyncio
async def test_unban_ip_lanca_excecao_em_falha():
    from collector import fail2ban_client

    mock_run = AsyncMock(return_value=(255, "", "ERROR NOK\n"))
    with patch.object(fail2ban_client, "_run", mock_run):
        with pytest.raises(RuntimeError, match="NOK"):
            await fail2ban_client.unban_ip("sshd", "203.0.113.5")


@pytest.mark.asyncio
async def test_reload_jail_lanca_excecao_em_falha():
    from collector import fail2ban_client

    mock_run = AsyncMock(return_value=(255, "", "Sorry but the jail 'x' does not exist\n"))
    with patch.object(fail2ban_client, "_run", mock_run):
        with pytest.raises(RuntimeError, match="does not exist"):
            await fail2ban_client.reload_jail("vps-monitor-inexistente")


@pytest.mark.asyncio
async def test_reload_all_chama_comando_sem_nome_de_jail():
    from collector import fail2ban_client

    mock_run = AsyncMock(return_value=(0, "OK", ""))
    with patch.object(fail2ban_client, "_run", mock_run):
        await fail2ban_client.reload_all()

    mock_run.assert_awaited_once_with("fail2ban-client", "reload")


@pytest.mark.asyncio
async def test_reload_all_lanca_excecao_em_falha():
    from collector import fail2ban_client

    mock_run = AsyncMock(return_value=(255, "", "ERROR NOK\n"))
    with patch.object(fail2ban_client, "_run", mock_run):
        with pytest.raises(RuntimeError, match="NOK"):
            await fail2ban_client.reload_all()
