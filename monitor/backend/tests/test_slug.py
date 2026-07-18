from api._slug import slugify


def test_slugify_gera_slug_com_prefixo_padrao():
    assert slugify("Teste de Bloqueio") == "vps-monitor-teste-de-bloqueio"


def test_slugify_remove_acentos_e_caracteres_especiais():
    assert slugify("Nóvo Cliénte! (Wildcard)") == "vps-monitor-novo-cliente-wildcard"


def test_slugify_aceita_prefixo_customizado():
    assert slugify("Exemplo", prefix="outro-prefixo-") == "outro-prefixo-exemplo"
