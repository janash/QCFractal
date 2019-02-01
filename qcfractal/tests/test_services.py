"""
Tests the on-node procedures compute capabilities.
"""

import copy

import pytest

import qcfractal.interface as portal
from qcfractal.testing import fractal_compute_server, recursive_dict_merge


@pytest.fixture(scope="module")
def torsiondrive_fixture(fractal_compute_server):

    # Cannot use this fixture without these services. Also cannot use `mark` and `fixture` decorators
    pytest.importorskip("torsiondrive")
    pytest.importorskip("geometric")
    pytest.importorskip("rdkit")

    client = portal.FractalClient(fractal_compute_server)

    # Add a HOOH
    hooh = portal.data.get_molecule("hooh.json")
    mol_ret = client.add_molecules({"hooh": hooh})
    default_grid_spacing = 90

    # Geometric options
    torsiondrive_options = {
        "torsiondrive_meta": {
            "dihedrals": [[0, 1, 2, 3]],
            "grid_spacing": [default_grid_spacing]
        },
        "optimization_meta": {
            "program": "geometric",
            "coordsys": "tric",
        },
        "qc_meta": {
            "driver": "gradient",
            "method": "UFF",
            "basis": "",
            "options": None,
            "program": "rdkit",
        },
    }

    def spin_up_test(grid_spacing=None, **keyword_augments):
        if grid_spacing is None:
            grid_spacing = default_grid_spacing

        instance_options = copy.deepcopy(torsiondrive_options)
        instance_options["torsiondrive_meta"]["grid_spacing"] = [grid_spacing]

        # instance_options = {**instance_options, **keyword_augments}
        recursive_dict_merge(instance_options, keyword_augments)
        ret = client.add_service("torsiondrive", [mol_ret["hooh"]], instance_options)
        fractal_compute_server.await_services()
        assert len(fractal_compute_server.list_current_tasks()) == 0
        return ret

    yield spin_up_test, client


def test_service_torsiondrive_single(torsiondrive_fixture):
    """"Tests torsiondrive pathway and checks the result result"""

    spin_up_test, client = torsiondrive_fixture

    ret = spin_up_test()
    compute_key = ret["submitted"][0]

    # Get a TorsionDriveORM result and check data
    result = client.get_procedures({"procedure": "torsiondrive"})[0]
    assert isinstance(str(result), str)  # Check that repr runs

    assert pytest.approx(0.002597541340221565, 1e-5) == result.final_energies(0)
    assert pytest.approx(0.000156553761859276, 1e-5) == result.final_energies(90)
    assert pytest.approx(0.000156553761859271, 1e-5) == result.final_energies(-90)
    assert pytest.approx(0.000753492556057886, 1e-5) == result.final_energies(180)

    assert hasattr(result.final_molecules()[(-90, )], "symbols")


def test_service_torsiondrive_duplicates(torsiondrive_fixture):
    """Ensure that duplicates are properly caught and yield the same results without calculation"""

    spin_up_test, client = torsiondrive_fixture

    # Run the test without modifications
    _ = spin_up_test()

    # Augment the input for torsion drive to yield a new hash procedure hash,
    # but not a new task set
    _ = spin_up_test(torsiondrive_meta={"meaningless_entry_to_change_hash": "Waffles!"})
    procedures = client.get_procedures({"procedure": "torsiondrive"})
    assert len(procedures) == 2  # Make sure only 2 procedures are yielded
    base_run, duplicate_run = procedures
    assert base_run._optimization_history == duplicate_run._optimization_history


def test_service_iterate_error(torsiondrive_fixture):
    """Ensure errors are caught and logged when iterating serivces"""

    spin_up_test, client = torsiondrive_fixture

    # Run the test without modifications
    ret = spin_up_test(grid_spacing="waffles_crasher")

    status = client.check_services({"hash_index": ret["submitted"][0]})
    assert len(status) == 1

    assert status[0]["status"] == "ERROR"
    assert "Service Build" in status[0]["error_message"]


def test_service_torsiondrive_compute_error(torsiondrive_fixture):
    """Ensure errors are caught and logged when iterating serivces"""

    spin_up_test, client = torsiondrive_fixture

    # Run the test without modifications
    ret = spin_up_test(qc_meta={"method": "waffles_crasher"})

    status = client.check_services({"hash_index": ret["submitted"][0]})
    assert len(status) == 1

    assert status[0]["status"] == "ERROR"
    assert "All tasks" in status[0]["error_message"]