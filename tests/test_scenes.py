from realtalk.scenes import ROLES, SCENES, Role, Scene


def test_scenes_module_loads() -> None:
    assert len(SCENES) == 6
    assert len(ROLES) == 5
    assert all(isinstance(scene, Scene) for scene in SCENES)
    assert all(isinstance(role, Role) for role in ROLES)


def test_scene_ids_unique() -> None:
    assert len({scene.id for scene in SCENES}) == len(SCENES)
    assert len({role.id for role in ROLES}) == len(ROLES)
