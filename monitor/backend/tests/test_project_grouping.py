from api._project_grouping import agrupar_por_projeto


def test_agrupa_containers_pelo_label_do_projeto():
    containers = [
        {"name": "a", "labels": {"com.docker.compose.project": "mecanicapro"}},
        {"name": "b", "labels": {"com.docker.compose.project": "mecanicapro"}},
        {"name": "c", "labels": {"com.docker.compose.project": "corridas"}},
    ]
    grupos = agrupar_por_projeto(containers)
    assert set(grupos.keys()) == {"mecanicapro", "corridas"}
    assert [c["name"] for c in grupos["mecanicapro"]] == ["a", "b"]
    assert [c["name"] for c in grupos["corridas"]] == ["c"]


def test_container_sem_label_vai_pra_sem_projeto():
    containers = [{"name": "orfao", "labels": {}}]
    grupos = agrupar_por_projeto(containers)
    assert set(grupos.keys()) == {"(sem projeto)"}


def test_container_sem_chave_labels():
    containers = [{"name": "sem-labels"}]
    grupos = agrupar_por_projeto(containers)
    assert set(grupos.keys()) == {"(sem projeto)"}
