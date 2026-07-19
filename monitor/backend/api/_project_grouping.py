def agrupar_por_projeto(containers: list[dict]) -> dict[str, list[dict]]:
    grupos: dict[str, list[dict]] = {}
    for c in containers:
        projeto = (c.get("labels") or {}).get("com.docker.compose.project", "(sem projeto)")
        grupos.setdefault(projeto, []).append(c)
    return grupos
