import re
import unicodedata


def slugify(nome_exibicao: str, prefix: str = "vps-monitor-") -> str:
    nfkd = unicodedata.normalize("NFKD", nome_exibicao)
    ascii_str = nfkd.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_str.lower()).strip("-")
    return f"{prefix}{slug}"
