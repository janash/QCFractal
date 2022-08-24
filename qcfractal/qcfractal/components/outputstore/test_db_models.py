from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from qcfractal.components.outputstore.db_models import OutputStoreORM
from qcfractal.components.records.db_models import RecordComputeHistoryORM
from qcfractal.components.records.singlepoint.testing_helpers import submit_test_data
from qcportal.compression import CompressionEnum
from qcportal.outputstore import OutputTypeEnum, OutputStore
from qcportal.record_models import RecordStatusEnum

if TYPE_CHECKING:
    from qcfractal.db_socket import SQLAlchemySocket


@pytest.fixture()
def existing_history_id(storage_socket):
    """
    Build a singlepoint calculation

    Needed for adding entries to the output store, which require a relationship
    with an existing calculation
    """

    record_id, _ = submit_test_data(storage_socket, "sp_psi4_benzene_energy_1")

    hist = RecordComputeHistoryORM(
        record_id=record_id,
        status=RecordStatusEnum.error,
        manager_name=None,
    )

    with storage_socket.session_scope() as session:
        session.add(hist)
        session.commit()
        hist_id = hist.id

    yield hist_id


@pytest.mark.parametrize("compression", CompressionEnum)
@pytest.mark.parametrize("compression_level", [None, 1, 5])
@pytest.mark.parametrize("output_type", OutputTypeEnum)
def test_outputs_models_roundtrip_str(
    storage_socket: SQLAlchemySocket, existing_history_id, compression, compression_level, output_type
):
    """
    Tests storing/retrieving plain string data in OutputStore
    """

    input_str = "This is some input " * 20
    output = OutputStore.compress(output_type, input_str, compression, compression_level)

    # Add both as the OutputStore and as the plain str
    out_orm = OutputStoreORM.from_model(output)
    out_orm.history_id = existing_history_id

    with storage_socket.session_scope() as session:
        session.add(out_orm)
        session.flush()
        out_id = out_orm.id

    # Retrieve again
    with storage_socket.session_scope() as session:
        stored_orm = session.query(OutputStoreORM).where(OutputStoreORM.id == out_id).one()
        out_model = stored_orm.to_model(OutputStore)

    assert out_model.compression == compression
    assert out_model.as_string == input_str

    # if compression_level is None (and compression is requested),
    # then a sensible default is used
    if compression_level is not None and compression is not CompressionEnum.none:
        assert out_model.compression_level == compression_level


@pytest.mark.parametrize("compression", CompressionEnum)
@pytest.mark.parametrize("compression_level", [None, 1, 5])
@pytest.mark.parametrize("output_type", OutputTypeEnum)
def test_outputs_models_roundtrip_dict(
    storage_socket: SQLAlchemySocket, existing_history_id, compression, compression_level, output_type
):
    """
    Tests storing/retrieving dict/json data in OutputStore
    """

    input_dict = {str(k): "This is some input " * k for k in range(5)}
    output = OutputStore.compress(output_type, input_dict, compression, compression_level)

    # Add both as the OutputStore and as the plain str
    out_orm = OutputStoreORM.from_model(output)
    out_orm.history_id = existing_history_id

    with storage_socket.session_scope() as session:
        session.add(out_orm)
        session.flush()
        out_id = out_orm.id

    # Retrieve again
    with storage_socket.session_scope() as session:
        stored_orm = session.query(OutputStoreORM).where(OutputStoreORM.id == out_id).one()
        out_model = stored_orm.to_model(OutputStore)

    assert out_model.compression == compression
    assert out_model.as_json == input_dict
    assert out_model.output_type == output_type

    # if compression_level is None (and compression is requested),
    # then a sensible default is used
    if compression_level is not None and compression is not CompressionEnum.none:
        assert out_model.compression_level == compression_level


@pytest.mark.parametrize("compression", CompressionEnum)
@pytest.mark.parametrize("compression_level", [None, 1, 5])
@pytest.mark.parametrize("output_type", OutputTypeEnum)
def test_outputs_models_append(
    storage_socket: SQLAlchemySocket, existing_history_id, compression, compression_level, output_type
):
    """
    Tests appending data to a stored string
    """

    input_str = "This is some input " * 20
    output = OutputStore.compress(output_type, input_str, compression, compression_level)

    # Add both as the OutputStore and as the plain str
    out_orm = OutputStoreORM.from_model(output)
    out_orm.history_id = existing_history_id

    with storage_socket.session_scope() as session:
        session.add(out_orm)
        session.flush()
        out_id = out_orm.id

    # Retrieve again and append a new string
    with storage_socket.session_scope() as session:
        stored_orm = session.query(OutputStoreORM).where(OutputStoreORM.id == out_id).one()
        stored_orm.append("Appended string")

    # Retrieve again
    with storage_socket.session_scope() as session:
        stored_orm = session.query(OutputStoreORM).where(OutputStoreORM.id == out_id).one()
        out_model = stored_orm.to_model(OutputStore)

    # Didn't change compression
    assert out_model.compression == compression
    assert out_model.output_type == output_type

    assert out_model.as_string == input_str + "Appended string"