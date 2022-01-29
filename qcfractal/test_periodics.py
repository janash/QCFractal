from __future__ import annotations

import time
from datetime import datetime
from typing import TYPE_CHECKING

from qcfractaltesting.helpers import load_procedure_data
from qcportal.managers import ManagerName, ManagerStatusEnum
from qcportal.records import RecordStatusEnum

if TYPE_CHECKING:
    from qcfractal.testing_helpers import TestingSnowflake, SQLAlchemySocket


def test_periodics_server_stats(snowflake: TestingSnowflake, storage_socket: SQLAlchemySocket):

    meta, stats = storage_socket.serverinfo.query_server_stats()
    assert meta.n_found == 0

    sleep_time = snowflake._qcf_config.statistics_frequency

    snowflake.start_periodics()

    for i in range(5):
        time_0 = datetime.utcnow()
        time.sleep(sleep_time)
        time_1 = datetime.utcnow()

        meta, stats = storage_socket.serverinfo.query_server_stats()
        assert meta.n_found == i + 1
        assert time_0 < stats[0]["timestamp"] < time_1


def test_periodics_manager_heartbeats(snowflake: TestingSnowflake, storage_socket: SQLAlchemySocket):

    heartbeat = snowflake._qcf_config.heartbeat_frequency
    max_missed = snowflake._qcf_config.heartbeat_max_missed

    mname1 = ManagerName(cluster="test_cluster", hostname="a_host", uuid="1234-5678-1234-5678")
    storage_socket.managers.activate(
        name_data=mname1,
        manager_version="v2.0",
        qcengine_version="v1.0",
        username="bill",
        programs={"psi4": None, "qchem": "v3.0"},
        tags=["tag1"],
    )

    snowflake.start_periodics()

    for i in range(max_missed + 1):
        time.sleep(heartbeat)
        manager = storage_socket.managers.get([mname1.fullname])

        if i < max_missed:
            assert manager[0]["status"] == ManagerStatusEnum.active
        else:
            assert manager[0]["status"] == ManagerStatusEnum.inactive


def test_periodics_service_iteration(snowflake: TestingSnowflake, storage_socket: SQLAlchemySocket):

    input_spec_1, molecules_1, result_data_1 = load_procedure_data("td_H2O2_psi4_b3lyp")
    input_spec_2, molecules_2, result_data_2 = load_procedure_data("td_H2O2_psi4_pbe")

    service_freq = snowflake._qcf_config.service_frequency

    meta_1, id_1 = storage_socket.records.torsiondrive.add([molecules_1], input_spec_1, as_service=True)

    rec = storage_socket.records.get(id_1)
    assert rec[0]["status"] == RecordStatusEnum.waiting

    snowflake.start_periodics()

    time.sleep(1.0)

    # added after startup
    meta_2, id_2 = storage_socket.records.torsiondrive.add([molecules_2], input_spec_2, as_service=True)

    # The first services iterated at startup
    rec = storage_socket.records.get(id_1 + id_2)
    assert rec[0]["status"] == RecordStatusEnum.running
    assert rec[1]["status"] == RecordStatusEnum.waiting

    # wait for the next iteration. Then both should be running
    time.sleep(service_freq + 0.5)
    rec = storage_socket.records.get(id_1 + id_2)
    assert rec[0]["status"] == RecordStatusEnum.running
    assert rec[1]["status"] == RecordStatusEnum.running